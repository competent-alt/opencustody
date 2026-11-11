# Security

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting
(Security tab, then "Report a vulnerability"). We aim to reply within 72 hours.

## Honest status

No software can promise "zero vulnerabilities", and this project does not. What it does:

* Automated checks on every commit: unit tests with published derivation vectors, `bandit`
  static analysis, `pip-audit` against known-CVE databases, and GitHub CodeQL.
* No custom cryptography. Every primitive comes from widely used libraries.
* It has **not** had an independent professional audit yet. Until then, treat it as alpha
  software and don't store more than you can afford to lose.

## Threat model

**Protected against:**
* Someone copying your vault file. The recovery phrase is encrypted with AES-256-GCM using a key
  from scrypt (N=2^17, r=8, p=1, about 128 MiB per guess), and the header is authenticated.
  Use a strong password.
* A malicious or compromised RPC/API server altering your transactions. Every transaction is
  built and signed **locally**. Tron transactions are protobuf-encoded locally rather than
  accepting server-built ones. The EVM chain ID is checked against the RPC. Recipient addresses
  are validated per network (checksums, mainnet prefixes).
* Leaving the wallet open: it auto-locks after inactivity (default 10 minutes).

**Not protected against:**
* Malware on your computer (keyloggers, clipboard hijackers, screen capture). Use a clean
  machine, compare addresses character by character, and consider a hardware wallet for large amounts.
* Physical access to an *unlocked* wallet.
* Memory forensics. Python cannot reliably wipe secrets from RAM.
* Privacy leaks to public API providers (see the README's Privacy section).
* Losing your recovery phrase. Nobody can recover it for you.

## User interface

The window is an HTML page rendered by the operating system's web view (via `pywebview`).
* The page loads only bundled local files over a private 127.0.0.1 port and runs under a strict
  Content-Security-Policy: no inline scripts, no `eval`, and `connect-src 'none'`, so the UI itself
  cannot make network requests. Every network call happens in Python.
* The page can only call the methods of `WalletApi` (`webapi.py`). It never receives the seed or
  private keys. It only sees addresses, balances, and the words while you are creating a backup.
* All data is inserted into the page with `textContent`, never as HTML.
* Signed transactions stay in Python behind a single-use random token. A review can be sent only
  once, and locking the wallet discards it.
* Private mode: the web view persists no cookies, cache or storage. Dev tools are disabled.

## Cryptography and dependencies

| Purpose | Library |
|---|---|
| BIP-39/32/44/84, SLIP-10, Monero key derivation | `bip_utils` (secp256k1 via `coincurve`/libsecp256k1) |
| secp256k1 signing (BTC, LTC, EVM, Tron) | `coincurve` (libsecp256k1) via `eth-account`/`eth-keys` and directly |
| Bitcoin/Litecoin transaction serialization and sighash | `embit` |
| Solana transactions, ed25519 | `solders` (Rust) |
| TON wallet v4r2 messages, ed25519 | `tonsdk` / PyNaCl (libsodium) |
| Monero transactions | official `monero-wallet-rpc` (external binary) |
| Vault encryption | `cryptography` (OpenSSL) |

All runtime dependencies are pinned to exact versions in `pyproject.toml`.

### Known advisory: PYSEC-2026-1325 (python-ecdsa, no fix available)

`bip_utils` depends on `python-ecdsa`, which has a timing side channel (Minerva) in **P-256**
signing and key generation. The upstream project will not fix it. OpenCustody never uses P-256,
and all secp256k1 operations go through libsecp256k1 (`coincurve`).
`tests/test_chains.py::test_secp256k1_uses_libsecp256k1` enforces this, and CI ignores only
this one advisory ID, with this justification.

### Monero-specific notes

The spend key is passed to a local `monero-wallet-rpc` process bound to 127.0.0.1 with
random digest-auth credentials. Other processes running as your user could observe it.
The wallet file it writes is encrypted with a password derived from your seed.
Use a node you trust; a malicious node can't steal funds, but it can harm your privacy.
