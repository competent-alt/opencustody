import pytest

from opencustody import webapi
from opencustody.paths import vault_path

ABANDON = "abandon " * 11 + "about"
PW = "correct horse battery"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCUSTODY_HOME", str(tmp_path))
    return webapi.WalletApi()


def test_create_flow(api):
    assert api.state()["vault_exists"] is False
    words = api.create_mnemonic()["words"]
    assert len(words) == 24
    pos = api.verify_challenge()["positions"]
    assert not api.verify_words(["wrong"] * 3)["ok"]
    assert api.verify_words([words[p - 1] for p in pos])["ok"]
    assert not api.save_wallet(PW, PW + "x")["ok"]  # mismatch
    assert not api.save_wallet("short", "short")["ok"]
    assert api.save_wallet(PW, PW)["ok"]
    assert vault_path().exists()
    assert api.state()["unlocked"] is True


def test_restore_unlock_lock(api):
    assert not api.restore_mnemonic("abandon " * 12)["ok"]
    assert api.restore_mnemonic(ABANDON)["ok"]
    assert api.save_wallet(PW, PW)["ok"]
    accts = api.accounts()["accounts"]
    ids = [a["id"] for a in accts]
    assert {"btc:0", "ltc:0", "eth:0", "sol:0", "trx:0", "ton:0"} <= set(ids)
    eth = next(a for a in accts if a["id"] == "eth:0")
    assert eth["address"] == "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
    assert "seed" not in str(accts).lower() and ABANDON.split()[0] not in str(accts)
    assert api.validate("eth:0", "0x000000000000000000000000000000000000dEaD")["valid"]
    assert not api.validate("eth:0", "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu")["valid"]

    api.lock()
    assert api.state()["unlocked"] is False
    assert not api.accounts()["ok"]
    assert not api.balance("eth:0")["ok"]
    assert not api.unlock("wrong password!")["ok"]
    assert api.unlock(PW)["ok"]


def test_errors_are_returned_not_raised(api):
    r = api.balance("nope")
    assert r["ok"] is False and r["error"]
    assert api.broadcast("unknown-token")["ok"] is False
    assert api.open_url("file:///etc/passwd")["ok"] is False
    assert api.open_url("javascript:alert(1)")["ok"] is False


def test_prepared_tx_single_use(api):
    api.restore_mnemonic(ABANDON)
    api.save_wallet(PW, PW)
    sent = []

    class Fake:
        def broadcast(self):
            sent.append(1)
            return "txid"

    chain = api._accounts["eth:0"][0]
    api._prepared = {"tok": (chain, Fake())}
    assert api.broadcast("tok")["ok"]
    assert not api.broadcast("tok")["ok"]  # cannot replay
    api._prepared = {"tok2": (chain, Fake())}
    api.lock()
    assert not api.broadcast("tok2")["ok"]  # lock discards reviews
    assert sent == [1]
