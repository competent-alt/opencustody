'use strict';
/* OpenCustody UI. Talks only to the Python bridge (window.pywebview.api).
   All data from Python is inserted with textContent, never innerHTML. */

const $ = s => document.querySelector(s);
const SVGNS = 'http://www.w3.org/2000/svg';

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
function icon(name, cls = 'i') {
  const s = document.createElementNS(SVGNS, 'svg');
  s.setAttribute('class', cls);
  const u = document.createElementNS(SVGNS, 'use');
  u.setAttribute('href', '#' + name);
  s.append(u);
  return s;
}
const thinking = sm => h('span', { class: 'thinking' + (sm ? ' sm' : '') }, h('i'), h('i'), h('i'));

let tt;
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(tt); tt = setTimeout(() => t.classList.remove('show'), 1800);
}

/* ---------------- Bridge ---------------- */
let API = null;
async function call(name, ...args) {
  const r = await API[name](...args);
  if (!r || !r.ok) throw new Error((r && r.error) || 'Something went wrong.');
  return r;
}

/* ---------------- Onboarding ---------------- */
function hero(...kids) {
  $('#app').hidden = true;
  closeModal();
  $('#onboard').hidden = false;
  $('#hero').replaceChildren(...kids.filter(Boolean));
  $('#onboard').scrollTop = 0;
  requestAnimationFrame(layoutFlow);
}
const heroLogo = () => icon('logo', 'hero-logo logo-glow');

function showWelcome() {
  hero(
    heroLogo(),
    h('h1', { text: 'Your keys. Your coins.' }),
    h('p', { text: 'A self-custodial wallet for Bitcoin, Ethereum, Solana, Monero and more. Nothing leaves this computer except transactions you sign.' }),
    h('div', { class: 'chips' },
      h('button', { class: 'chip', onclick: startCreate },
        h('b', {}, icon('i-sparkle'), 'Create a new wallet'), h('span', { text: 'Generate a fresh 24-word recovery phrase' })),
      h('button', { class: 'chip', onclick: showRestore },
        h('b', {}, icon('i-key'), 'Restore a wallet'), h('span', { text: 'Use an existing 12 or 24-word phrase' }))),
    h('div', { class: 'foot-note', text: 'Open source · MIT licensed · No accounts, no tracking' }));
}

function showUnlock(msg) {
  const input = h('input', { type: 'password', placeholder: 'Password', autocomplete: 'current-password' });
  const btn = h('button', { class: 'send', title: 'Unlock (Enter)' }, icon('i-right'));
  const err = h('div', { class: 'form-err', text: msg || '' });
  const go = async () => {
    if (!input.value) return;
    btn.disabled = true; err.replaceChildren(thinking(true), ' Decrypting…');
    const pw = input.value; input.value = '';
    try { await call('unlock', pw); enterApp(); }
    catch (e) { err.textContent = e.message; btn.disabled = false; input.focus(); }
  };
  btn.onclick = go;
  input.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  hero(heroLogo(), h('h1', { text: 'Welcome back' }),
    h('p', { text: 'Enter your password to unlock this wallet.' }),
    h('div', { class: 'composer' }, icon('i-lock'), input, btn), err);
  input.focus();
}

let pendingWords = null;
async function startCreate() {
  try { pendingWords = (await call('create_mnemonic')).words; showWords(); }
  catch (e) { toast(e.message); }
}

function showWords() {
  const reveal = h('div', { class: 'reveal blur' },
    h('div', { class: 'words' }, pendingWords.map((w, i) => h('div', { class: 'word' }, h('i', { text: i + 1 }), w))),
    h('div', { class: 'reveal-btn' }, h('button', { class: 'btn', onclick: () => { reveal.classList.remove('blur'); sync(); } },
      icon('i-eye'), 'Reveal words (make sure no one is watching)')));
  const cb = h('input', { type: 'checkbox' });
  const next = h('button', { class: 'btn primary', disabled: true, onclick: showVerify }, 'Continue', icon('i-right'));
  const sync = () => { next.disabled = !(cb.checked && !reveal.classList.contains('blur')); };
  cb.addEventListener('change', sync);
  hero(heroLogo(), h('h1', { text: 'Your recovery phrase' }),
    h('p', { class: 'warn', text: 'Write these 24 words on paper, in order, and keep them offline. Anyone with them controls your funds. Never type them into a website, photo, cloud note or chat.' }),
    h('div', { class: 'panel wide' }, reveal,
      h('label', { class: 'check-row' }, cb, 'I have written down all 24 words, in order'),
      h('div', { class: 'panel-foot' }, h('button', { class: 'btn ghost', onclick: () => { pendingWords = null; showWelcome(); } }, 'Back'), next)));
}

async function showVerify() {
  let positions;
  try { positions = (await call('verify_challenge')).positions; } catch (e) { toast(e.message); return; }
  const inputs = positions.map(() => h('input', { class: 'mono', autocomplete: 'off', spellcheck: 'false' }));
  const err = h('div', { class: 'form-err' });
  const go = async () => {
    try { await call('verify_words', inputs.map(i => i.value)); pendingWords = null; showPassword(); }
    catch (e) { err.textContent = e.message; }
  };
  inputs.forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') go(); }));
  hero(heroLogo(), h('h1', { text: 'Confirm your backup' }),
    h('p', { text: 'Enter these words from your paper backup to prove it is complete.' }),
    h('div', { class: 'panel' },
      positions.map((p, k) => h('div', { class: 'field' }, h('label', {}, `Word #${p}`), inputs[k])), err,
      h('div', { class: 'panel-foot' },
        h('button', { class: 'btn ghost', onclick: showWords }, 'Show words again'),
        h('button', { class: 'btn primary', onclick: go }, 'Confirm', icon('i-check')))));
  inputs[0].focus();
}

