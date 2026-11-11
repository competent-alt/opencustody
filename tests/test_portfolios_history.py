import pytest

from opencustody import webapi
from opencustody.chains import evm, tron, utxo
from opencustody.chains.base import HistoryUnavailable
from opencustody.keys import Keyring

ABANDON = "abandon " * 11 + "about"
PW = "correct horse battery"


@pytest.fixture(scope="module")
def kr():
    return Keyring(ABANDON)


# ---- derivation ----------------------------------------------------------------
def test_portfolio_zero_is_classic_wallet(kr):
    p0 = kr.portfolio(0)
    assert p0.evm_address() == "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
    assert p0.bip84_key("btc", 0, 0)[2] == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
    assert p0.tron_address() == "TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH"


def test_portfolio_one_matches_metamask_account_2(kr):
    assert kr.portfolio(1).evm_address() == "0x6Fac4D18c912343BF86fa7049364Dd4E424Ab9C0"


def test_portfolios_are_distinct_everywhere(kr):
    a, b = kr.portfolio(1), kr.portfolio(2)
    assert a.bip84_key("btc", 0, 0)[2] != b.bip84_key("btc", 0, 0)[2]
    assert a.bip84_key("ltc", 0, 0)[2] != b.bip84_key("ltc", 0, 0)[2]
    assert a.tron_address() != b.tron_address()
    assert a.solana_key().public != b.solana_key().public
    assert a.ton_key().public != b.ton_key().public
    assert a.monero_keys().address != b.monero_keys().address
    assert a.derived_secret("x") != b.derived_secret("x") != kr.derived_secret("x")


def test_portfolio_range(kr):
    with pytest.raises(ValueError):
        kr.portfolio(-1)
    with pytest.raises(ValueError):
        kr.portfolio(100)


def test_wiped_keyring_cannot_derive():
    k = Keyring(ABANDON)
    p = k.portfolio(1)
    k.wipe()
    with pytest.raises(RuntimeError):
        p.evm_address()


# ---- bridge --------------------------------------------------------------------
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCUSTODY_HOME", str(tmp_path))
    a = webapi.WalletApi()
    a.restore_mnemonic(ABANDON)
    assert a.save_wallet(PW, PW)["ok"]
    return a


def _eth(api):
    return next(x["address"] for x in api.accounts()["accounts"] if x["id"] == "eth:0")


def test_create_switch_rename_remove(api):
    assert api.portfolios()["items"] == [{"n": 0, "name": "Main"}]
    assert not api.create_portfolio("   ")["ok"]
    r = api.create_portfolio("Savings")
    assert r["ok"] and r["n"] == 1
    assert api.portfolios()["active"] == 1
    assert _eth(api) == "0x6Fac4D18c912343BF86fa7049364Dd4E424Ab9C0"
    assert api.rename_portfolio(1, "Cold storage")["ok"]
    assert api.portfolios()["items"][1]["name"] == "Cold storage"
    assert api.switch_portfolio(0)["ok"] and _eth(api) == "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
    assert not api.switch_portfolio(7)["ok"]
    assert not api.remove_portfolio(0)["ok"]
    assert api.switch_portfolio(1)["ok"]
    assert api.remove_portfolio(1)["ok"]
    assert api.portfolios()["active"] == 0 and _eth(api).startswith("0x9858")
    # Re-adding brings back the same addresses (index 1 is free again).
    assert api.create_portfolio("Back")["n"] == 1 and _eth(api) == "0x6Fac4D18c912343BF86fa7049364Dd4E424Ab9C0"


def test_active_portfolio_survives_relock(api):
    api.create_portfolio("Savings")
    api.lock()
    assert api.unlock(PW)["ok"]
    assert api.portfolios()["active"] == 1 and _eth(api).startswith("0x6Fac")


def test_switch_discards_pending_review(api):
    chain = api._accounts["eth:0"][0]
    api._prepared = {"tok": (chain, object())}
    api.create_portfolio("Other")
    assert not api.broadcast("tok")["ok"]


def test_prices_disabled(api):
    api._cfg["prices"] = {"enabled": False}
    r = api.prices()
    assert r["ok"] and r["enabled"] is False


def test_history_unavailable_is_not_an_error(api):
    r = api.history("bsc:0")
    assert r["ok"] and r["items"] == [] and r["unavailable"]


