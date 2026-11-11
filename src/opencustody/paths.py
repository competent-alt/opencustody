"""Locations of the wallet's files on disk."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("OPENCUSTODY_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / "OpenCustody"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "OpenCustody"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "opencustody"
    base.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(base, 0o700)
    return base


def vault_path() -> Path:
    return data_dir() / "vault.json"


def config_path() -> Path:
    return data_dir() / "config.json"


def monero_dir() -> Path:
    p = data_dir() / "monero"
    p.mkdir(exist_ok=True)
    return p


def atomic_write(path: Path, data: bytes) -> None:
    """Write a file so that a crash never leaves a half-written vault behind."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