function showRestore() {
  const ta = h('textarea', { placeholder: 'word1 word2 word3 …', spellcheck: 'false', autocomplete: 'off' });
  const err = h('div', { class: 'form-err' });
  const go = async () => {
    try { await call('restore_mnemonic', ta.value); ta.value = ''; showPassword(); }
    catch (e) { err.textContent = e.message; }
  };
  hero(heroLogo(), h('h1', { text: 'Restore a wallet' }),
    h('p', { text: 'Enter your 12 or 24-word BIP-39 recovery phrase, separated by spaces.' }),
    h('div', { class: 'panel' },
      h('div', { class: 'field' }, h('label', {}, 'Recovery phrase'), ta), err,
      h('div', { class: 'panel-foot' },
        h('button', { class: 'btn ghost', onclick: showWelcome }, 'Back'),
        h('button', { class: 'btn primary', onclick: go }, 'Continue', icon('i-right')))));
  ta.focus();
}

function showPassword() {
  const pw1 = h('input', { type: 'password', autocomplete: 'new-password' });
  const pw2 = h('input', { type: 'password', autocomplete: 'new-password' });
  const pp = h('input', { type: 'password', autocomplete: 'off' });
  const ppField = h('div', { class: 'field', hidden: true }, h('label', {}, 'BIP-39 passphrase'), pp,
    h('div', { class: 'hint', text: 'Creates a different wallet. You need BOTH the words and this passphrase to recover.' }));
  const cb = h('input', { type: 'checkbox', onchange: () => { ppField.hidden = !cb.checked; } });
  const err = h('div', { class: 'form-err' });
  const btn = h('button', { class: 'btn primary' }, 'Create encrypted wallet', icon('i-lock'));
  btn.onclick = async () => {
    btn.disabled = true; err.replaceChildren(thinking(true), ' Encrypting (slow on purpose)…');
    try { await call('save_wallet', pw1.value, pw2.value, cb.checked ? pp.value : ''); enterApp(); }
    catch (e) { err.textContent = e.message; btn.disabled = false; }
  };
  hero(heroLogo(), h('h1', { text: 'Protect this wallet' }),
    h('p', { text: "This password encrypts the wallet on this computer. It isn't a backup: if you forget it, restore from your recovery phrase." }),
    h('div', { class: 'panel' },
      h('div', { class: 'field' }, h('label', {}, 'Password', h('em', { text: 'min. 10 characters' })), pw1),
      h('div', { class: 'field' }, h('label', {}, 'Repeat password'), pw2),
      h('label', { class: 'check-row' }, cb, 'Advanced: add a BIP-39 passphrase'), ppField, err,
      h('div', { class: 'panel-foot' }, btn)));
  pw1.focus();
}

/* ---------------- Wallet ---------------- */
const S = {
  accounts: [], bal: {}, cur: null, notes: [], monero: 'off', moneroErr: '', gen: 0,
  page: 'home', pf: { active: 0, items: [] }, prices: null, pricesOff: false, priceErr: '',
  hist: {}, histFilter: 'all', histAsset: null, hover: null,
};

async function enterApp() {
  $('#onboard').hidden = true;
  $('#app').hidden = false;
  await loadPortfolio();
}

async function loadPortfolio() {
  S.gen++;
  S.bal = {}; S.hist = {}; S.cur = null; S.histAsset = null; S.hover = null;
  await Promise.all([loadAccounts(), loadPortfolios()]);
  if (S.accounts.length) S.cur = S.accounts[0].id;
  renderSidebar(); go('home');
  refreshAll();
  if (S.monero === 'idle') startMonero();
}

async function loadAccounts() {
  const r = await call('accounts');
  S.accounts = r.accounts; S.notes = r.notes; S.monero = r.monero; S.moneroErr = r.monero_error;
}
async function loadPortfolios() {
  S.pf = await call('portfolios');
  const p = S.pf.items.find(i => i.n === S.pf.active);
  $('#pfName').textContent = p ? p.name : 'Main';
}
async function loadPrices() {
  try {
    const r = await call('prices');
    S.pricesOff = !r.enabled; S.prices = r.prices; S.priceErr = '';
  } catch (e) { S.priceErr = e.message; }
  if (S.page === 'home') scheduleHome();
}

async function startMonero() {
  S.monero = 'starting'; renderSidebar();
  try { await call('start_monero'); } catch (e) { S.moneroErr = e.message; }
  await loadAccounts(); renderSidebar(); renderPage();
  for (const a of S.accounts) if (a.network === 'Monero') refreshOne(a.id);
}

const cur = () => S.accounts.find(a => a.id === S.cur);
const byId = id => S.accounts.find(a => a.id === id);
const loading = id => S.bal[id] === undefined || S.bal[id] === '…';
function balNode(id, small) {
  return loading(id) ? thinking(small) : small ? h('span', { class: 'bal', text: S.bal[id] }) : document.createTextNode(S.bal[id]);
}
const symBadge = s => s.length > 4 ? s.slice(0, 4) : s;
const usdFmt = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const usd = v => usdFmt.format(v);
const price = sym => (S.prices && S.prices[sym]) ? S.prices[sym].usd : null;
function usdValue(a) {
  const p = price(a.symbol), b = parseFloat(S.bal[a.id]);
  return p == null || !isFinite(b) ? null : b * p;
}
const short = s => !s ? '' : s.length > 16 ? `${s.slice(0, 6)}…${s.slice(-4)}` : s;
function ago(t) {
  if (!t) return 'pending';
  const s = Date.now() / 1000 - t;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  return new Date(t * 1000).toLocaleDateString();
}

/* ---- navigation ---- */
function go(page, opts) {
  S.page = page;
  if (page === 'history') S.histAsset = opts && opts.asset || null;
  $('#navHome').classList.toggle('on', page === 'home');
  $('#navHistory').classList.toggle('on', page === 'history');
  $('#topHistory').classList.toggle('on', page === 'history');
  for (const b of document.querySelectorAll('#accts .acct')) b.classList.toggle('active', page === 'asset' && b.dataset.id === S.cur);
  renderPage();
  $('.scroller').scrollTop = 0;
}
function select(id) { S.cur = id; go('asset'); }
function renderPage() {
  if (S.page === 'home') renderHome();
  else if (S.page === 'history') renderHistory();
  else renderAsset();
}

// Update one balance in place, so clicks aren't lost to re-rendering while balances load.
function showBal(id) {
  for (const b of document.querySelectorAll('#accts .acct')) if (b.dataset.id === id) b.lastChild.replaceWith(balNode(id, true));
  const v = $('#balVal');
  if (S.page === 'asset' && id === S.cur && v) v.replaceChildren(balNode(id, false));
  if (S.page === 'home') scheduleHome();
}

