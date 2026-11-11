"""Key derivation from a single BIP-39 recovery phrase.

Derivation paths follow what the most common wallets use, so the same phrase
restores the same addresses elsewhere:

  Bitcoin   BIP-84  m/84'/0'/0'/c/i          (native SegWit, bc1...)
  Litecoin  BIP-84  m/84'/2'/0'/c/i          (native SegWit, ltc1...)
  EVM       BIP-44  m/44'/60'/0'/0/0         (MetaMask / Ledger default)
  Solana    SLIP-10 m/44'/501'/0'/0'         (Phantom / Solflare default)
  Tron      BIP-44  m/44'/195'/0'/0/0        (TronLink default)
  TON       SLIP-10 m/44'/607'/0'            (Ledger TON app), wallet v4r2
  Monero    SLIP-10 m/44'/128'/0'            spend key reduced mod l (Ledger-style)
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from bip_utils import (
    Bip39MnemonicGenerator,
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip39WordsNum,
    Bip44,
    Bip44Changes,
    Bip44Coins,
    Bip84,
    Bip84Coins,
    Monero,
)


def new_mnemonic() -> str:
    """24 words = 256 bits of entropy from the OS CSPRNG."""
    return str(Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_24))


def normalize_mnemonic(text: str) -> str:
    return " ".join(text.lower().split())


def is_valid_mnemonic(text: str) -> bool:
    try:
        return Bip39MnemonicValidator().IsValid(normalize_mnemonic(text))
    except Exception:
        return False


@dataclass
class Ed25519Key:
    private: bytes  # 32-byte seed
    public: bytes  # 32-byte public key


@dataclass
class MoneroKeys:
    address: str
    private_spend: str
    private_view: str


class Keyring:
    """Holds the BIP-39 seed in memory while the wallet is unlocked."""

    def __init__(self, mnemonic: str, passphrase: str = ""):  # nosec B107 - empty BIP-39 passphrase is the standard default
        mnemonic = normalize_mnemonic(mnemonic)
        if not is_valid_mnemonic(mnemonic):
            raise ValueError("Invalid recovery phrase (bad word or checksum).")
        self._seed = bytes(Bip39SeedGenerator(mnemonic).Generate(passphrase))

    def wipe(self) -> None:
        # Python can't guarantee memory is zeroed, but drop our reference.
        self._seed = b""

    # -- secp256k1 account chains -------------------------------------------------
    def _bip44_addr(self, coin: Bip44Coins, index: int = 0):
        return (
            Bip44.FromSeed(self._seed, coin).Purpose().Coin().Account(0)
            .Change(Bip44Changes.CHAIN_EXT).AddressIndex(index)
        )

    def evm_private_key(self, index: int = 0) -> bytes:
        return self._bip44_addr(Bip44Coins.ETHEREUM, index).PrivateKey().Raw().ToBytes()

    def evm_address(self, index: int = 0) -> str:
        return self._bip44_addr(Bip44Coins.ETHEREUM, index).PublicKey().ToAddress()

    def tron_private_key(self) -> bytes:
        return self._bip44_addr(Bip44Coins.TRON).PrivateKey().Raw().ToBytes()

    def tron_address(self) -> str:
        return self._bip44_addr(Bip44Coins.TRON).PublicKey().ToAddress()

    # -- UTXO chains ----------------------------------------------------------------
    def bip84_account_xprv(self, coin: str) -> str:
        """Account-level extended private key (zprv-style serialization) for BTC or LTC."""
        c = {"btc": Bip84Coins.BITCOIN, "ltc": Bip84Coins.LITECOIN}[coin]
        return Bip84.FromSeed(self._seed, c).Purpose().Coin().Account(0).PrivateKey().ToExtended()

    def bip84_key(self, coin: str, change: int, index: int) -> tuple[bytes, bytes, str]:
        """(private key, compressed public key, address) for one BIP-84 address."""
        c = {"btc": Bip84Coins.BITCOIN, "ltc": Bip84Coins.LITECOIN}[coin]
        node = (
            Bip84.FromSeed(self._seed, c).Purpose().Coin().Account(0)
            .Change(Bip44Changes(change)).AddressIndex(index)
        )
        return (
            node.PrivateKey().Raw().ToBytes(),
            node.PublicKey().RawCompressed().ToBytes(),
            node.PublicKey().ToAddress(),
        )

    # -- ed25519 chains -------------------------------------------------------------
    def solana_key(self) -> Ed25519Key:
        node = Bip44.FromSeed(self._seed, Bip44Coins.SOLANA).Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT)
        return Ed25519Key(node.PrivateKey().Raw().ToBytes(), node.PublicKey().RawCompressed().ToBytes()[1:])

    def ton_key(self) -> Ed25519Key:
        node = Bip44.FromSeed(self._seed, Bip44Coins.TON).Purpose().Coin().Account(0)
        return Ed25519Key(node.PrivateKey().Raw().ToBytes(), node.PublicKey().RawCompressed().ToBytes()[1:])

    def ton_address_reference(self) -> str:
        """bip-utils' own TON v4 address encoding, used to cross-check tonsdk."""
        return Bip44.FromSeed(self._seed, Bip44Coins.TON).Purpose().Coin().Account(0).PublicKey().ToAddress()

    def monero_keys(self) -> MoneroKeys:
        node = Bip44.FromSeed(self._seed, Bip44Coins.MONERO_ED25519_SLIP).Purpose().Coin().Account(0)
        m = Monero.FromBip44PrivateKey(node.PrivateKey().Raw().ToBytes())
        return MoneroKeys(
            address=str(m.PrimaryAddress()),
            private_spend=m.PrivateSpendKey().Raw().ToHex(),
            private_view=m.PrivateViewKey().Raw().ToHex(),
        )

    def derived_secret(self, label: str) -> str:
        """Deterministic per-purpose secret (e.g. the Monero wallet-file password)."""
        return hmac.new(self._seed, b"opencustody:" + label.encode(), hashlib.sha256).hexdigest()
