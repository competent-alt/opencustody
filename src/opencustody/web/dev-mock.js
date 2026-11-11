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
  const priv = { tor_enabled: false, tor_proxy: '127.0.0.1:9150', mode: false, hide_balances: false, monero_enabled: false,
    monero_state: 'off', monero_rpc: '', monero_daemon: '', monero_restore_height: 0 };
  const pf = { active: 0, items: [{ n: 0, name: 'Main' }, { n: 1, name: 'Savings' }] };
  const PRICES = { BTC: { usd: 74239, change24h: 1.8 }, LTC: { usd: 51.28, change24h: -0.6 }, ETH: { usd: 2078, change24h: 2.4 },
    SOL: { usd: 84.85, change24h: -1.2 }, TRX: { usd: 0.3163, change24h: 0.3 }, TON: { usd: 1.21, change24h: -2.1 },
    USDC: { usd: 1, change24h: 0 }, USDT: { usd: 1, change24h: 0 }, XMR: { usd: 589.53, change24h: 0.9 } };
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
    accounts: async () => ok({ accounts: A.map(({ bal, ...a }) => ({ ...a, coin_control: a.max })), notes: [], monero: 'off', monero_error: '' }),
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
    privacy_status: async () => ok({ ...priv, prices_enabled: !priv.mode, endpoints: [
      { network: 'Bitcoin', host: 'mempool.space', own: false }, { network: 'Litecoin', host: 'litecoinspace.org', own: false },
      { network: 'Ethereum', host: 'my-node.local', own: true }, { network: 'Solana', host: 'api.mainnet-beta.solana.com', own: false },
      { network: 'Tron', host: 'api.trongrid.io', own: false }, { network: 'TON', host: 'toncenter.com', own: false }] }),
    set_privacy: async o => {
      if ('tor_enabled' in o) priv.tor_enabled = o.tor_enabled; if ('tor_proxy' in o) priv.tor_proxy = o.tor_proxy;
      if ('mode' in o) priv.mode = o.mode; if ('hide_balances' in o) priv.hide_balances = o.hide_balances;
      return ok({ reloaded: 'tor_enabled' in o });
    },
    test_tor: async () => { await wait(900); return priv.tor_proxy.endsWith('9150') ? ok({ is_tor: true, ip: '185.220.101.4' }) : { ok: false, error: `No connection through ${priv.tor_proxy}: Can't reach the Tor/SOCKS proxy. Is Tor running?` }; },
    coins: async id => { await wait(400); return ok({ symbol: 'BTC', coins: [
      { id: 'aa:0', txid: 'aa11bb22cc33', vout: 0, amount: '0.03', address: 'bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu', change: false },
      { id: 'bb:1', txid: 'bb44cc55dd66', vout: 1, amount: '0.01512', address: 'bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g', change: true },
      { id: 'cc:0', txid: 'cc77dd88ee99', vout: 0, amount: '0.003', address: 'bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu', change: false }] }); },
    monero_setup: async (path, daemon, h, en) => { if (en && !/monero-wallet-rpc/i.test(path)) return { ok: false, error: 'Choose the monero-wallet-rpc program from the official Monero CLI download.' }; priv.monero_enabled = en; return ok(); },
    pick_file: async () => ok({ path: 'C:\\monero\\monero-wallet-rpc.exe' }),
    portfolios: async () => ok({ active: pf.active, items: pf.items }),
    switch_portfolio: async n => { pf.active = n; return ok(); },
    create_portfolio: async name => { const n = pf.items.length; pf.items.push({ n, name }); pf.active = n; return ok({ n }); },
    rename_portfolio: async (n, name) => { pf.items.find(i => i.n === n).name = name; return ok(); },
    remove_portfolio: async n => { pf.items = pf.items.filter(i => i.n !== n); pf.active = 0; return ok(); },
    prices: async () => { await wait(300); return ok({ enabled: true, prices: PRICES }); },
    history: async id => {
      await wait(200 + Math.random() * 800);
      const a = A.find(x => x.id === id);
      if (id === 'base:0') return ok({ items: [], unavailable: 'History for Base needs a free Etherscan API key.' });
      const now = Date.now() / 1000, items = [];
      for (let i = 0; i < 4; i++) {
        const dir = (i + id.length) % 3 === 0 ? 'out' : 'in';
        items.push({ id, txid: id + i, time: i === 0 && id === 'sol:0' ? null : Math.floor(now - (i * 3 + id.length) * 36000), dir,
          amount: (parseFloat(a.bal || '1') / (i + 3)).toFixed(4).replace(/\.?0+$/, ''), symbol: a.symbol, network: a.network,
          counterparty: dir === 'in' ? 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh' : '0x5350cf0d1279c9e5b45a3d3a3e8c1b2f7a6d9e0c',
          status: i === 0 && id === 'sol:0' ? 'pending' : 'confirmed', url: 'https://example.com' });
      }
      return ok({ items });
    },
  };
})();