/* ---- sidebar ---- */
function renderSidebar() {
  const q = $('#acctSearch').value.trim().toLowerCase();
  const nav = $('#accts'); nav.replaceChildren();
  const groups = new Map();
  for (const a of S.accounts) {
    if (q && !(a.symbol + ' ' + a.network).toLowerCase().includes(q)) continue;
    if (!groups.has(a.network)) groups.set(a.network, []);
    groups.get(a.network).push(a);
  }
  for (const [net, list] of groups) {
    nav.append(h('div', { class: 'group-label', text: net }));
    for (const a of list) {
      nav.append(h('button', { class: 'acct' + (S.page === 'asset' && a.id === S.cur ? ' active' : ''), 'data-id': a.id, onclick: () => select(a.id) },
        h('span', { class: 'sym', text: symBadge(a.symbol) }),
        h('span', { class: 'nm' }, a.symbol, a.token ? h('small', { text: 'token' }) : null),
        balNode(a.id, true)));
    }
  }
  if (!S.accounts.some(a => a.network === 'Monero') && (!q || 'monero xmr'.includes(q))) {
    nav.append(h('div', { class: 'group-label', text: 'Monero' }));
    const sub = { off: 'set up', idle: 'starting', starting: null, error: 'error', on: '' }[S.monero];
    nav.append(h('button', { class: 'acct', onclick: openSettings },
      h('span', { class: 'sym', text: 'XMR' }), h('span', { class: 'nm' }, 'XMR'),
      S.monero === 'starting' ? thinking(true) : h('span', { class: 'bal', text: sub || '' })));
  }
  if (!nav.children.length) nav.append(h('div', { class: 'empty-history', text: 'No matching assets' }));
}

/* ---- portfolio switcher ---- */
function togglePf(open) {
  const dd = $('#pfDD'), btn = $('#pfBtn');
  open = open === undefined ? !dd.classList.contains('open') : open;
  if (open) {
    const r = btn.getBoundingClientRect();
    dd.style.left = r.left + 'px'; dd.style.top = (r.bottom + 6) + 'px'; dd.style.width = Math.max(r.width, 280) + 'px';
    dd.replaceChildren(
      h('div', { class: 'menu-label', text: 'Portfolios' }),
      ...S.pf.items.map(p => h('div', { class: 'pf-opt' + (p.n === S.pf.active ? ' sel' : ''), role: 'button', onclick: () => switchPf(p.n) },
        icon('i-check', 'i check'),
        h('div', { class: 'meta' }, h('div', { class: 'nm', text: p.name }), h('div', { class: 'sub', text: `#${p.n} · account ${p.n}` })),
        h('div', { class: 'acts' },
          h('button', { class: 'icon-btn', title: 'Rename', onclick: e => { e.stopPropagation(); togglePf(false); openPfModal(p); } }, icon('i-edit')),
          p.n ? h('button', { class: 'icon-btn', title: 'Remove from list', onclick: e => { e.stopPropagation(); togglePf(false); openRemovePf(p); } }, icon('i-trash')) : null))),
      h('button', { class: 'btn-add', onclick: () => { togglePf(false); openPfModal(null); } }, icon('i-plus'), 'New portfolio'));
  }
  dd.classList.toggle('open', open); btn.classList.toggle('open', open);
}
async function switchPf(n) {
  togglePf(false);
  if (n === S.pf.active) return;
  try { await call('switch_portfolio', n); await loadPortfolio(); toast(`Switched to ${S.pf.items.find(i => i.n === n).name}`); }
  catch (e) { toast(e.message); }
}
function openPfModal(p) {
  const input = h('input', { value: p ? p.name : '', placeholder: 'e.g. Savings, Trading, Business', maxlength: '40' });
  const err = h('div', { class: 'form-err' });
  const save = h('button', { class: 'btn primary' }, p ? 'Save' : 'Create portfolio', icon('i-check'));
  const submit = async () => {
    save.disabled = true;
    try {
      if (p) { await call('rename_portfolio', p.n, input.value); closeModal(); await loadPortfolios(); }
      else { await call('create_portfolio', input.value); closeModal(); await loadPortfolio(); toast('Portfolio created'); }
    } catch (e) { err.textContent = e.message; save.disabled = false; }
  };
  save.onclick = submit;
  input.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
  const next = S.pf.items.reduce((m, i) => i.n === m ? m + 1 : m, 0);
  openModal(mHead(p ? 'i-edit' : 'i-plus', p ? 'Rename portfolio' : 'New portfolio'),
    h('div', { class: 'm-body' },
      h('div', { class: 'field' }, h('label', {}, 'Name'), input),
      p ? null : h('p', { class: 'note' },
        `This creates a separate set of addresses on every network from your same recovery phrase (portfolio #${next}). `,
        'Nothing links it on-chain to your other portfolios, and your existing backup already covers it. ',
        `In other wallets it is account ${next + 1}: MetaMask's account ${next + 1}, Phantom's wallet ${next + 1}, Ledger's account ${next + 1}.`),
      err),
    h('div', { class: 'm-foot' }, h('button', { class: 'btn', onclick: closeModal }, 'Cancel'), save));
  input.focus();
}
function openRemovePf(p) {
  const btn = h('button', { class: 'btn primary' }, 'Remove from list');
  btn.onclick = async () => {
    try { await call('remove_portfolio', p.n); closeModal(); await loadPortfolio(); } catch (e) { toast(e.message); }
  };
  openModal(mHead('i-trash', `Remove "${p.name}"?`),
    h('div', { class: 'm-body' }, h('p', { class: 'note' },
      'This only hides it here. Its funds stay on-chain and remain controlled by your recovery phrase; ',
      'adding portfolios again brings the same addresses back.')),
    h('div', { class: 'm-foot' }, h('button', { class: 'btn', onclick: closeModal }, 'Cancel'), btn));
}

/* ---- home: pie wheel ---- */
// Monochrome steps (brand is black & white). Identity never relies on shade alone:
// every slice has a labelled legend row, and hover links slice and row.
const SHADES = ['#ffffff', '#cfcfcf', '#a6a6a6', '#828282', '#636363', '#4a4a4a', '#363636'];
const OTHER_SHADE = '#2a2a2a';
let homeTimer;
function scheduleHome() { clearTimeout(homeTimer); homeTimer = setTimeout(() => { if (S.page === 'home') renderHome(); }, 150); }

