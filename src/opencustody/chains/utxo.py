"""Bitcoin and Litecoin: BIP-84 native SegWit wallet over an Esplora API."""

from __future__ import annotations

from dataclasses import dataclass

import coincurve
from embit import base58, bech32
from embit.ec import PublicKey
from embit.script import Script, Witness, p2pkh
from embit.transaction import SIGHASH, Transaction, TransactionInput, TransactionOutput

from .. import net
from .base import Asset, Chain, HistoryItem, InsufficientFunds, PreparedTx

NETWORKS = {
    "btc": {"name": "Bitcoin", "symbol": "BTC", "hrp": "bc", "p2pkh": 0x00, "p2sh": (0x05,)},
    # Litecoin P2SH: 0x32 ("M...") and the legacy 0x05 ("3...")
    "ltc": {"name": "Litecoin", "symbol": "LTC", "hrp": "ltc", "p2pkh": 0x30, "p2sh": (0x32, 0x05)},
}
DUST = 546
# vbytes for P2WPKH: overhead 10.5, input 68, output up to 43 (P2TR is the largest)
TX_OVERHEAD, IN_VB, OUT_VB = 11, 68, 43
MAX_FEE_RATE = 1000  # sat/vB sanity ceiling


def address_to_script(addr: str, coin: str) -> Script:
    """Decode an address and check that it belongs to this coin's mainnet."""
    n = NETWORKS[coin]
    lower = addr.lower()
    if lower.startswith(n["hrp"] + "1"):
        if addr != lower and addr != addr.upper():
            raise ValueError("Mixed-case bech32 address.")
        ver, prog = bech32.decode(n["hrp"], lower)
        if ver is None:
            raise ValueError("Invalid bech32 address checksum.")
        if ver == 0 and len(prog) not in (20, 32):
            raise ValueError("Invalid SegWit v0 program length.")
        if ver == 1 and len(prog) != 32:
            raise ValueError("Invalid Taproot address.")
        if ver > 1:
            raise ValueError("Unsupported future SegWit version.")
        op = 0 if ver == 0 else 0x50 + ver
        return Script(bytes([op, len(prog)]) + bytes(prog))
    try:
        raw = base58.decode_check(addr)
    except Exception:
        raise ValueError("Invalid address.") from None
    if len(raw) != 21:
        raise ValueError("Invalid address length.")
    if raw[0] == n["p2pkh"]:
        return Script(b"\x76\xa9\x14" + raw[1:] + b"\x88\xac")
    if raw[0] in n["p2sh"]:
        return Script(b"\xa9\x14" + raw[1:] + b"\x87")
    raise ValueError(f"Not a {n['name']} mainnet address.")


@dataclass
class Utxo:
    txid: str
    vout: int
    value: int
    change: int
    index: int


def vsize(n_in: int, n_out: int) -> int:
    return TX_OVERHEAD + IN_VB * n_in + OUT_VB * n_out


def select_coins(utxos: list[Utxo], amount: int | None, rate: int) -> tuple[list[Utxo], int, int]:
    """Largest-first selection. Returns (inputs, send amount, change)."""
    pool = sorted(utxos, key=lambda u: u.value, reverse=True)
    if amount is None:  # sweep
        total = sum(u.value for u in pool)
        fee = vsize(len(pool), 1) * rate
        if total - fee < DUST:
            raise InsufficientFunds("Balance too small to cover the fee.")
        return pool, total - fee, 0
    chosen, total = [], 0
    for u in pool:
        chosen.append(u)
        total += u.value
        fee2 = vsize(len(chosen), 2) * rate
        if total >= amount + fee2:
            change = total - amount - fee2
            if change < DUST:  # absorb dust change into fee
                return chosen, amount, 0
            return chosen, amount, change
    fee1 = vsize(len(chosen), 1) * rate
    if total >= amount + fee1:
        return chosen, amount, 0
    raise InsufficientFunds("Not enough confirmed funds for amount plus fee.")


