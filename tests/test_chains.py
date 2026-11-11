import hashlib

import pytest
from eth_account import Account
from eth_keys import keys as eth_keys
from embit import ec
from embit.script import p2pkh, p2wpkh
from embit.transaction import Transaction
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from opencustody.chains import tron, utxo
from opencustody.chains.base import Asset
from opencustody.chains.evm import EvmChain, encode_transfer, validate_evm_address
from opencustody.chains.solana import build_transfer
from opencustody.chains.ton import make_wallet, parse_address
from opencustody.keys import Keyring

ABANDON = "abandon " * 11 + "about"


@pytest.fixture(scope="module")
def kr():
    return Keyring(ABANDON)


# ---- EVM ------------------------------------------------------------------------
class FakeRpc:
    def __init__(self, responses):
        self.r = responses
        self.calls = []

    def call(self, method, params=None):
        self.calls.append((method, params))
        v = self.r[method]
        return v(params) if callable(v) else v


def _evm(kr, **over):
    cfg = {"key": "eth", "name": "Ethereum", "chain_id": 1, "symbol": "ETH", "rpc": "https://x",
           "explorer": "https://etherscan.io",
           "tokens": [{"symbol": "USDC", "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"}]}
    c = EvmChain(cfg, kr)
    r = {"eth_chainId": "0x1", "eth_getTransactionCount": "0x5",
         "eth_getBlockByNumber": {"baseFeePerGas": hex(10**9)}, "eth_maxPriorityFeePerGas": hex(10**8),
         "eth_estimateGas": hex(21000), "eth_getBalance": hex(10**18),
         "eth_call": lambda p: hex(6) if p[0]["data"] == "0x313ce567" else hex(5_000_000)}
    r.update(over)
    c.rpc = FakeRpc(r)
    return c


def test_evm_native_send_signed_correctly(kr):
    c = _evm(kr)
    to = "0x000000000000000000000000000000000000dEaD"
    ptx = c.prepare_send(c.native, to, 10**17)
    sent = []
    c.rpc.r["eth_sendRawTransaction"] = lambda p: sent.append(p[0]) or "0xabc"
    assert ptx.broadcast() == "0xabc"
    raw = sent[0]
    assert Account.recover_transaction(raw) == kr.evm_address()
    assert ptx.fee == 21000 * (2 * 10**9 + 10**8)


def test_evm_wrong_chain_id_refused(kr):
    c = _evm(kr, eth_chainId="0x38")
    with pytest.raises(RuntimeError, match="chain"):
        c.prepare_send(c.native, "0x000000000000000000000000000000000000dEaD", 1)


def test_evm_insufficient(kr):
    c = _evm(kr, eth_getBalance="0x1")
    with pytest.raises(Exception, match="Not enough"):
        c.prepare_send(c.native, "0x000000000000000000000000000000000000dEaD", 10**17)


def test_evm_token_send(kr):
    c = _evm(kr, eth_estimateGas=hex(50000))
    usdc = c.assets()[1]
    ptx = c.prepare_send(usdc, "0x000000000000000000000000000000000000dEaD", 1_000_000)
    assert usdc.decimals == 6
    assert ptx.amount == 1_000_000 and ptx.recipient.endswith("dEaD")
    with pytest.raises(ValueError):
        c.prepare_send(usdc, usdc.contract, 1)


def test_evm_address_validation():
    assert validate_evm_address("0x9858EfFD232B4033E47d90003D41EC34EcaEda94") is None
    assert validate_evm_address("0x9858effd232b4033e47d90003d41ec34ecaeda94") is None
    assert "checksum" in validate_evm_address("0x9858EfFD232B4033E47d90003D41EC34EcaEdA94")
    assert validate_evm_address("0x" + "00" * 20)
    assert validate_evm_address("0x123")
    assert encode_transfer("0x" + "11" * 20, 5).endswith("05")


# ---- Bitcoin / Litecoin ------------------------------------------------------------
def test_utxo_address_parsing():
    s = utxo.address_to_script("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu", "btc")
    assert s.data[:2] == b"\x00\x14"
    utxo.address_to_script("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", "btc")
    utxo.address_to_script("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy", "btc")
    utxo.address_to_script("bc1p5d7rjq7g6rdk2yhzks9smlaqtedr4dekq08ge8ztwac72sfr9rusxg3297", "btc")
    utxo.address_to_script("ltc1qjmxnz78nmc8nq77wuxh25n2es7rzm5c2rkk4wh", "ltc")
    for addr, coin in [("ltc1qjmxnz78nmc8nq77wuxh25n2es7rzm5c2rkk4wh", "btc"),
                       ("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu", "ltc"),
                       ("bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyv", "btc"),  # bad checksum
                       ("tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx", "btc"),  # testnet
                       ("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN3", "btc")]:
        with pytest.raises(ValueError):
            utxo.address_to_script(addr, coin)