function slices() {
  const rows = S.accounts.map(a => ({ a, v: usdValue(a) })).filter(r => r.v && r.v > 0).sort((x, y) => y.v - x.v);
  const total = rows.reduce((s, r) => s + r.v, 0);
  // Up to 7 slices; beyond that the smallest fold into one "Other" slice (never a generated 8th shade).
  const cut = rows.length > SHADES.length ? SHADES.length - 1 : rows.length;
  const top = rows.slice(0, cut), rest = rows.slice(cut);
  const out = top.map((r, i) => ({ key: r.a.id, a: r.a, v: r.v, color: SHADES[i], label: r.a.symbol, sub: r.a.network }));
  if (rest.length) out.push({ key: 'other', v: rest.reduce((s, r) => s + r.v, 0), color: OTHER_SHADE, label: 'Other', sub: `${rest.length} assets`, rest });
  return { total, out };
}

function wheelSvg(sl, total) {
  const R = 118, W = 30, C = 2 * Math.PI * R, GAP = sl.length > 1 ? 3 : 0;
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('viewBox', '0 0 300 300');
  const mk = (tag, attrs) => { const e = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };
  svg.append(mk('circle', { class: 'track', cx: 150, cy: 150, r: R, 'stroke-width': W }));
  const g = mk('g', { transform: 'rotate(-90 150 150)' });
  let acc = 0;
  for (const s of sl) {
    const len = total ? (s.v / total) * C : 0;
    const dash = Math.max(len - GAP, 0.5);
    const c = mk('circle', { class: 'slice', cx: 150, cy: 150, r: R, stroke: s.color, 'stroke-width': W,
      'stroke-dasharray': `${dash} ${C - dash}`, 'stroke-dashoffset': -acc, 'data-key': s.key });
    c.addEventListener('mouseenter', () => hoverSlice(s.key));
    c.addEventListener('mouseleave', () => hoverSlice(null));
    c.addEventListener('click', () => { if (s.a) select(s.a.id); });
    g.append(c);
    acc += len;
  }
  svg.append(g);
  return svg;
}

function hoverSlice(key) {
  S.hover = key;
  const wheel = $('.wheel');
  if (!wheel) return;
  wheel.classList.toggle('dim', !!key);
  for (const c of wheel.querySelectorAll('.slice')) {
    const on = c.dataset.key === key;
    c.classList.toggle('hl', on);
    c.setAttribute('stroke-width', on ? 38 : 30);
  }
  for (const r of document.querySelectorAll('.leg')) r.classList.toggle('hl', r.dataset.key === key);
  renderCenter();
}

function renderCenter() {
  const el = $('.wheel-center');
  if (!el) return;
  const { total, out } = slices();
  const s = out.find(x => x.key === S.hover);
  if (s) {
    el.replaceChildren(h('div', {}, h('div', { class: 'k', text: `${s.label} · ${s.sub}` }), h('div', { class: 'v', text: usd(s.v) }),
      h('div', { class: 's', text: `${(s.v / total * 100).toFixed(1)}%` + (s.a ? ` · ${S.bal[s.a.id]} ${s.a.symbol}` : '') })));
  } else {
    el.replaceChildren(h('div', {}, h('div', { class: 'k', text: 'Total value' }), h('div', { class: 'v', text: usd(total) }),
      h('div', { class: 's', text: change24h(out, total) })));
  }
}
function change24h(out, total) {
  let prev = 0;
  for (const a of S.accounts) {
    const v = usdValue(a), p = S.prices && S.prices[a.symbol];
    if (!v || !p || p.change24h == null) { if (v) prev += v; continue; }
    prev += v / (1 + p.change24h / 100);
  }
  if (!total || !prev) return '24h —';
  const pct = (total / prev - 1) * 100;
  return `24h ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

function renderHome() {
  if (S.page !== 'home') return;
  const p = S.pf.items.find(i => i.n === S.pf.active);
  $('#netTitle').textContent = p ? p.name : 'Home';
  const v = $('#view');
  const stillLoading = S.accounts.some(a => loading(a.id));
  const { total, out } = slices();
  let chart;
  if (S.pricesOff) {
    chart = h('div', { class: 'empty', text: 'Prices are turned off in Settings, so values and the chart are hidden. Balances are in the sidebar.' });
  } else if (!S.prices) {
    chart = h('div', { class: 'empty' }, S.priceErr ? `Couldn't load prices: ${S.priceErr}` : thinking());
  } else if (!out.length) {
    chart = h('div', { class: 'home-grid' },
      h('div', { class: 'wheel' }, wheelSvg([], 0), h('div', { class: 'wheel-center' },
        h('div', {}, h('div', { class: 'k', text: 'Total value' }), h('div', { class: 'v', text: usd(0) }),
          h('div', { class: 's' }, stillLoading ? thinking(true) : 'No funds yet')))),
      h('div', { class: 'empty' }, stillLoading ? 'Loading balances…' : 'This portfolio is empty. Pick an asset in the sidebar to see its receive address.'));
  } else {
    chart = h('div', { class: 'home-grid' },
      h('div', { class: 'wheel' }, wheelSvg(out, total), h('div', { class: 'wheel-center' })),
      h('div', { class: 'legend' }, out.map(s => h('button', {
        class: 'leg', 'data-key': s.key, title: s.a ? `Open ${s.label} on ${s.sub}` : s.rest.map(r => r.a.symbol + ' · ' + r.a.network).join(', '),
        onmouseenter: () => hoverSlice(s.key), onmouseleave: () => hoverSlice(null), onclick: () => { if (s.a) select(s.a.id); },
      },
        h('span', { class: 'sw' }), h('span', { class: 'nm' }, s.label, h('small', { text: s.a ? `${s.sub} · ${S.bal[s.a.id]} ${s.a.symbol}` : s.sub })),
        h('span', { class: 'usd' }, usd(s.v)), h('span', { class: 'pct', text: `${(s.v / total * 100).toFixed(1)}%` })))));
    // Swatch colours are set through the CSSOM (allowed by the CSP), not inline style attributes.
    chart.querySelectorAll('.leg').forEach((r, i) => { r.querySelector('.sw').style.background = out[i].color; });
  }
  v.replaceChildren(
    h('div', { class: 'home-top' }, h('div', { class: 'label', text: 'Portfolio' }),
      h('div', { class: 'balance' }, S.prices && !S.pricesOff ? usd(total) : (p ? p.name : ''),
        stillLoading && S.prices ? h('span', {}, thinking(true)) : null)),
    chart,
    h('div', { class: 'section-head' }, h('h3', { text: 'Recent activity' }),
      h('button', { class: 'link-btn', onclick: () => go('history') }, 'View all history', icon('i-right'))),
    recentList(5),
    S.prices && !S.pricesOff ? h('div', { class: 'foot-note', text: 'Prices: CoinGecko (sees only which coins are listed, never your addresses)' }) : null);
  if (S.prices && out.length) { renderCenter(); if (S.hover) hoverSlice(S.hover); }
  ensureHistory(S.accounts.filter(a => parseFloat(S.bal[a.id]) > 0).map(a => a.id));
}

