import json

import pytest

from opencustody import vault
from opencustody.amounts import fmt, to_base_units
from opencustody.keys import Keyring, is_valid_mnemonic, new_mnemonic

ABANDON = "abandon " * 11 + "about"


@pytest.fixture(scope="module")
def kr():
    return Keyring(ABANDON)


# Published vectors for the "abandon ... about" test phrase (no passphrase),
# matching MetaMask, BIP-84, Phantom, TronLink and Ledger-derived wallets.
def test_known_vectors(kr):
    assert kr.evm_address() == "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
    assert kr.bip84_key("btc", 0, 0)[2] == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
    assert kr.bip84_key("btc", 0, 1)[2] == "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"
    assert kr.bip84_key("btc", 1, 0)[2] == "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el"
    assert kr.bip84_key("ltc", 0, 0)[2] == "ltc1qjmxnz78nmc8nq77wuxh25n2es7rzm5c2rkk4wh"
    assert kr.solana_key() is not None
    assert kr.tron_address() == "TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH"


def test_bip84_account_xpub_vector(kr):
    # BIP-84 spec: account 0 zprv for the test phrase
    assert kr.bip84_account_xprv("btc") == (
        "zprvAdG4iTXWBoARxkkzNpNh8r6Qag3irQB8PzEMkAFeTRXxHpbF9z4QgEvBRmfvqWvGp42t42nvgGpNgYSJA9iefm1yYNZKEm7z6qUWCroSQnE")


def test_passphrase_changes_wallet():
    assert Keyring(ABANDON, "x").evm_address() != Keyring(ABANDON).evm_address()


def test_mnemonic_generation():
    m = new_mnemonic()
    assert len(m.split()) == 24 and is_valid_mnemonic(m)
    assert m != new_mnemonic()


def test_invalid_mnemonic_rejected():
    assert not is_valid_mnemonic("abandon " * 12)
    with pytest.raises(ValueError):
        Keyring("abandon " * 12)


def test_mnemonic_normalization():
    assert Keyring("  ABANDON " * 11 + " About\n").evm_address() == Keyring(ABANDON).evm_address()


def test_vault_roundtrip(tmp_path):
    p = tmp_path / "v.json"
    vault.save(p, vault.Secrets(ABANDON, "pp"), "correct horse battery", {"a": 1})
    s = vault.load(p, "correct horse battery")
    assert s.mnemonic == ABANDON and s.passphrase == "pp"
    assert vault.read_metadata(p) == {"a": 1}
    assert ABANDON.split()[0] not in p.read_text()


def test_vault_wrong_password(tmp_path):
    p = tmp_path / "v.json"
    vault.save(p, vault.Secrets(ABANDON), "correct horse battery")
    with pytest.raises(vault.WrongPassword):
        vault.load(p, "wrong password!!")


def test_vault_rejects_short_password(tmp_path):
    with pytest.raises(vault.VaultError):
        vault.save(tmp_path / "v.json", vault.Secrets(ABANDON), "short")


def test_vault_rejects_tampered_header(tmp_path):
    p = tmp_path / "v.json"
    vault.save(p, vault.Secrets(ABANDON), "correct horse battery")
    doc = json.loads(p.read_text())
    doc["metadata"]["x"] = 1  # authenticated as AAD
    p.write_text(json.dumps(doc))
    with pytest.raises(vault.WrongPassword):
        vault.load(p, "correct horse battery")
    doc["metadata"].pop("x")
    doc["kdf"]["n"] = 2**10  # weakened KDF
    p.write_text(json.dumps(doc))
    with pytest.raises(vault.VaultError):
        vault.load(p, "correct horse battery")


def test_amounts():
    assert to_base_units("1.5", 8) == 150_000_000
    assert to_base_units("0.000000000000000001", 18) == 1
    assert fmt(150_000_000, 8) == "1.5"
    assert fmt(0, 18) == "0"
    for bad in ("0", "-1", "abc", "1e400000", "nan", "inf", "0.123456789", "1,000", "+1", " ", "1e3", "٣"):
        with pytest.raises(ValueError):
            to_base_units(bad, 8)