def test_utxo_embit_crosscheck(kr):
    from embit.networks import NETWORKS
    for i in range(3):
        _, pub, addr = kr.bip84_key("btc", 0, i)
        assert p2wpkh(ec.PublicKey.parse(pub)).address(NETWORKS["main"]) == addr


def test_utxo_sign_and_verify(kr):
    u = utxo.Utxo("ab" * 32, 1, 100_000, 0, 0)
    dest = utxo.address_to_script("bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g", "btc")
    tx = utxo.build_signed_tx(kr, "btc", [u], [(dest, 90_000)])
    parsed = Transaction.parse(tx.serialize())
    assert parsed.vin[0].txid.hex() == "ab" * 32
    assert parsed.vout[0].value == 90_000
    sig_der, pub = parsed.vin[0].witness.items
    pk = ec.PublicKey.parse(pub)
    h = parsed.sighash_segwit(0, p2pkh(pk), 100_000)
    assert pk.verify(ec.Signature.parse(sig_der[:-1]), h)


def test_coin_selection():
    us = [utxo.Utxo("00" * 32, i, v, 0, i) for i, v in enumerate([5000, 50_000, 20_000])]
    ins, amt, change = utxo.select_coins(us, 30_000, 2)
    assert [u.value for u in ins] == [50_000] and amt == 30_000
    assert change == 50_000 - 30_000 - utxo.vsize(1, 2) * 2
    ins, amt, change = utxo.select_coins(us, None, 2)
    assert len(ins) == 3 and change == 0 and amt == 75_000 - utxo.vsize(3, 1) * 2
    with pytest.raises(Exception):
        utxo.select_coins(us, 80_000, 2)


# ---- Tron ------------------------------------------------------------------------
def test_tron_varint():
    assert tron._varint(0) == b"\x00"
    assert tron._varint(300) == b"\xac\x02"


def test_tron_sign_recovers_address(kr):
    owner = tron.address_to_bytes(kr.tron_address())
    to = tron.address_to_bytes("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    contract = tron.encode_contract(tron.TRANSFER_CONTRACT, tron.transfer_value(owner, to, 1_000_000))
    raw = tron.encode_raw(b"\x01\x02", b"\x00" * 8, 1_700_000_060_000, contract, 1_700_000_000_000)
    txid, signed = tron.sign_raw(raw, kr.tron_private_key())
    assert txid == hashlib.sha256(raw).hexdigest()
    sig = signed[-65:]
    pub = eth_keys.Signature(sig).recover_public_key_from_msg_hash(bytes.fromhex(txid))
    eth_addr = bytes.fromhex(pub.to_address()[2:])
    assert b"\x41" + eth_addr == owner
    assert owner in raw and to in raw


def test_tron_address_validation():
    with pytest.raises(Exception):
        tron.address_to_bytes("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")


# ---- Solana ----------------------------------------------------------------------
def test_solana_transfer_signature(kr):
    k = kr.solana_key()
    kp = Keypair.from_seed(k.private)
    assert str(kp.pubkey()) == "HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk"
    to = Pubkey.from_string("11111111111111111111111111111112")
    tx = build_transfer(kp, to, 1000, "11111111111111111111111111111111")
    tx.verify()
    assert tx.message.account_keys[0] == kp.pubkey()


# ---- TON -------------------------------------------------------------------------
def test_ton_address_matches_reference(kr):
    k = kr.ton_key()
    w = make_wallet(k.public, k.private)
    assert w.address.to_string(True, True, False) == kr.ton_address_reference()
    msg = w.create_transfer_message(w.address.to_string(True, True, False), 1, 0)
    assert msg["state_init"] is not None  # seqno 0 deploys the wallet
    with pytest.raises(Exception):
        parse_address("UQAzWZa6nM5mJev91wGc7VCSfBoIsYRqKJpV78N8Add9-RKZ")  # bad checksum


# ---- crypto backends ------------------------------------------------------------
def test_secp256k1_uses_libsecp256k1(kr):
    """python-ecdsa (PYSEC-2026-1325, no fix) must never be on a signing path."""
    from bip_utils.ecc import Secp256k1PrivateKey
    assert "coincurve" in Secp256k1PrivateKey.__module__
    assert "CoinCurve" in type(eth_keys.PrivateKey(b"\x01" * 32).backend).__name__
    # BTC/LTC signatures come from coincurve: verify one against libsecp256k1 directly.
    import coincurve
    priv, pub, _ = kr.bip84_key("btc", 0, 0)
    msg = hashlib.sha256(b"x").digest()
    assert coincurve.PublicKey(pub).verify(coincurve.PrivateKey(priv).sign(msg, hasher=None), msg, hasher=None)


# ---- Monero ---------------------------------------------------------------------
def test_monero_keys(kr):
    m = kr.monero_keys()
    assert m.address.startswith("4") and len(m.address) == 95
    assert len(m.private_spend) == 64 and len(m.private_view) == 64