/* ---- asset ---- */
function renderAsset() {
  const a = cur(), v = $('#view');
  if (!a) { go('home'); return; }
  $('#netTitle').textContent = a.network;
  const addr = a.address;
  const pre = h('pre', { class: 'sel' }, h('b', { text: addr.slice(0, 6) }), addr.slice(6, -6), h('b', { text: addr.slice(-6) }));
  const val = usdValue(a);
  v.replaceChildren(
    h('div', { class: 'acct-hero' },
      h('div', { class: 'big-sym', text: symBadge(a.symbol) }),
      h('div', { class: 'label', text: a.network + (a.token ? ' · token' : '') }),
      h('div', { class: 'balance' }, h('span', { id: 'balVal', class: 'val' }, balNode(a.id, false)), h('span', { text: a.symbol })),
      h('div', { class: 'sub', text: (val != null ? usd(val) + ' · ' : '') + (a.token ? `Token on ${a.network}` : `Native coin of ${a.network}`) })),
    h('div', { class: 'actions' },
      h('button', { class: 'btn primary lg', onclick: () => openSend(a) }, icon('i-up'), 'Send'),
      h('button', { class: 'btn lg', onclick: () => copyText(addr) }, icon('i-copy'), 'Copy address'),
      h('button', { class: 'btn lg', onclick: () => go('history', { asset: a.id }) }, icon('i-clock'), 'History'),
      h('button', { class: 'btn lg', onclick: () => API.explorer(a.id) }, icon('i-ext'), 'Explorer'),
      h('button', { class: 'btn lg ghost', title: 'Refresh balance', onclick: () => refreshOne(a.id) }, icon('i-refresh'))),
    h('div', { class: 'code' },
      h('div', { class: 'code-head' }, h('span', { text: `RECEIVE ADDRESS · ${a.network.toUpperCase()}` }),
        h('button', { onclick: () => copyText(addr) }, icon('i-copy'), 'Copy')),
      pre),
    h('p', { class: 'note' },
      `Only send ${a.symbol} on ${a.network} to this address; assets sent from another network may be lost. `,
      'After pasting an address anywhere, compare the highlighted first and last characters. ',
      a.token ? h('span', {}, 'Network fees are paid in ', h('b', { text: a.native }), '. Contract: ', h('code', { text: a.token })) : null,
      a.network === 'Monero' ? 'Incoming XMR needs 10 confirmations (about 20 minutes) before it can be spent.' : null),
    h('div', {}, S.notes.map(n => h('div', { class: 'err', text: n }))));
}

/* ---- history ---- */
async function loadHistory(id, force) {
  const gen = S.gen;
  if (!force && S.hist[id] && !S.hist[id].error) return;
  S.hist[id] = { loading: true, items: [] };
  try {
    const r = await call('history', id);
    if (gen !== S.gen) return;
    S.hist[id] = { items: r.items, unavailable: r.unavailable || '' };
  } catch (e) {
    if (gen !== S.gen) return;
    S.hist[id] = { items: [], error: e.message };
  }
  if (S.page === 'history') scheduleHistory();
  if (S.page === 'home') scheduleHome();
}
// Networks in parallel, one request at a time per network.
const histQueue = new Map();
function ensureHistory(ids, force) {
  for (const id of ids) {
    if (!force && S.hist[id]) continue;
    const a = byId(id); if (!a) continue;
    S.hist[id] = { loading: true, items: [] };
    const prev = histQueue.get(a.network) || Promise.resolve();
    const next = prev.then(() => loadHistory(id, true));
    histQueue.set(a.network, next.catch(() => {}));
  }
}
let histTimer;
function scheduleHistory() { clearTimeout(histTimer); histTimer = setTimeout(() => { if (S.page === 'history') renderHistory(); }, 120); }

function mergedHistory(ids) {
  const items = [];
  for (const id of ids) for (const it of (S.hist[id] && S.hist[id].items) || []) items.push(it);
  items.sort((x, y) => (y.time || 9e12) - (x.time || 9e12));
  return items;
}
function txRow(it) {
  const a = byId(it.id);
  const dirIcon = it.dir === 'in' ? 'i-down' : it.dir === 'out' ? 'i-up' : 'i-refresh';
  const title = it.dir === 'in' ? 'Received' : it.dir === 'out' ? 'Sent' : it.dir === 'self' ? 'Moved between own addresses' : 'Transaction';
  const sign = it.dir === 'in' ? '+' : it.dir === 'out' ? '−' : '';
  const p = price(it.symbol), n = parseFloat(it.amount);
  return h('button', { class: 'tx ' + it.dir, title: 'Open in block explorer', onclick: () => API.open_url(it.url) },
    h('span', { class: 'ic' }, icon(dirIcon)),
    h('span', { class: 'meta' },
      h('span', { class: 't' }, `${title} ${it.symbol}`,
        it.status !== 'confirmed' ? h('span', { class: 'pill', text: it.status }) : null),
      h('span', { class: 'd', text: [it.network, ago(it.time), it.counterparty ? (it.dir === 'in' ? 'from ' : 'to ') + short(it.counterparty) : '', a && a.token ? 'token' : ''].filter(Boolean).join(' · ') })),
    h('span', { class: 'amt' }, it.dir === 'unknown' ? '—' : `${sign}${it.amount} ${it.symbol}`,
      p != null && isFinite(n) && n > 0 ? h('small', { text: usd(n * p) }) : null));
}
function recentList(n) {
  const ids = S.accounts.filter(a => parseFloat(S.bal[a.id]) > 0).map(a => a.id);
  const items = mergedHistory(ids).slice(0, n);
  const busy = ids.some(id => S.hist[id] && S.hist[id].loading);
  if (!items.length) return h('div', { class: 'empty' }, busy ? thinking() : 'No recent transactions.');
  return h('div', { class: 'tx-list' }, items.map(txRow));
}