def spend_exactly(coins: list[Utxo], amount: int | None, rate: int) -> tuple[list[Utxo], int, int]:
    """Coin control: spend every selected coin (no more, no fewer). Returns (inputs, send amount, change)."""
    total = sum(u.value for u in coins)
    if amount is None:
        send = total - vsize(len(coins), 1) * rate
        if send < DUST:
            raise InsufficientFunds("Selected coins don't cover the fee.")
        return coins, send, 0
    change = total - amount - vsize(len(coins), 2) * rate
    if change >= DUST:
        return coins, amount, change
    if total >= amount + vsize(len(coins), 1) * rate:
        return coins, amount, 0  # leftover below dust goes to the fee
    raise InsufficientFunds("Selected coins don't cover the amount plus fee.")


def build_signed_tx(keyring, coin: str, inputs: list[Utxo], outputs: list[tuple[Script, int]]) -> Transaction:
    vin = [TransactionInput(bytes.fromhex(u.txid), u.vout, sequence=0xFFFFFFFD) for u in inputs]  # RBF on
    vout = [TransactionOutput(v, s) for s, v in outputs]
    tx = Transaction(version=2, vin=vin, vout=vout, locktime=0)
    for i, u in enumerate(inputs):
        priv, pub, _ = keyring.bip84_key(coin, u.change, u.index)
        # Sign with libsecp256k1 (coincurve): constant-time, RFC 6979 nonces, low-S.
        ck = coincurve.PrivateKey(priv)
        if ck.public_key.format(compressed=True) != pub:
            raise RuntimeError("Key derivation mismatch; refusing to sign.")
        h = tx.sighash_segwit(i, p2pkh(PublicKey.parse(pub)), u.value, sighash=SIGHASH.ALL)
        sig = ck.sign(h, hasher=None) + bytes([SIGHASH.ALL])
        tx.vin[i].witness = Witness([sig, pub])
    return tx