# ---- history parsing (offline) -------------------------------------------------------
def test_utxo_history_directions(kr, monkeypatch):
    c = utxo.UtxoChain("btc", {"esplora": "https://x", "explorer": "https://e", "gap_limit": 2}, kr.portfolio(0))
    me = kr.bip84_key("btc", 0, 0)[2]
    change = kr.bip84_key("btc", 1, 0)[2]
    other = "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g_not_ours"
    txs = [
        {"txid": "a1", "status": {"confirmed": True, "block_time": 100},
         "vin": [{"prevout": {"scriptpubkey_address": other, "value": 5000}}],
         "vout": [{"scriptpubkey_address": me, "value": 4000}]},
        {"txid": "b2", "status": {"confirmed": False},
         "vin": [{"prevout": {"scriptpubkey_address": me, "value": 4000}}],
         "vout": [{"scriptpubkey_address": other, "value": 1000}, {"scriptpubkey_address": change, "value": 2800}]},
    ]

    def fake(url, **kw):
        if url.endswith("/txs"):
            return txs if me in url else []
        addr = url.rsplit("/", 1)[1]
        used = addr in (me, change)
        n = 1 if used else 0
        return {"chain_stats": {"tx_count": n, "funded_txo_sum": 0, "spent_txo_sum": 0},
                "mempool_stats": {"tx_count": 0, "funded_txo_sum": 0, "spent_txo_sum": 0}}

    monkeypatch.setattr(utxo.net, "get_json", fake)
    h = c.history(c.native)
    assert [(x.txid, x.direction, x.amount, x.status) for x in h] == [
        ("b2", "out", 1200, "pending"),  # 1000 sent + 200 fee
        ("a1", "in", 4000, "confirmed"),
    ]
    assert h[0].counterparty == other and h[1].counterparty == other


def test_evm_history_parsing(kr, monkeypatch):
    c = evm.EvmChain({"key": "eth", "name": "Ethereum", "chain_id": 1, "symbol": "ETH", "rpc": "https://x",
                      "explorer": "https://e", "history_api": "https://h/api"}, kr.portfolio(0))
    me = c.address()
    rows = [
        {"hash": "0x1", "from": me.lower(), "to": "0xabc", "value": "5", "timeStamp": "200", "isError": "0"},
        {"hash": "0x2", "from": "0xdef", "to": me.lower(), "value": "7", "timeStamp": "100", "isError": "0"},
        {"hash": "0x3", "from": me.lower(), "to": "0xtoken", "value": "0", "timeStamp": "50"},  # token call: skipped
        {"hash": "0x4", "from": me.lower(), "to": "0xabc", "value": "1", "timeStamp": "40", "isError": "1"},
    ]
    seen = {}
    monkeypatch.setattr(evm.net, "get_json", lambda url, **kw: seen.setdefault("u", url) and {"status": "1", "result": rows})
    h = c.history(c.native)
    assert [(x.txid, x.direction, x.amount, x.status) for x in h] == [
        ("0x1", "out", 5, "confirmed"), ("0x2", "in", 7, "confirmed"), ("0x4", "out", 1, "failed")]
    assert "action=txlist" in seen["u"]


def test_evm_history_needs_key_where_no_free_api(kr):
    c = evm.EvmChain({"key": "base", "name": "Base", "chain_id": 8453, "symbol": "ETH", "rpc": "https://x",
                      "explorer": "https://e", "history_api": ""}, kr.portfolio(0))
    with pytest.raises(HistoryUnavailable):
        c.history(c.native)


def test_tron_history_parsing(kr, monkeypatch):
    c = tron.TronChain({"api": "https://t", "explorer": "https://e", "tokens": []}, kr.portfolio(0))
    me_hex = tron.address_to_bytes(c.address()).hex()
    other = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    other_hex = tron.address_to_bytes(other).hex()
    data = {"data": [
        {"txID": "t1", "block_timestamp": 2000, "ret": [{"contractRet": "SUCCESS"}],
         "raw_data": {"contract": [{"type": "TransferContract",
                                    "parameter": {"value": {"owner_address": other_hex, "to_address": me_hex, "amount": 9}}}]}},
        {"txID": "t2", "block_timestamp": 1000, "raw_data": {"contract": [{"type": "TriggerSmartContract", "parameter": {"value": {}}}]}},
    ]}
    monkeypatch.setattr(tron.net, "get_json", lambda url, **kw: data)
    h = c.history(c.native)
    assert len(h) == 1 and h[0].direction == "in" and h[0].amount == 9 and h[0].counterparty == other and h[0].time == 2