function renderHistory() {
  if (S.page !== 'history') return;
  const asset = S.histAsset && byId(S.histAsset);
  $('#netTitle').textContent = 'History';
  const ids = asset ? [asset.id] : S.accounts.map(a => a.id);
  ensureHistory(ids);
  let items = mergedHistory(ids);
  if (S.histFilter === 'in') items = items.filter(i => i.dir === 'in');
  if (S.histFilter === 'out') items = items.filter(i => i.dir === 'out');
  const busy = ids.filter(id => S.hist[id] && S.hist[id].loading).length;
  const seg = h('div', { class: 'seg' }, [['all', 'All'], ['in', 'Received'], ['out', 'Sent']].map(([k, label]) =>
    h('button', { class: S.histFilter === k ? 'on' : '', onclick: () => { S.histFilter = k; renderHistory(); } }, label)));
  // Group rows by day.
  const list = [];
  let day = null;
  for (const it of items.slice(0, 200)) {
    const d = it.time ? new Date(it.time * 1000).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }) : 'Pending';
    if (d !== day) { list.push(h('div', { class: 'tx-day', text: d })); day = d; }
    list.push(txRow(it));
  }
  const notes = ids.map(id => [byId(id), S.hist[id]]).filter(([a, s]) => a && s && (s.unavailable || s.error))
    .map(([a, s]) => h('div', { text: `${a.symbol} · ${a.network}: ${s.unavailable || s.error}` }));
  $('#view').replaceChildren(
    h('div', { class: 'home-top' }, h('div', { class: 'label', text: 'Transactions' }),
      h('div', { class: 'balance', text: asset ? `${asset.symbol} history` : 'Recent activity' })),
    h('div', { class: 'hist-tools' }, seg,
      asset ? h('span', { class: 'filter-chip' }, `${asset.symbol} · ${asset.network}`,
        h('button', { title: 'Show all assets', onclick: () => go('history') }, icon('i-x'))) : null,
      h('span', { class: 'spacer' }),
      busy ? h('span', { class: 'note' }, thinking(true), ` loading ${busy} of ${ids.length}…`) : null,
      h('button', { class: 'link-btn', onclick: () => { ensureHistory(ids, true); renderHistory(); } }, icon('i-refresh'), 'Refresh')),
    list.length ? h('div', { class: 'tx-list' }, list)
      : h('div', { class: 'empty' }, busy ? thinking() : 'No transactions found.'),
    notes.length ? h('div', { class: 'hist-notes' }, notes) : null);
}

/* ---- balances ---- */
async function refreshOne(id) {
  const gen = S.gen;
  S.bal[id] = '…'; showBal(id);
  let val;
  try { val = (await call('balance', id)).balance; } catch (e) { val = 'error'; console.warn(e.message); }
  if (gen !== S.gen) return;
  S.bal[id] = val; showBal(id);
}

async function refreshAll() {
  const btn = $('#topRefresh'); btn.classList.add('busy');
  loadPrices();
  const byNet = new Map();
  for (const a of S.accounts) { if (!byNet.has(a.network)) byNet.set(a.network, []); byNet.get(a.network).push(a.id); }
  // Networks in parallel, assets within a network one by one (kinder to public APIs).
  await Promise.all([...byNet.values()].map(async ids => { for (const id of ids) await refreshOne(id); }));
  btn.classList.remove('busy');
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = h('textarea', { value: text }); document.body.append(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }
  toast('Address copied');
}

async function lockNow(msg) {
  closeModal(); togglePf(false);
  S.gen++; S.accounts = []; S.bal = {}; S.cur = null; S.hist = {}; S.prices = null;
  try { await call('lock'); } catch { /* already locked */ }
  showUnlock(msg);
}

/* ---------------- Modals ---------------- */
function openModal(...kids) { $('#modal').replaceChildren(...kids.filter(Boolean)); $('#overlay').classList.add('open'); }
function closeModal() {
  if (!$('#overlay').classList.contains('open')) return;
  $('#overlay').classList.remove('open'); $('#modal').replaceChildren();
  if (API && API.cancel_send) API.cancel_send();
}
$('#overlay').addEventListener('mousedown', e => { if (e.target.id === 'overlay') closeModal(); });
const mHead = (ic, title, extra) => h('div', { class: 'm-head' }, h('h2', {}, icon(ic), title, extra || null),
  h('button', { class: 'icon-btn', title: 'Close (Esc)', onclick: closeModal }, icon('i-x')));