class UtxoChain(Chain):
    supports_max = True

    def __init__(self, coin: str, cfg: dict, keyring):
        n = NETWORKS[coin]
        self.coin = coin
        self.key = coin
        self.name = n["name"]
        self.native = Asset(n["symbol"], 8)
        self.api = cfg["esplora"].rstrip("/")
        self.gap = int(cfg.get("gap_limit", 10))
        self.explorer_tx = cfg["explorer"] + "/tx/{}"
        self.explorer_addr = cfg["explorer"] + "/address/{}"
        self.kr = keyring
        self._next = {0: 0, 1: 0}
        self._used: list[tuple[int, int, str]] = []
        self._stats: dict = {}

    def _addr(self, change: int, index: int) -> str:
        return self.kr.bip84_key(self.coin, change, index)[2]

    def _scan(self) -> None:
        used = []
        self._stats = {}
        for change in (0, 1):
            i, gap = 0, 0
            while gap < self.gap:
                a = self._addr(change, i)
                st = self._stats[a] = net.get_json(f"{self.api}/address/{a}")
                n_tx = st["chain_stats"]["tx_count"] + st["mempool_stats"]["tx_count"]
                if n_tx:
                    used.append((change, i, a))
                    gap = 0
                    self._next[change] = i + 1
                else:
                    gap += 1
                i += 1
        self._used = used

    def address(self) -> str:
        return self._addr(0, self._next[0])

    def balance(self, asset: Asset) -> int:
        self._scan()
        total = 0
        for _, _, a in self._used:
            st = self._stats[a]
            for k in ("chain_stats", "mempool_stats"):
                total += st[k]["funded_txo_sum"] - st[k]["spent_txo_sum"]
        return total

    def _utxos(self) -> list[Utxo]:
        out = []
        for change, idx, a in self._used:
            for u in net.get_json(f"{self.api}/address/{a}/utxo"):
                if u["status"].get("confirmed"):
                    out.append(Utxo(u["txid"], int(u["vout"]), int(u["value"]), change, idx))
        return out

    def validate_address(self, addr: str) -> str | None:
        try:
            address_to_script(addr.strip(), self.coin)
            return None
        except ValueError as e:
            return str(e)

    def _fee_rate(self) -> int:
        est = net.get_json(f"{self.api}/fee-estimates")
        rate = est.get("3") or est.get("6") or 1
        rate = max(1, int(-(-float(rate) // 1)))  # ceil
        if rate > MAX_FEE_RATE:
            raise RuntimeError(f"Fee estimate {rate} sat/vB looks wrong; refusing.")
        return rate

    supports_coin_control = True

    def coins(self, asset: Asset) -> list[dict]:
        """Spendable (confirmed) coins, for coin control."""
        self._scan()
        addr = {(c, i): a for c, i, a in self._used}
        return [{"id": f"{u.txid}:{u.vout}", "txid": u.txid, "vout": u.vout, "value": u.value,
                 "address": addr[(u.change, u.index)], "change": bool(u.change)}
                for u in sorted(self._utxos(), key=lambda u: u.value, reverse=True)]

    def prepare_send(self, asset: Asset, to: str, amount: int | None, coins: list[str] | None = None) -> PreparedTx:
        to = to.strip()
        dest = address_to_script(to, self.coin)
        if amount is not None and amount < DUST:
            raise ValueError(f"Amount below dust limit ({DUST} sats).")
        self._scan()
        rate = self._fee_rate()
        available = self._utxos()
        if coins:
            wanted = set(coins)
            chosen = [u for u in available if f"{u.txid}:{u.vout}" in wanted]
            if len(chosen) != len(wanted):
                raise ValueError("Some selected coins are no longer spendable. Refresh and pick again.")
            inputs, send_amt, change = spend_exactly(chosen, amount, rate)
        else:
            inputs, send_amt, change = select_coins(available, amount, rate)
        warnings = []
        n_addr = len({(u.change, u.index) for u in inputs})
        if n_addr > 1:
            warnings.append(f"This spends coins from {n_addr} of your addresses, which links them together "
                            "on-chain. Use coin control to avoid that.")
        outputs = [(dest, send_amt)]
        if change:
            change_addr = self._addr(1, self._next[1])
            outputs.append((address_to_script(change_addr, self.coin), change))
        tx = build_signed_tx(self.kr, self.coin, inputs, outputs)
        fee = sum(u.value for u in inputs) - sum(v for _, v in outputs)
        raw = tx.serialize().hex()

        def send() -> str:
            return net.post_text(f"{self.api}/tx", raw).strip()

        return PreparedTx(self.name, asset, self.address(), to, send_amt, fee, f"({rate} sat/vB)",
                          self.native, send, warnings)

    def history(self, asset: Asset, limit: int = 25) -> list[HistoryItem]:
        self._scan()
        ours = {a for _, _, a in self._used}
        txs: dict[str, dict] = {}
        for a in ours:
            for t in net.get_json(f"{self.api}/address/{a}/txs"):
                txs[t["txid"]] = t
        out = []
        for t in txs.values():
            recv = sum(o.get("value", 0) for o in t["vout"] if o.get("scriptpubkey_address") in ours)
            spent = sum((i.get("prevout") or {}).get("value", 0) for i in t["vin"]
                        if (i.get("prevout") or {}).get("scriptpubkey_address") in ours)
            net_amt = recv - spent
            if spent and net_amt <= 0:
                others = [o for o in t["vout"] if o.get("scriptpubkey_address") not in ours]
                direction = "out" if others else "self"
                party = others[0].get("scriptpubkey_address", "") if others else ""
            else:
                direction = "in"
                srcs = [(i.get("prevout") or {}).get("scriptpubkey_address", "") for i in t["vin"]]
                party = next((s for s in srcs if s and s not in ours), "")
            st = t.get("status") or {}
            out.append(HistoryItem(t["txid"], st.get("block_time"), direction, abs(net_amt), party,
                                   "confirmed" if st.get("confirmed") else "pending"))
        out.sort(key=lambda h: h.time or 2**62, reverse=True)
        return out[:limit]
