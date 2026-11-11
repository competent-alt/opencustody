# OpenCustody

An open-source, self-custodial desktop wallet for **Bitcoin, Litecoin, Ethereum and EVM chains
(Base, Arbitrum, Optimism, Polygon, BNB Chain), Solana, Tron, TON and Monero**, all from one
24-word recovery phrase.

* **Your keys, your coins.** Keys are generated and stored only on your computer, encrypted with
  your password. There is no server, account, telemetry or analytics.
* **Standard derivation paths.** Your phrase restores the same addresses in MetaMask, Phantom,
  TronLink, Sparrow/Electrum (BIP-84), Ledger and others (see [`keys.py`](src/opencustody/keys.py)).
* **Small, readable codebase** (~1,700 lines) built on well-known cryptography libraries.
  There are no hand-rolled curve implementations.

> ⚠️ **Status: alpha, unaudited.** Read [SECURITY.md](SECURITY.md) before you store real value.
> Try it with small amounts first.

## Features

* **Home screen:** total portfolio value with a pie wheel of your holdings. Hover a slice to see its
  value and share, click it to open that asset.
* **Portfolios:** many separate wallets from the same recovery phrase (e.g. "Main", "Savings",
  "Business"). Each has its own addresses on every network, with no on-chain link between them,
  and your one backup restores them all.
* **History:** recent sent and received transactions across all assets, filterable, with links
  to block explorers.
* Send, receive and per-network address checks, auto-lock, and an encrypted vault.

## Supported assets

| Network | Native coin | Tokens | Send-all |
|---|---|---|---|
| Bitcoin | BTC (native SegWit, RBF on) | – | ✓ |
| Litecoin | LTC (native SegWit) | – | ✓ |
| Ethereum, Base, Arbitrum, Optimism, Polygon, BNB Chain | ETH / POL / BNB | ERC-20 (USDC, USDT; add more in config) | – |
| Solana | SOL | – (SPL planned) | – |
| Tron | TRX | TRC-20 (USDT) | – |
| TON | TON (wallet v4r2) | – (Jettons planned) | – |
| Monero | XMR (via official `monero-wallet-rpc`) | – | ✓ |

## Install & run

Requires Python 3.11+ and the system web view: Edge WebView2 (preinstalled on Windows 10/11),
WebKit on macOS (built in), or GTK WebKit2 / Qt WebEngine on Linux (see the
[pywebview docs](https://pywebview.flowrl.com/guide/installation.html)).

```bash
git clone https://github.com/<you>/opencustody.git
```

```bash
cd opencustody
```

```bash
python -m venv .venv
```

Activate it (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` elsewhere), then:

```bash
pip install -e .
```

```bash
opencustody
```

(or `python -m opencustody`)

### Enabling Monero

Monero transactions are built and signed by the **official Monero wallet**, not by code in this
repository.

1. Download the Monero CLI from <https://www.getmonero.org/downloads/> and **verify its hash/signature**.
2. In OpenCustody choose **Settings**, which opens `config.json`, and set:
   ```json
   "monero": { "enabled": true,
               "wallet_rpc_path": "C:/monero/monero-wallet-rpc.exe",
               "daemon": "your-node.example:18081",
               "restore_height": 0 }
   ```
3. Lock and unlock the wallet. If you created the wallet in OpenCustody, the restore height is
   worked out automatically. If you restored an older phrase, set `restore_height` to a block
   from before your first transaction (`0` scans the whole chain, which is slow).

## Privacy

By default the app talks to public endpoints (mempool.space, litecoinspace.org, publicnode.com,
api.mainnet-beta.solana.com, trongrid.io, toncenter.com). Those services can see your IP address
and which addresses belong to you. For privacy, point `config.json` at your own nodes (Esplora/electrs,
an Ethereum node, and so on) and/or use Tor/VPN. All endpoints must be HTTPS (or localhost).

## Recovery phrase compatibility

Portfolio *n* (0 = Main) uses these paths, matching what other wallets call "account *n*+1":

| Network | Path for portfolio n | Compatible with |
|---|---|---|
| Bitcoin | m/84'/0'/n' | Sparrow, Electrum (BIP-84), Ledger, Trezor |
| Litecoin | m/84'/2'/n' | Ledger, Trezor |
| EVM | m/44'/60'/0'/0/n | MetaMask (account n+1), Rabby |
| Solana | m/44'/501'/n'/0' | Phantom, Solflare |
| Tron | m/44'/195'/n'/0/0 | Ledger; TronLink for n = 0 |
| TON | m/44'/607'/n' (v4r2) | Ledger TON app. **Not** Tonkeeper's TON-native 24 words |
| Monero | m/44'/128'/n' (SLIP-10) | Ledger-style derivation. **Not** a native 25-word Monero seed |

Portfolio names are stored unencrypted in `portfolios.json`; they are labels only. If you restore
on a new computer, add portfolios again: numbers are reused in order, so the same addresses come back.

## Prices and history

* **Prices** come from CoinGecko's public API, which sees which coins are listed but never your
  addresses. Turn them off with `"prices": {"enabled": false}`.
* **History** uses each network's public indexer: Esplora (BTC, LTC), Blockscout (Ethereum,
  Optimism), Solana RPC, TronGrid, toncenter, and your local `monero-wallet-rpc`. These services
  see the addresses you query, like the balance APIs do. Base, Arbitrum, Polygon and BNB Chain need
  a free [Etherscan API key](https://etherscan.io/apis): set `"etherscan_api_key"` in `config.json`
  and it covers every EVM chain.

## Development

```bash
pip install -e ".[dev]"
```

```bash
pytest
```

```bash
bandit -r src -c pyproject.toml
```

```bash
pip-audit
```

To work on the interface without Python or keys, serve `src/opencustody/web/` with any static
server and open `index.html?mock` (`?mock&unlocked` skips to the wallet). That loads
`dev-mock.js`, a fake bridge with sample data. The real app never loads it.

CI runs these on every push (see `.github/workflows/ci.yml`), along with CodeQL.

## Roadmap

SPL and Jetton tokens, hardware-wallet signing, QR codes, multiple accounts, coin control,
reproducible signed release builds (PyInstaller), and an independent security audit.

## License

MIT. See [LICENSE](LICENSE). No warranty: you are responsible for your keys and transactions.
