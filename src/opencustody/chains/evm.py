"""Ethereum and EVM-compatible chains (EIP-1559 transactions, ERC-20 tokens)."""

from __future__ import annotations

from eth_account import Account
from eth_utils import is_address, is_checksum_address, to_checksum_address

import urllib.parse

from .. import net
from ..net import JsonRpc
from .base import Asset, Chain, HistoryItem, HistoryUnavailable, InsufficientFunds, PreparedTx

# Free Etherscan-compatible APIs (Blockscout), used if config.json predates "history_api".
# (Base/Arbitrum/Polygon Blockscout instances block non-browser clients.)
DEFAULT_HISTORY_API = {"eth": "https://eth.blockscout.com/api", "op": "https://optimism.blockscout.com/api"}

TRANSFER_SELECTOR = "a9059cbb"  # transfer(address,uint256)
BALANCEOF_SELECTOR = "70a08231"  # balanceOf(address)
DECIMALS_SELECTOR = "313ce567"  # decimals()
ZERO = "0x" + "00" * 20


def encode_transfer(to: str, amount: int) -> str:
    return "0x" + TRANSFER_SELECTOR + to[2:].lower().rjust(64, "0") + format(amount, "064x")


def validate_evm_address(addr: str) -> str | None:
    if not is_address(addr):
        return "Not a valid EVM address."
    has_upper = any(c.isupper() for c in addr[2:])
    has_lower = any(c.islower() for c in addr[2:])
    if has_upper and has_lower and not is_checksum_address(addr):
        return "Address checksum is wrong (possible typo)."
    if addr.lower() == ZERO:
        return "Refusing to send to the zero address."
    return None