function openSend(a, prev) {
  prev = prev || { to: '', amount: '', max: false };
  const to = h('input', { class: 'mono', placeholder: `${a.network} address`, spellcheck: 'false', autocomplete: 'off', value: prev.to });
  const toHint = h('div', { class: 'hint' });
  const amt = h('input', { class: 'mono', placeholder: '0.00', inputmode: 'decimal', autocomplete: 'off', value: prev.amount });
  let max = prev.max;
  const segA = h('button', { text: 'Amount' }), segM = h('button', { text: 'Send all' });
  const syncSeg = () => { segA.classList.toggle('on', !max); segM.classList.toggle('on', max); amt.disabled = max; amt.placeholder = max ? 'Entire balance minus fee' : '0.00'; if (max) amt.value = ''; };
  segA.onclick = () => { max = false; syncSeg(); amt.focus(); }; segM.onclick = () => { max = true; syncSeg(); };
  const err = h('div', { class: 'form-err' });
  let vt;
  const validate = () => {
    clearTimeout(vt);
    const val = to.value.trim();
    if (!val) { toHint.textContent = ''; toHint.className = 'hint'; return; }
    vt = setTimeout(async () => {
      try {
        const r = await call('validate', a.id, val);
        if (to.value.trim() !== val) return;
        toHint.className = 'hint ' + (r.valid ? 'good' : 'bad');
        toHint.textContent = r.valid ? `✓ Valid ${a.network} address` : r.error;
      } catch (e) { toHint.textContent = e.message; }
    }, 300);
  };
  to.addEventListener('input', validate);
  const review = h('button', { class: 'btn primary' }, 'Review', icon('i-right'));
  const cancel = h('button', { class: 'btn', onclick: closeModal }, 'Cancel');
  review.onclick = async () => {
    const state = { to: to.value.trim(), amount: amt.value.trim(), max };
    if (!state.to) { err.textContent = 'Enter a recipient address.'; return; }
    if (!max && !state.amount) { err.textContent = 'Enter an amount.'; return; }
    review.disabled = cancel.disabled = true;
    err.replaceChildren(thinking(true), ' Building and signing on this computer…');
    try {
      const r = await call('prepare_send', a.id, state.to, state.amount, max);
      showConfirm(a, r, state);
    } catch (e) { err.textContent = e.message; review.disabled = cancel.disabled = false; }
  };
  const bal = S.bal[a.id];
  openModal(
    mHead('i-up', `Send ${a.symbol}`, h('span', { class: 'pill', text: a.network })),
    h('div', { class: 'm-body' },
      h('div', { class: 'field' }, h('label', {}, 'To', h('em', { text: a.network })), to, toHint),
      h('div', { class: 'field' }, h('label', {}, 'Amount', h('em', { text: bal && bal !== '…' ? `Balance: ${bal} ${a.symbol}` : '' })),
        h('div', { class: 'amount-row' }, amt, a.max ? h('div', { class: 'seg' }, segA, segM) : null)),
      h('div', { class: 'err' }, `Only send to a ${a.network} address. Transactions can't be reversed.`),
      err),
    h('div', { class: 'm-foot' }, cancel, review));
  syncSeg(); if (prev.to) validate();
  (prev.to ? amt : to).focus();
}

function showConfirm(a, r, state) {
  const hl = new Set(['To', 'Amount', 'Total']);
  const send = h('button', { class: 'btn primary' }, icon('i-up'), 'Confirm & send');
  const back = h('button', { class: 'btn', onclick: () => openSend(a, state) }, 'Back');
  const err = h('div', { class: 'form-err' });
  send.onclick = async () => {
    send.disabled = back.disabled = true; err.replaceChildren(thinking(true), ' Broadcasting…');
    try { const res = await call('broadcast', r.token); showSent(a, res); }
    catch (e) { err.textContent = e.message; back.disabled = false; }
  };
  openModal(
    mHead('i-check', 'Confirm transaction'),
    h('div', { class: 'm-body' },
      h('div', { class: 'err strong', text: "Check every detail. Transactions can't be reversed." }),
      h('dl', { class: 'summary' }, r.rows.map(([k, v]) => h('div', { class: hl.has(k) ? 'hl' : '' }, h('dt', { text: k }), h('dd', { text: v })))),
      r.warnings.length ? h('div', {}, r.warnings.map(w => h('div', { class: 'err', text: '⚠ ' + w }))) : null,
      err),
    h('div', { class: 'm-foot' }, back, send));
}

function showSent(a, res) {
  openModal(
    mHead('i-check', 'Sent'),
    h('div', { class: 'm-body' },
      h('div', { class: 'done' }, h('div', { class: 'ring' }, icon('i-check')),
        h('h3', { text: 'Transaction broadcast' }),
        h('p', { class: 'note', text: 'It will appear on the network shortly. Transaction ID:' }),
        h('code', { class: 'sel mono-val', text: res.txid }))),
    h('div', { class: 'm-foot' },
      h('button', { class: 'btn left', onclick: () => API.open_url(res.url) }, icon('i-ext'), 'View in explorer'),
      h('button', { class: 'btn primary', onclick: () => { closeModal(); setTimeout(() => refreshOne(a.id), 3000); } }, 'Done')));
}

async function openSettings() {
  let s;
  try { s = await call('settings'); } catch (e) { toast(e.message); return; }
  const mState = { off: 'Off', idle: 'Enabled (not started)', starting: 'Starting…', on: 'Running', error: 'Error' }[s.monero_state] || s.monero_state;
  openModal(
    mHead('i-gear', 'Settings'),
    h('div', { class: 'm-body' },
      h('dl', { class: 'kv' },
        h('dt', { text: 'Vault' }), h('dd', { class: 'sel', text: s.vault_path }),
        h('dt', { text: 'Config' }), h('dd', { class: 'sel', text: s.config_path }),
        h('dt', { text: 'Auto-lock' }), h('dd', { text: s.auto_lock ? `${s.auto_lock} minutes` : 'Off' }),
        h('dt', { text: 'Monero' }), h('dd', { text: mState }),
        h('dt', { text: 'Monero wallet-rpc' }), h('dd', { text: s.monero_rpc || '—' }),
        h('dt', { text: 'Monero node' }), h('dd', { text: s.monero_daemon || '—' })),
      s.monero_error ? h('div', { class: 'err', text: s.monero_error }) : null,
      h('p', { class: 'note' },
        'Endpoints, tokens, auto-lock, prices, the optional ', h('code', { text: 'etherscan_api_key' }),
        ' (history on Base, Arbitrum, Polygon and BNB Chain) and Monero are set in the config file. To enable Monero, download the official CLI from getmonero.org, verify it, then set ',
        h('code', { text: 'monero.enabled' }), ', ', h('code', { text: 'wallet_rpc_path' }), ' and ', h('code', { text: 'daemon' }),
        '. Lock and unlock to apply changes.')),
    h('div', { class: 'm-foot' },
      h('button', { class: 'btn left', onclick: () => API.open_config() }, icon('i-file'), 'Open config file'),
      s.monero_enabled && s.monero_state !== 'on' && s.monero_state !== 'starting'
        ? h('button', { class: 'btn', onclick: () => { closeModal(); startMonero(); } }, 'Start Monero') : null,
      h('button', { class: 'btn primary', onclick: closeModal }, 'Done')));
}

