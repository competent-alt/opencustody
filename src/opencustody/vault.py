"""Encrypted storage for the recovery phrase.

The phrase is encrypted with AES-256-GCM. The key comes from the user's
password through scrypt (N=2^17, r=8, p=1), so each password guess costs an
attacker about 128 MiB of memory. The file header is bound to the ciphertext
as associated data, so tampering with the KDF parameters makes decryption fail.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .paths import atomic_write

VERSION = 1
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
MIN_PASSWORD_LEN = 10


class VaultError(Exception):
    pass


class WrongPassword(VaultError):
    pass


@dataclass
class Secrets:
    mnemonic: str
    passphrase: str = ""


def _kdf(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise VaultError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")


def save(path: Path, secrets: Secrets, password: str, metadata: dict | None = None) -> None:
    check_password_strength(password)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    header = {
        "version": VERSION,
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt": _b64(salt)},
        "cipher": {"name": "aes-256-gcm", "nonce": _b64(nonce)},
        "metadata": metadata or {},
    }
    aad = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    key = _kdf(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    plaintext = json.dumps({"mnemonic": secrets.mnemonic, "passphrase": secrets.passphrase}).encode()
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    doc = dict(header, ciphertext=_b64(ct))
    atomic_write(path, json.dumps(doc, indent=2).encode())


def read_metadata(path: Path) -> dict:
    return json.loads(path.read_text("utf-8")).get("metadata", {})


def load(path: Path, password: str) -> Secrets:
    try:
        doc = json.loads(path.read_text("utf-8"))
        if doc.get("version") != VERSION:
            raise VaultError("Unsupported vault version.")
        kdf, cipher = doc["kdf"], doc["cipher"]
        if kdf["name"] != "scrypt" or cipher["name"] != "aes-256-gcm":
            raise VaultError("Unsupported vault algorithms.")
        # Refuse weakened parameters even though the header is authenticated:
        # an attacker who swaps the file could otherwise phish a cheap-to-crack password.
        if kdf["n"] < SCRYPT_N or kdf["r"] < SCRYPT_R or kdf["p"] < SCRYPT_P:
            raise VaultError("Vault KDF parameters are weaker than allowed.")
        header = {k: doc[k] for k in ("version", "kdf", "cipher", "metadata")}
        aad = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        key = _kdf(password, base64.b64decode(kdf["salt"]), kdf["n"], kdf["r"], kdf["p"])
        pt = AESGCM(key).decrypt(base64.b64decode(cipher["nonce"]), base64.b64decode(doc["ciphertext"]), aad)
    except InvalidTag:
        raise WrongPassword("Wrong password (or the vault file was modified).") from None
    except (KeyError, ValueError, TypeError) as e:
        raise VaultError(f"Vault file is corrupt: {e}") from None
    data = json.loads(pt)
    return Secrets(mnemonic=data["mnemonic"], passphrase=data.get("passphrase", ""))


def update_metadata(path: Path, password: str, metadata: dict) -> None:
    """Re-encrypt with new metadata (a fresh salt and nonce are generated)."""
    secrets = load(path, password)
    merged = dict(read_metadata(path), **metadata)
    save(path, secrets, password, merged)