class EvmChain(Chain):
    def __init__(self, cfg: dict, keyring):
        self.cfg = cfg
        self.key = cfg["key"]
        self.name = cfg["name"]
        self.chain_id = int(cfg["chain_id"])
        self.native = Asset(cfg["symbol"], 18)
        self.explorer_tx = cfg["explorer"] + "/tx/{}"
        self.explorer_addr = cfg["explorer"] + "/address/{}"
        self.rpc = JsonRpc(cfg["rpc"])
        self._priv = keyring.evm_private_key()
        self._addr = to_checksum_address(keyring.evm_address())
        self._tokens: list[Asset] | None = None

    def address(self) -> str:
        return self._addr

    def _check_chain_id(self) -> None:
        got = int(self.rpc.call("eth_chainId"), 16)
        if got != self.chain_id:
            raise RuntimeError(f"RPC endpoint is for chain {got}, expected {self.chain_id}. Check your config.")

    def assets(self) -> list[Asset]:
        if self._tokens is None:
            toks = []
            for t in self.cfg.get("tokens", []):
                if validate_evm_address(t["address"]) is None:
                    toks.append(Asset(t["symbol"], -1, to_checksum_address(t["address"])))
            self._tokens = toks
        return [self.native] + self._tokens

    def _token_decimals(self, asset: Asset) -> int:
        if asset.decimals < 0:
            res = self.rpc.call("eth_call", [{"to": asset.contract, "data": "0x" + DECIMALS_SELECTOR}, "latest"])
            d = int(res, 16)
            if not 0 <= d <= 36:
                raise RuntimeError(f"Token {asset.symbol} reports odd decimals: {d}")
            asset.decimals = d
        return asset.decimals

    def balance(self, asset: Asset) -> int:
        if asset.contract is None:
            return int(self.rpc.call("eth_getBalance", [self._addr, "latest"]), 16)
        self._token_decimals(asset)
        data = "0x" + BALANCEOF_SELECTOR + self._addr[2:].lower().rjust(64, "0")
        res = self.rpc.call("eth_call", [{"to": asset.contract, "data": data}, "latest"])
        return int(res, 16) if res and res != "0x" else 0

    def validate_address(self, addr: str) -> str | None:
        return validate_evm_address(addr)

    def _fees(self) -> tuple[int, int]:
        block = self.rpc.call("eth_getBlockByNumber", ["latest", False])
        base = int(block.get("baseFeePerGas", "0x0"), 16)
        try:
            prio = int(self.rpc.call("eth_maxPriorityFeePerGas"), 16)
        except Exception:
            prio = 1_500_000_000
        return base * 2 + prio, prio

    def prepare_send(self, asset: Asset, to: str, amount: int | None) -> PreparedTx:
        err = self.validate_address(to)
        if err:
            raise ValueError(err)
        if amount is None or amount <= 0:
            raise ValueError("Enter an amount.")
        to = to_checksum_address(to)
        self._check_chain_id()
        nonce = int(self.rpc.call("eth_getTransactionCount", [self._addr, "pending"]), 16)
        max_fee, prio = self._fees()
        warnings = []

        if asset.contract is None:
            call = {"from": self._addr, "to": to, "value": hex(amount)}
            tx_to, value, data = to, amount, "0x"
        else:
            self._token_decimals(asset)
            if to.lower() == asset.contract.lower():
                raise ValueError("Recipient is the token contract itself; tokens sent there are usually lost.")
            if self.balance(asset) < amount:
                raise InsufficientFunds(f"Not enough {asset.symbol}.")
            data = encode_transfer(to, amount)
            call = {"from": self._addr, "to": asset.contract, "data": data}
            tx_to, value = asset.contract, 0

        gas_est = int(self.rpc.call("eth_estimateGas", [call]), 16)
        gas = max(21000, gas_est if gas_est == 21000 else gas_est * 12 // 10)
        if asset.contract is None and gas > 21000:
            warnings.append("Recipient is a smart contract.")
        fee = gas * max_fee
        bal = self.balance(self.native)
        if bal < value + fee:
            raise InsufficientFunds(f"Not enough {self.native.symbol} for amount plus network fee.")

        tx = {
            "type": 2, "chainId": self.chain_id, "nonce": nonce, "to": tx_to, "value": value,
            "data": data, "gas": gas, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": prio,
        }
        signed = Account.sign_transaction(tx, self._priv)
        raw = "0x" + bytes(signed.raw_transaction).hex()

        def send() -> str:
            return self.rpc.call("eth_sendRawTransaction", [raw])

        return PreparedTx(self.name, asset, self._addr, to, amount, fee, "(maximum; usually less)",
                          self.native, send, warnings)

    def history(self, asset: Asset, limit: int = 25) -> list[HistoryItem]:
        try:
            return self._history(asset, limit)
        except net.NetError:
            raise HistoryUnavailable(f"{self.name} history service didn't respond; try again or use the explorer.") from None

    def _history(self, asset: Asset, limit: int) -> list[HistoryItem]:
        params = {"module": "account", "address": self._addr, "page": 1, "offset": limit, "sort": "desc"}
        key = self.cfg.get("etherscan_api_key") or ""
        if key:
            api = "https://api.etherscan.io/v2/api"
            params.update(chainid=self.chain_id, apikey=key)
        else:
            api = self.cfg.get("history_api", DEFAULT_HISTORY_API.get(self.key, ""))
        if not api:
            raise HistoryUnavailable(f"History for {self.name} needs a free Etherscan API key "
                                     "(etherscan_api_key in Settings → config).")
        if asset.contract is None:
            params["action"] = "txlist"
        else:
            params.update(action="tokentx", contractaddress=asset.contract)
            self._token_decimals(asset)
        r = net.get_json(f"{api}?{urllib.parse.urlencode(params)}")
        rows = r.get("result")
        if not isinstance(rows, list):
            if str(r.get("message", "")).lower().startswith("no transactions"):
                return []
            raise net.NetError(f"History API: {r.get('message') or r.get('result')}")
        me = self._addr.lower()
        out = []
        for t in rows:
            value = int(t.get("value") or 0)
            if asset.contract is None and value == 0:
                continue  # contract calls without ETH (e.g. token transfers) live in the token's history
            frm, to = (t.get("from") or "").lower(), (t.get("to") or "").lower()
            direction = "self" if frm == to == me else "out" if frm == me else "in"
            failed = t.get("isError") == "1" or t.get("txreceipt_status") == "0"
            out.append(HistoryItem(t["hash"], int(t.get("timeStamp") or 0) or None, direction, value,
                                   t.get("from") if direction == "in" else t.get("to") or "",
                                   "failed" if failed else "confirmed"))
        return out