/* ---------------- Global wiring ---------------- */
$('#topRefresh').onclick = refreshAll;
$('#lockBtn').onclick = () => lockNow();
$('#settingsBtn').onclick = openSettings;
$('#navHome').onclick = () => go('home');
$('#navHistory').onclick = () => go('history');
$('#topHistory').onclick = () => go('history');
$('#pfBtn').onclick = e => { e.stopPropagation(); togglePf(); };
document.addEventListener('mousedown', e => { if (!e.target.closest('#pfDD, #pfBtn')) togglePf(false); });
$('#toggleSide').onclick = () => $('#app').classList.toggle('collapsed');
$('#acctSearch').addEventListener('input', renderSidebar);
window.__locked = () => { if (!$('#app').hidden) lockNow('Locked after inactivity.'); };
document.addEventListener('keydown', e => {
  const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
  if (mod && (k === 'r' || k === 'p' || k === 'h' || k === 'y')) e.preventDefault();  // no reload / print / browser history
  if (e.key === 'F5') e.preventDefault();
  if ($('#app').hidden) return;
  if (mod && k === 'r') refreshAll();
  if (mod && k === 'h') go('home');
  if (mod && k === 'y') go('history');
  if (mod && k === 'l') { e.preventDefault(); lockNow(); }
  if (e.key === 'Escape') { closeModal(); togglePf(false); }
});
document.addEventListener('contextmenu', e => { if (!e.target.closest('input, textarea, .sel')) e.preventDefault(); });
let lastPing = 0;
for (const ev of ['mousemove', 'keydown', 'mousedown', 'wheel']) {
  document.addEventListener(ev, () => {
    const now = Date.now();
    if (API && now - lastPing > 15000) { lastPing = now; API.ping(); }
  }, { passive: true });
}

/* ---------------- Converging-lines background ---------------- */
const cv = $('#flow'), ctx = cv.getContext('2d');
let W = 0, H = 0, paths = [], ends = [];
function bez(p, t) { const u = 1 - t; return [u*u*u*p[0][0] + 3*u*u*t*p[1][0] + 3*u*t*t*p[2][0] + t*t*t*p[3][0], u*u*u*p[0][1] + 3*u*u*t*p[1][1] + 3*u*t*t*p[2][1] + t*t*t*p[3][1]]; }
function layoutFlow() {
  const dpr = devicePixelRatio || 1; W = cv.clientWidth; H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const logo = $('#hero .hero-logo');
  paths = []; ends = [];
  if (!logo) return;
  // Wires converge on the spark where the padlock's shackle opens (viewBox 67,40).
  const lr = logo.getBoundingClientRect(), s = lr.width / 100;
  const ex = lr.left + 67 * s, ey = lr.top + 40 * s;
  ends.push([ex, ey]);
  const N = 7, spread = Math.min(H * 0.8, 520);
  for (const side of [-1, 1]) {
    for (let k = 0; k < N; k++) {
      const f = k / (N - 1) - 0.5, y0 = Math.max(ey, H * 0.42) + f * spread, x0 = side < 0 ? 40 : W - 40;
      const reach = Math.max(70, lr.width * 0.9);
      const c2 = [ex + side * reach * 0.8, ey - reach * 0.35];
      const p = [[x0, y0], [x0 + (c2[0] - x0) * 0.55, y0], c2, [ex, ey]];
      const edge = Math.abs(f) * 2;
      paths.push({ p, a: 0.55 - edge * 0.38, dots: Array.from({ length: 2 }, () => ({ t: Math.random(), v: 0.0009 + Math.random() * 0.0016, r: 1.6 + Math.random() * 1.6 })) });
    }
  }
}
function frame() {
  if (!$('#onboard').hidden && W) {
    ctx.clearRect(0, 0, W, H);
    for (const P of paths) {
      const [a, b, c, d] = P.p;
      ctx.beginPath(); ctx.moveTo(...a); ctx.bezierCurveTo(...b, ...c, ...d);
      ctx.strokeStyle = `rgba(255,255,255,${P.a * 0.45})`; ctx.lineWidth = 1; ctx.shadowBlur = 6; ctx.shadowColor = 'rgba(255,255,255,.5)'; ctx.stroke();
      ctx.beginPath(); ctx.arc(a[0], a[1], 3.2, 0, 7); ctx.strokeStyle = `rgba(255,255,255,${P.a + 0.2})`; ctx.lineWidth = 1.2; ctx.stroke();
      ctx.beginPath(); ctx.arc(a[0], a[1], 1.4, 0, 7); ctx.fillStyle = '#fff'; ctx.fill();
      for (const D of P.dots) {
        D.t += D.v; if (D.t > 1) { D.t = 0; D.v = 0.0009 + Math.random() * 0.0016; }
        const [x, y] = bez(P.p, D.t), fade = Math.sin(Math.PI * D.t);
        ctx.beginPath(); ctx.arc(x, y, D.r, 0, 7); ctx.fillStyle = `rgba(255,255,255,${0.35 + fade * 0.65})`; ctx.shadowBlur = 12; ctx.shadowColor = '#fff'; ctx.fill();
      }
    }
    for (const [x, y] of ends) {
      const g = ctx.createRadialGradient(x, y, 0, x, y, 9);
      g.addColorStop(0, 'rgba(255,255,255,.9)'); g.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 9, 0, 7); ctx.fill();
    }
    ctx.shadowBlur = 0;
  }
  requestAnimationFrame(frame);
}
new ResizeObserver(layoutFlow).observe(cv);
$('#onboard').addEventListener('scroll', layoutFlow, { passive: true });
requestAnimationFrame(frame);

/* ---------------- Boot ---------------- */
async function start() {
  try {
    const st = await call('state');
    if (st.unlocked) enterApp();
    else if (st.vault_exists) showUnlock();
    else showWelcome();
  } catch (e) { hero(heroLogo(), h('h1', { text: 'Something went wrong' }), h('p', { text: e.message })); }
}
window.addEventListener('pywebviewready', () => { if (!API) { API = window.pywebview.api; start(); } });
// UI development without Python: open index.html?mock in a browser.
if (/[?&]mock\b/.test(location.search)) {
  setTimeout(() => {
    if (API) return;
    const s = document.createElement('script'); s.src = 'dev-mock.js';
    s.onload = () => { API = window.MockApi; start(); };
    document.head.append(s);
  }, 300);
}
