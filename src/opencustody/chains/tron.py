"""Tron: TRX and TRC-20 transfers.

Transactions are encoded locally (hand-written protobuf for the two contract
types we use) so a malicious API node cannot swap the recipient or amount:
we never sign anything the node built for us.
"""

from __future__ import annotations

import hashlib
import time

from eth_keys import keys as eth_keys
from embit import base58

from .. import net
from .base import Asset, Chain, InsufficientFunds, PreparedTx

TRANSFER_CONTRACT = 1
TRIGGER_SMART_CONTRACT = 31
TYPE_URLS = {
    TRANSFER_CONTRACT: "type.googleapis.com/protocol.TransferContract",
    TRIGGER_SMART_CONTRACT: "type.googleapis.com/protocol.TriggerSmartContract",
}
SUN = 1_000_000


# ---- protobuf encoding --------------------------------------------------------
def _varint(n: int) -> bytes:
    if n < 0:
        raise ValueError("negative varint")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _f_varint(field: int, n: int) -> bytes:
    return _varint(field << 3) + _varint(n) if n else b""


def _f_bytes(field: int, b: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(b)) + b if b else b""


def address_to_bytes(addr: str) -> bytes:
    raw = base58.decode_check(addr)
    if len(raw) != 21 or raw[0] != 0x41:
        raise ValueError("Not a Tron address.")
    return raw


def bytes_to_address(raw: bytes) -> str:
    return base58.encode_check(raw)


def encode_contract(ctype: int, value: bytes) -> bytes:
    any_msg = _f_bytes(1, TYPE_URLS[ctype].encode()) + _f_bytes(2, value)
    return _f_varint(1, ctype) + _f_bytes(2, any_msg)


def encode_raw(ref_block_bytes: bytes, ref_block_hash: bytes, expiration: int, contract: bytes,
               timestamp: int, fee_limit: int = 0) -> bytes:
    return (_f_bytes(1, ref_block_bytes) + _f_bytes(4, ref_block_hash) + _f_varint(8, expiration)
            + _f_bytes(11, contract) + _f_varint(14, timestamp) + _f_varint(18, fee_limit))


def transfer_value(owner: bytes, to: bytes, amount: int) -> bytes:
    return _f_bytes(1, owner) + _f_bytes(2, to) + _f_varint(3, amount)


def trigger_value(owner: bytes, contract: bytes, data: bytes) -> bytes:
    return _f_bytes(1, owner) + _f_bytes(2, contract) + _f_bytes(4, data)


def trc20_transfer_data(to: bytes, amount: int) -> bytes:
    return bytes.fromhex("a9059cbb") + to[1:].rjust(32, b"\0") + amount.to_bytes(32, "big")


def sign_raw(raw: bytes, priv: bytes) -> tuple[str, bytes]:
    txid = hashlib.sha256(raw).digest()
    sig = eth_keys.PrivateKey(priv).sign_msg_hash(txid).to_bytes()
    signed = _f_bytes(1, raw) + _f_bytes(2, sig)
    return txid.hex(), signed


class TronChain(Chain):
    key = "trx"
    name = "Tron"

    def __init__(self, cfg: dict, keyring):
        self.cfg = cfg
        self.native = Asset("TRX", 6)
        self.api = cfg["api"].rstrip("/")
        self.explorer_tx = cfg["explorer"] + "/transaction/{}"
        self.explorer_addr = cfg["explorer"] + "/address/{}"
        self._priv = keyring.tron_private_key()
        self._addr = keyring.tron_address()
        self._owner = address_to_bytes(self._addr)
        self._tokens = [Asset(t["symbol"], -1, t["address"]) for t in cfg.get("tokens", [])]

    def address(self) -> str:
        return self._addr

    def assets(self) -> list[Asset]:
        return [self.native] + self._tokens

    def _post(self, path: str, payload: dict):
        return net.post_json(self.api + path, payload)

    def _const_call(self, contract: str, selector: str, param_hex: str = "") -> int:
        r = self._post("/wallet/triggerconstantcontract", {
            "owner_address": self._addr, "contract_address": contract,
            "function_selector": selector, "parameter": param_hex, "visible": True})
        res = (r.get("constant_result") or ["0"])[0]
        return int(res or "0", 16)

    def _decimals(self, asset: Asset) -> int:
        if asset.decimals < 0:
            d = self._const_call(asset.contract, "decimals()")
            if not 0 <= d <= 36:
                raise RuntimeError("Token reports odd decimals.")
            asset.decimals = d
        return asset.decimals

    def balance(self, asset: Asset) -> int:
        if asset.contract is None:
            return int(self._post("/wallet/getaccount", {"address": self._addr, "visible": True}).get("balance", 0))
        self._decimals(asset)
        return self._const_call(asset.contract, "balanceOf(address)", self._owner[1:].rjust(32, b"\0").hex())

    def validate_address(self, addr: str) -> str | None:
        try:
            address_to_bytes(addr.strip())
            return None
        except Exception:
            return "Not a valid Tron address (should start with T)."

    def prepare_send(self, asset: Asset, to: str, amount: int | None) -> PreparedTx:
        to = to.strip()
        err = self.validate_address(to)
        if err:
            raise ValueError(err)
        if not amount or amount <= 0:
            raise ValueError("Enter an amount.")
        to_b = address_to_bytes(to)
        blk = self._post("/wallet/getnowblock", {})
        num = int(blk["block_header"]["raw_data"]["number"])
        block_id = bytes.fromhex(blk["blockID"])
        if int.from_bytes(block_id[:8], "big") != num:
            raise RuntimeError("Inconsistent block data from Tron API.")
        now = int(time.time() * 1000)
        warnings = []
        trx_bal = self.balance(self.native)
        to_acct = self._post("/wallet/getaccount", {"address": to, "visible": True})
        if not to_acct:
            warnings.append("Recipient account is not activated yet (costs about 1.1 TRX extra).")

        if asset.contract is None:
            fee_limit = 0
            contract = encode_contract(TRANSFER_CONTRACT, transfer_value(self._owner, to_b, amount))
            fee, note = 1_100_000 if not to_acct else 300_000, "(estimate; may be free with bandwidth)"
            if trx_bal < amount:
                raise InsufficientFunds("Not enough TRX.")
        else:
            self._decimals(asset)
            if self.balance(asset) < amount:
                raise InsufficientFunds(f"Not enough {asset.symbol}.")
            fee_limit = int(self.cfg.get("trc20_fee_limit_trx", 50)) * SUN
            contract = encode_contract(
                TRIGGER_SMART_CONTRACT,
                trigger_value(self._owner, address_to_bytes(asset.contract), trc20_transfer_data(to_b, amount)))
            fee, note = fee_limit, "(maximum energy burn; usually much less)"
            if trx_bal < 5 * SUN:
                warnings.append("Low TRX balance: TRC-20 transfers burn TRX for energy and may fail.")

        raw = encode_raw(block_id[6:8], block_id[8:16], now + 60_000, contract, now, fee_limit)
        txid, signed = sign_raw(raw, self._priv)

        def send() -> str:
            r = self._post("/wallet/broadcasthex", {"transaction": signed.hex()})
            if not r.get("result"):
                msg = r.get("message", "")
                try:
                    msg = bytes.fromhex(msg).decode("utf-8", "replace")
                except ValueError:
                    pass
                raise net.NetError(f"Broadcast rejected: {r.get('code', '')} {msg}")
            return txid

        return PreparedTx(self.name, asset, self._addr, to, amount, fee, note, self.native, send, warnings)
