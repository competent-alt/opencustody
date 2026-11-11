"""Network endpoints and token lists. Users can override any of it in config.json.

Public endpoints see your IP address and which addresses you query. For
privacy, point these at your own nodes.
"""

from __future__ import annotations

import copy
import json

from .paths import atomic_write, config_path

DEFAULTS: dict = {
    "auto_lock_minutes": 10,
    "prices": {"enabled": True, "api": "https://api.coingecko.com/api/v3"},
    # Optional free key from etherscan.io: enables transaction history on every EVM chain.
    "etherscan_api_key": "",
    "bitcoin": {"esplora": "https://mempool.space/api", "gap_limit": 10,
                "explorer": "https://mempool.space"},
    "litecoin": {"esplora": "https://litecoinspace.org/api", "gap_limit": 10,
                 "explorer": "https://litecoinspace.org"},
    "evm": [
        {"key": "eth", "name": "Ethereum", "chain_id": 1, "symbol": "ETH",
         "rpc": "https://ethereum-rpc.publicnode.com", "explorer": "https://etherscan.io",
         "history_api": "https://eth.blockscout.com/api",
         "tokens": [
             {"symbol": "USDC", "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"},
             {"symbol": "USDT", "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7"},
         ]},
        {"key": "base", "name": "Base", "chain_id": 8453, "symbol": "ETH",
         "rpc": "https://base-rpc.publicnode.com", "explorer": "https://basescan.org",
         "history_api": "",  # set etherscan_api_key for history
         "tokens": [{"symbol": "USDC", "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"}]},
        {"key": "arb", "name": "Arbitrum One", "chain_id": 42161, "symbol": "ETH",
         "rpc": "https://arbitrum-one-rpc.publicnode.com", "explorer": "https://arbiscan.io",
         "history_api": "",  # set etherscan_api_key for history
         "tokens": [{"symbol": "USDC", "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"}]},
        {"key": "op", "name": "Optimism", "chain_id": 10, "symbol": "ETH",
         "rpc": "https://optimism-rpc.publicnode.com", "explorer": "https://optimistic.etherscan.io",
         "history_api": "https://optimism.blockscout.com/api",
         "tokens": [{"symbol": "USDC", "address": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85"}]},
        {"key": "polygon", "name": "Polygon", "chain_id": 137, "symbol": "POL",
         "rpc": "https://polygon-bor-rpc.publicnode.com", "explorer": "https://polygonscan.com",
         "history_api": "",  # set etherscan_api_key for history
         "tokens": [{"symbol": "USDC", "address": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"}]},
        {"key": "bsc", "name": "BNB Smart Chain", "chain_id": 56, "symbol": "BNB",
         "rpc": "https://bsc-rpc.publicnode.com", "explorer": "https://bscscan.com",
         "history_api": "",  # no free indexer; set an Etherscan-compatible API URL (with apikey) to enable
         "tokens": [{"symbol": "USDT", "address": "0x55d398326f99059fF775485246999027B3197955"}]},
    ],
    "solana": {"rpc": "https://api.mainnet-beta.solana.com", "explorer": "https://solscan.io"},
    "tron": {"api": "https://api.trongrid.io", "explorer": "https://tronscan.org/#",
             "trc20_fee_limit_trx": 50,
             "tokens": [{"symbol": "USDT", "address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"}]},
    "ton": {"api": "https://toncenter.com/api/v2", "api_key": "", "explorer": "https://tonviewer.com"},
    "monero": {
        "enabled": False,
        "wallet_rpc_path": "",
        "daemon": "",
        "restore_height": 0,
        "explorer": "https://xmrchain.net",
    },
}


def _merge(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = _merge(base.get(k), v) if k in base else v
        return out
    return copy.deepcopy(override)


def load() -> dict:
    p = config_path()
    if p.exists():
        return _merge(copy.deepcopy(DEFAULTS), json.loads(p.read_text("utf-8")))
    return copy.deepcopy(DEFAULTS)


def save(cfg: dict) -> None:
    atomic_write(config_path(), json.dumps(cfg, indent=2).encode())


def ensure_file() -> None:
    if not config_path().exists():
        save(DEFAULTS)
