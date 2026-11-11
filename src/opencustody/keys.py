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

    def seed(self) -> bytes:
        if not self._seed:
            raise RuntimeError("Wallet is locked.")
        return self._seed

    def portfolio(self, n: int) -> "PortfolioKeys":
        """Keys for portfolio n: a separate, unlinkable set of addresses from the same phrase."""
        return PortfolioKeys(self, n)

    def derived_secret(self, label: str) -> str:
        """Deterministic per-purpose secret (e.g. the Monero wallet-file password)."""
        return hmac.new(self.seed(), b"opencustody:" + label.encode(), hashlib.sha256).hexdigest()

    # Portfolio 0 shortcuts (the default wallet).
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(PortfolioKeys(self, 0), name)


MAX_PORTFOLIOS = 100


class PortfolioKeys:
    """Derivations for one portfolio. Portfolio n uses the index each wallet app uses for
    'account n' (see the README table), so portfolio 0 is the classic single wallet."""

    def __init__(self, keyring: Keyring, n: int):
        if not 0 <= int(n) < MAX_PORTFOLIOS:
            raise ValueError("Portfolio number out of range.")
        self._kr = keyring
        self.n = int(n)

    def derived_secret(self, label: str) -> str:
        return self._kr.derived_secret(f"{label}:{self.n}" if self.n else label)

    # -- secp256k1 account chains -------------------------------------------------
    def _evm_node(self):
        # MetaMask-style: account n = address index n.
        return (Bip44.FromSeed(self._kr.seed(), Bip44Coins.ETHEREUM).Purpose().Coin().Account(0)
                .Change(Bip44Changes.CHAIN_EXT).AddressIndex(self.n))

    def evm_private_key(self) -> bytes:
        return self._evm_node().PrivateKey().Raw().ToBytes()

    def evm_address(self) -> str:
        return self._evm_node().PublicKey().ToAddress()

    def _tron_node(self):
        # Ledger-style: account n = m/44'/195'/n'/0/0.
        return (Bip44.FromSeed(self._kr.seed(), Bip44Coins.TRON).Purpose().Coin().Account(self.n)
                .Change(Bip44Changes.CHAIN_EXT).AddressIndex(0))

    def tron_private_key(self) -> bytes:
        return self._tron_node().PrivateKey().Raw().ToBytes()

    def tron_address(self) -> str:
        return self._tron_node().PublicKey().ToAddress()

    # -- UTXO chains ----------------------------------------------------------------
    def _bip84_account(self, coin: str):
        c = {"btc": Bip84Coins.BITCOIN, "ltc": Bip84Coins.LITECOIN}[coin]
        return Bip84.FromSeed(self._kr.seed(), c).Purpose().Coin().Account(self.n)

    def bip84_account_xprv(self, coin: str) -> str:
        """Account-level extended private key (zprv-style serialization) for BTC or LTC."""
        return self._bip84_account(coin).PrivateKey().ToExtended()

    def bip84_key(self, coin: str, change: int, index: int) -> tuple[bytes, bytes, str]:
        """(private key, compressed public key, address) for one BIP-84 address."""
        node = self._bip84_account(coin).Change(Bip44Changes(change)).AddressIndex(index)
        return (
            node.PrivateKey().Raw().ToBytes(),
            node.PublicKey().RawCompressed().ToBytes(),
            node.PublicKey().ToAddress(),
        )

    # -- ed25519 chains -------------------------------------------------------------
    def solana_key(self) -> Ed25519Key:
        node = (Bip44.FromSeed(self._kr.seed(), Bip44Coins.SOLANA).Purpose().Coin().Account(self.n)
                .Change(Bip44Changes.CHAIN_EXT))
        return Ed25519Key(node.PrivateKey().Raw().ToBytes(), node.PublicKey().RawCompressed().ToBytes()[1:])

    def _ton_node(self):
        return Bip44.FromSeed(self._kr.seed(), Bip44Coins.TON).Purpose().Coin().Account(self.n)

    def ton_key(self) -> Ed25519Key:
        node = self._ton_node()
        return Ed25519Key(node.PrivateKey().Raw().ToBytes(), node.PublicKey().RawCompressed().ToBytes()[1:])

    def ton_address_reference(self) -> str:
        """bip-utils' own TON v4 address encoding, used to cross-check tonsdk."""
        return self._ton_node().PublicKey().ToAddress()

    def monero_keys(self) -> MoneroKeys:
        node = Bip44.FromSeed(self._kr.seed(), Bip44Coins.MONERO_ED25519_SLIP).Purpose().Coin().Account(self.n)
        m = Monero.FromBip44PrivateKey(node.PrivateKey().Raw().ToBytes())
        return MoneroKeys(
            address=str(m.PrimaryAddress()),
            private_spend=m.PrivateSpendKey().Raw().ToHex(),
            private_view=m.PrivateViewKey().Raw().ToHex(),
        )
