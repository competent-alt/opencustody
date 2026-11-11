"""TON: native Toncoin transfers from a v4r2 wallet contract."""

from __future__ import annotations

import base64
import urllib.parse

from tonsdk.contract.wallet import WalletV4ContractR2
from tonsdk.utils import Address

from .. import net
from .base import Asset, Chain, HistoryItem, InsufficientFunds, PreparedTx

FEE_RESERVE = 50_000_000  # 0.05 TON kept for fees (actual fees are usually ~0.005)


def make_wallet(pub: bytes, priv_seed: bytes) -> WalletV4ContractR2:
    return WalletV4ContractR2(public_key=pub, private_key=priv_seed + pub, wc=0)


def parse_address(addr: str) -> Address:
    addr = addr.strip()
    if not (len(addr) == 48 or addr.startswith(("0:", "-1:"))):
        raise ValueError("Not a TON address.")
    a = Address(addr)  # raises on bad checksum / format
    if a.wc != 0:
        raise ValueError("Only basechain (workchain 0) addresses are supported.")
    return a


class TonChain(Chain):
    key = "ton"
    name = "TON"

    def __init__(self, cfg: dict, keyring):
        self.native = Asset("TON", 9)
        self.api = cfg["api"].rstrip("/")
        self.api_key = cfg.get("api_key") or ""
        self.explorer_addr = cfg["explorer"] + "/{}"
        k = keyring.ton_key()
        self._w = make_wallet(k.public, k.private)
        # Non-bounceable form, as wallets show for receiving
        self._addr = self._w.address.to_string(True, True, False)

    def address(self) -> str:
        return self._addr

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def _get(self, method: str, **params):
        q = urllib.parse.urlencode(params)
        r = net.get_json(f"{self.api}/{method}?{q}", headers=self._headers())
        if not r.get("ok"):
            raise net.NetError(f"toncenter {method}: {r.get('error')}")
        return r["result"]

    def _post(self, method: str, payload: dict):
        r = net.post_json(f"{self.api}/{method}", payload, headers=self._headers())
        if not r.get("ok"):
            raise net.NetError(f"toncenter {method}: {r.get('error')}")
        return r["result"]

    def balance(self, asset: Asset) -> int:
        return int(self._get("getAddressBalance", address=self._addr))

    def validate_address(self, addr: str) -> str | None:
        try:
            parse_address(addr)
            return None
        except Exception as e:
            return f"Invalid TON address: {e}"

    def _seqno(self) -> int:
        if self._get("getAddressState", address=self._addr) != "active":
            return 0
        r = self._post("runGetMethod", {"address": self._addr, "method": "seqno", "stack": []})
        if r.get("exit_code") != 0:
            raise RuntimeError("Could not read wallet seqno.")
        return int(r["stack"][0][1], 16)

    def prepare_send(self, asset: Asset, to: str, amount: int | None) -> PreparedTx:
        dest = parse_address(to)
        if not amount or amount <= 0:
            raise ValueError("Enter an amount.")
        if self.balance(self.native) < amount + FEE_RESERVE:
            raise InsufficientFunds("Not enough TON (keep about 0.05 TON for fees).")
        warnings = []
        state = self._get("getAddressState", address=dest.to_string(True, True, True))
        if state != "active" and dest.is_bounceable:
            warnings.append("Recipient wallet is not deployed and the address is bounceable: "
                            "funds will bounce back (minus fees). Use their non-bounceable (UQ...) address.")
        seqno = self._seqno()
        if seqno == 0:
            warnings.append("First outgoing transfer: this also deploys your wallet contract.")
        query = self._w.create_transfer_message(to.strip(), amount, seqno)
        boc = base64.b64encode(query["message"].to_boc(False)).decode()

        def send() -> str:
            self._post("sendBoc", {"boc": boc})
            return query["message"].bytes_hash().hex()

        return PreparedTx(self.name, asset, self._addr, to.strip(), amount, 10_000_000,
                          "(estimate)", self.native, send, warnings)

    def tx_url(self, txid: str) -> str:
        return f"{self.explorer_addr.rsplit('/', 1)[0]}/transaction/{txid}" if len(txid) == 64 else self.address_url()

    def history(self, asset: Asset, limit: int = 20) -> list[HistoryItem]:
        out = []
        for t in self._get("getTransactions", address=self._addr, limit=limit):
            h = base64.b64decode(t["transaction_id"]["hash"]).hex()
            inm, outs = t.get("in_msg") or {}, t.get("out_msgs") or []
            sent = sum(int(m.get("value") or 0) for m in outs)
            got = int(inm.get("value") or 0) if inm.get("source") else 0
            if outs:
                out.append(HistoryItem(h, t.get("utime"), "out", sent, outs[0].get("destination", "")))
            elif got:
                out.append(HistoryItem(h, t.get("utime"), "in", got, inm.get("source", "")))
        return out
