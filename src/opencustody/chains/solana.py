"""Solana: native SOL transfers."""

from __future__ import annotations

import base64

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from ..net import JsonRpc
from .base import Asset, Chain, HistoryItem, InsufficientFunds, PreparedTx


def build_transfer(kp: Keypair, to: Pubkey, lamports: int, blockhash: str) -> Transaction:
    ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=to, lamports=lamports))
    bh = Hash.from_string(blockhash)
    msg = Message.new_with_blockhash([ix], kp.pubkey(), bh)
    return Transaction([kp], msg, bh)


class SolanaChain(Chain):
    key = "sol"
    name = "Solana"

    def __init__(self, cfg: dict, keyring):
        self.native = Asset("SOL", 9)
        self.rpc = JsonRpc(cfg["rpc"])
        self.explorer_tx = cfg["explorer"] + "/tx/{}"
        self.explorer_addr = cfg["explorer"] + "/account/{}"
        k = keyring.solana_key()
        self._kp = Keypair.from_seed(k.private)
        if bytes(self._kp.pubkey()) != k.public:
            raise RuntimeError("Solana key derivation mismatch.")

    def address(self) -> str:
        return str(self._kp.pubkey())

    def balance(self, asset: Asset) -> int:
        return int(self.rpc.call("getBalance", [self.address(), {"commitment": "confirmed"}])["value"])

    def validate_address(self, addr: str) -> str | None:
        try:
            pk = Pubkey.from_string(addr.strip())
        except Exception:
            return "Not a valid Solana address."
        if pk == Pubkey.default():
            return "Refusing to send to the system program address."
        return None

    def prepare_send(self, asset: Asset, to: str, amount: int | None) -> PreparedTx:
        err = self.validate_address(to)
        if err:
            raise ValueError(err)
        if not amount or amount <= 0:
            raise ValueError("Enter an amount.")
        to_pk = Pubkey.from_string(to.strip())
        bh = self.rpc.call("getLatestBlockhash", [{"commitment": "finalized"}])["value"]["blockhash"]
        tx = build_transfer(self._kp, to_pk, amount, bh)
        msg_b64 = base64.b64encode(bytes(tx.message)).decode()
        fee = self.rpc.call("getFeeForMessage", [msg_b64, {"commitment": "finalized"}])["value"] or 5000
        rent_min = int(self.rpc.call("getMinimumBalanceForRentExemption", [0]))
        bal = self.balance(self.native)
        warnings = []
        if bal < amount + fee:
            raise InsufficientFunds("Not enough SOL for amount plus fee.")
        remaining = bal - amount - fee
        if 0 < remaining < rent_min:
            raise ValueError(f"This would leave {remaining} lamports, below the rent-exempt minimum "
                             f"({rent_min}). Send a bit less, or send everything minus the fee.")
        to_bal = int(self.rpc.call("getBalance", [str(to_pk)])["value"])
        if to_bal == 0 and amount < rent_min:
            raise ValueError(f"Recipient account is empty; the first transfer must be at least "
                             f"{rent_min / 1e9} SOL (rent-exempt minimum).")
        if to_bal == 0:
            warnings.append("Recipient address has never been used.")
        raw = base64.b64encode(bytes(tx)).decode()

        def send() -> str:
            return self.rpc.call("sendTransaction", [raw, {"encoding": "base64", "preflightCommitment": "confirmed"}])

        return PreparedTx(self.name, asset, self.address(), str(to_pk), amount, int(fee), "",
                          self.native, send, warnings)

    def history(self, asset: Asset, limit: int = 10) -> list[HistoryItem]:
        me = self.address()
        sigs = self.rpc.call("getSignaturesForAddress", [me, {"limit": 20}]) or []
        out = []
        for s in sigs:
            if len(out) >= limit:
                break
            sig = s["signature"]
            item = HistoryItem(sig, s.get("blockTime"), "in", 0, "",
                               "failed" if s.get("err") else "confirmed")
            try:
                tx = self.rpc.call("getTransaction", [sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                                                            "maxSupportedTransactionVersion": 0}])
            except Exception:
                tx = None
            if tx and tx.get("meta"):
                keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
                pre, post = tx["meta"]["preBalances"], tx["meta"]["postBalances"]
                if me in keys:
                    i = keys.index(me)
                    delta = post[i] - pre[i]
                    item.direction = "in" if delta > 0 else "out"
                    item.amount = abs(delta)
                    deltas = [(post[j] - pre[j], keys[j]) for j in range(len(keys)) if j != i]
                    if deltas:
                        d, k = (max if delta < 0 else min)(deltas)
                        if (d > 0) == (delta < 0) and d != 0:
                            item.counterparty = k
                if item.amount == 0:
                    continue  # spam/memo transactions that didn't move our SOL
            else:
                item.direction = "unknown"  # couldn't load details; still list it
            out.append(item)
        return out
