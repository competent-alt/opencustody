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
const S = { accounts: [], bal: {}, cur: null, notes: [], monero: 'off', moneroErr: '', gen: 0 };

async function enterApp() {
  $('#onboard').hidden = true;
  $('#app').hidden = false;
  S.gen++;
  await loadAccounts();
  if (!S.cur && S.accounts.length) S.cur = S.accounts[0].id;
  renderSidebar(); renderView();
  refreshAll();
  if (S.monero === 'idle') startMonero();
}

async function loadAccounts() {
  const r = await call('accounts');
  S.accounts = r.accounts; S.notes = r.notes; S.monero = r.monero; S.moneroErr = r.monero_error;
}

async function startMonero() {
  S.monero = 'starting'; renderSidebar();
  try { await call('start_monero'); } catch (e) { S.moneroErr = e.message; }
  await loadAccounts(); renderSidebar(); renderView();
  for (const a of S.accounts) if (a.network === 'Monero') refreshOne(a.id);
}

const cur = () => S.accounts.find(a => a.id === S.cur);
const loading = id => S.bal[id] === undefined || S.bal[id] === '…';
function balNode(id, small) {
  return loading(id) ? thinking(small) : small ? h('span', { class: 'bal', text: S.bal[id] }) : document.createTextNode(S.bal[id]);
}
function select(id) {
  S.cur = id;
  for (const b of document.querySelectorAll('#accts .acct')) b.classList.toggle('active', b.dataset.id === id);
  renderView();
}
// Update one balance in place, so clicks aren't lost to re-rendering while balances load.
function showBal(id) {
  for (const b of document.querySelectorAll('#accts .acct')) if (b.dataset.id === id) b.lastChild.replaceWith(balNode(id, true));
  const v = $('#balVal');
  if (id === S.cur && v) v.replaceChildren(balNode(id, false));
}
const symBadge = s => s.length > 4 ? s.slice(0, 4) : s;

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
      nav.append(h('button', { class: 'acct' + (a.id === S.cur ? ' active' : ''), 'data-id': a.id, onclick: () => select(a.id) },
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

function renderView() {
  const a = cur(), v = $('#view');
  if (!a) { v.replaceChildren(h('div', { class: 'empty-history', text: 'No accounts.' })); return; }
  $('#netTitle').textContent = a.network;
  const addr = a.address;
  const pre = h('pre', { class: 'sel' }, h('b', { text: addr.slice(0, 6) }), addr.slice(6, -6), h('b', { text: addr.slice(-6) }));
  v.replaceChildren(
    h('div', { class: 'acct-hero' },
      h('div', { class: 'big-sym', text: symBadge(a.symbol) }),
      h('div', { class: 'label', text: a.network + (a.token ? ' · token' : '') }),
      h('div', { class: 'balance' }, h('span', { id: 'balVal', class: 'val' }, balNode(a.id, false)), h('span', { text: a.symbol })),
      h('div', { class: 'sub', text: a.token ? `Token on ${a.network}` : `Native coin of ${a.network}` })),
    h('div', { class: 'actions' },
      h('button', { class: 'btn primary lg', onclick: () => openSend(a) }, icon('i-up'), 'Send'),
      h('button', { class: 'btn lg', onclick: () => copyText(addr) }, icon('i-copy'), 'Copy address'),
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

async function refreshOne(id) {
  const gen = S.gen;
  S.bal[id] = '…'; showBal(id);
  let val;
  try { val = (await call('balance', id)).balance; } catch (e) { val = 'error'; console.warn(e.message); }
  if (gen !== S.gen) return;
  S.bal[id] = val; showBal(id);
}

async function refreshAll() {
  const btn = $('#refreshBtn'); btn.classList.add('busy');
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
  closeModal();
  S.gen++; S.accounts = []; S.bal = {}; S.cur = null;
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
        'Endpoints, tokens, auto-lock and Monero are set in the config file. To enable Monero, download the official CLI from getmonero.org, verify it, then set ',
        h('code', { text: 'monero.enabled' }), ', ', h('code', { text: 'wallet_rpc_path' }), ' and ', h('code', { text: 'daemon' }),
        '. Lock and unlock to apply changes.')),
    h('div', { class: 'm-foot' },
      h('button', { class: 'btn left', onclick: () => API.open_config() }, icon('i-file'), 'Open config file'),
      s.monero_enabled && s.monero_state !== 'on' && s.monero_state !== 'starting'
        ? h('button', { class: 'btn', onclick: () => { closeModal(); startMonero(); } }, 'Start Monero') : null,
      h('button', { class: 'btn primary', onclick: closeModal }, 'Done')));
}

/* ---------------- Global wiring ---------------- */
$('#refreshBtn').onclick = refreshAll;
$('#lockBtn').onclick = () => lockNow();
$('#settingsBtn').onclick = openSettings;
$('#toggleSide').onclick = () => $('#app').classList.toggle('collapsed');
$('#acctSearch').addEventListener('input', renderSidebar);
window.__locked = () => { if (!$('#app').hidden) lockNow('Locked after inactivity.'); };
document.addEventListener('keydown', e => {
  const k = e.key.toLowerCase();
  if ((e.ctrlKey || e.metaKey) && (k === 'r' || k === 'p')) e.preventDefault();  // no reload / print
  if (e.key === 'F5') e.preventDefault();
  if ($('#app').hidden) return;
  if ((e.ctrlKey || e.metaKey) && k === 'r') refreshAll();
  if ((e.ctrlKey || e.metaKey) && k === 'l') { e.preventDefault(); lockNow(); }
  if (e.key === 'Escape') closeModal();
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
