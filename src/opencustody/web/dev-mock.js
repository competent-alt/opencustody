'use strict';
/* Fake bridge for UI development in a normal browser (index.html?mock).
   Never loaded in the real app, where window.pywebview.api exists. Holds no keys. */
(() => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const ok = o => ({ ok: true, ...o });
  const params = new URLSearchParams(location.search);
  let unlocked = params.has('unlocked'), vault = !params.has('fresh');
  const WORDS = 'abandon ability able about above absent absorb abstract absurd abuse access accident account accuse achieve acid acoustic acquire across act action actor actress actual'.split(' ');
  const A = [
    ['btc:0', 'Bitcoin', 'BTC', 'BTC', null, 'bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu', true, '0.04812'],
    ['ltc:0', 'Litecoin', 'LTC', 'LTC', null, 'ltc1qjmxnz78nmc8nq77wuxh25n2es7rzm5c2rkk4wh', true, '3.1'],
    ['eth:0', 'Ethereum', 'ETH', 'ETH', null, '0x9858EfFD232B4033E47d90003D41EC34EcaEda94', false, '0.7342'],
    ['eth:1', 'Ethereum', 'USDC', 'ETH', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', '0x9858EfFD232B4033E47d90003D41EC34EcaEda94', false, '1250'],
    ['base:0', 'Base', 'ETH', 'ETH', null, '0x9858EfFD232B4033E47d90003D41EC34EcaEda94', false, '0.0151'],
    ['sol:0', 'Solana', 'SOL', 'SOL', null, 'HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk', false, '2.563239079'],
    ['trx:0', 'Tron', 'TRX', 'TRX', null, 'TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH', false, '0'],
    ['trx:1', 'Tron', 'USDT', 'TRX', 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t', 'TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH', false, '8248.535785'],
    ['ton:0', 'TON', 'TON', 'TON', null, 'UQAzWZa6nM5mJev91wGc7VCSfBoIsYRqKJpV78N8Add9-RKY', false, '0'],
  ].map(([id, network, symbol, native, token, address, max, bal]) => ({ id, network, symbol, native, token, address, max, bal }));
  window.MockApi = {
    state: async () => ok({ version: 'mock', vault_exists: vault, unlocked }),
    ping: async () => ok(),
    create_mnemonic: async () => ok({ words: WORDS }),
    verify_challenge: async () => ok({ positions: [3, 11, 20] }),
    verify_words: async () => ok(),
    restore_mnemonic: async t => t.trim().split(/\s+/).length >= 12 ? ok() : { ok: false, error: 'That is not a valid BIP-39 phrase.' },
    save_wallet: async (a, b) => { await wait(700); if (a !== b) return { ok: false, error: "Passwords don't match." }; vault = unlocked = true; return ok(); },
    unlock: async pw => { await wait(600); if (pw !== 'password123') return { ok: false, error: 'Wrong password (mock password is password123).' }; unlocked = true; return ok(); },
    lock: async () => { unlocked = false; return ok(); },
    accounts: async () => ok({ accounts: A.map(({ bal, ...a }) => a), notes: [], monero: 'off', monero_error: '' }),
    balance: async id => { await wait(300 + Math.random() * 900); return ok({ balance: A.find(a => a.id === id).bal }); },
    start_monero: async () => ({ ok: false, error: 'Monero is off.' }),
    validate: async (id, to) => ok({ valid: to.length > 25, error: to.length > 25 ? '' : 'Not a valid address.' }),
    prepare_send: async (id, to, amount, max) => {
      await wait(800); const a = A.find(x => x.id === id);
      return ok({ token: 't', warnings: a.token ? [] : ['Recipient address has never been used.'], rows: [
        ['Network', a.network], ['From', a.address], ['To', to], ['Amount', `${max ? a.bal : amount} ${a.symbol}`],
        ['Network fee', `0.000021 ${a.native} (maximum; usually less)`], ['Total', `${max ? a.bal : amount} ${a.native}`]] });
    },
    broadcast: async () => { await wait(700); return ok({ txid: '5f2c0d8e9a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d', url: 'https://example.com' }); },
    cancel_send: async () => ok(),
    explorer: async () => ok(),
    open_url: async () => ok(),
    settings: async () => ok({ config_path: 'C:\\Users\\you\\AppData\\Roaming\\OpenCustody\\config.json', vault_path: 'C:\\Users\\you\\AppData\\Roaming\\OpenCustody\\vault.json', auto_lock: 10, monero_enabled: false, monero_rpc: '', monero_daemon: '', monero_state: 'off', monero_error: '' }),
    open_config: async () => ok(),
  };
})();
