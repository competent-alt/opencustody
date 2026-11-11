"""Chain registry."""

from __future__ import annotations

from .base import Chain


def build_chains(cfg: dict, keyring) ->tuple[list[Chain], list[str]]:
    """Create every chain. Returns (chains, errors) so one bad config doesn't block the rest."""
    from .evm import EvmChain
    from .solana import SolanaChain
    from .ton import TonChain
    from .tron import TronChain
    from .utxo import UtxoChain

    chains: list[Chain] = []
    errors: list[str] = []
    makers = [
        ("Bitcoin", lambda: UtxoChain("btc", cfg["bitcoin"], keyring)),
        ("Litecoin", lambda: UtxoChain("ltc", cfg["litecoin"], keyring)),
        *[(e["name"], (lambda e=e: EvmChain(e, keyring))) for e in cfg["evm"]],
        ("Solana", lambda: SolanaChain(cfg["solana"], keyring)),
        ("Tron", lambda: TronChain(cfg["tron"], keyring)),
        ("TON", lambda: TonChain(cfg["ton"], keyring)),
    ]
    for name, make in makers:
        try:
            chains.append(make())
        except Exception as e:
            errors.append(f"{name}: {e}")
    return chains, errors


def build_monero(cfg: dict, keyring, restore_height: int) -> Chain:
    from .monero import MoneroChain
    return MoneroChain(cfg["monero"], keyring, restore_height)
