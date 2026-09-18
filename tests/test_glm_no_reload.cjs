'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { createRequire } = require('node:module');
const { EventEmitter } = require('node:events');

// Runs the shipped adapter without Electron/npm packages. Only HTML conversion
// and the remote DOM/CDP boundary are mocked; navigation/session/timing/error
// handling execute the production source. Real-DOM coverage is in
// test_glm_session.cjs (requires the project's existing jsdom dependency).
const adapterFile = path.join(__dirname, '../electron/adapter.cjs');
const adapterSource = fs.readFileSync(adapterFile, 'utf8');
const localRequire = createRequire(adapterFile);
function loadAdapter() {
  const module = { exports: {} };
  class TextOnlyConverter {
    use() {} remove() {} addRule() {}
    turndown(html) { return html.replace(/<[^>]*>/g, ''); }
  }
  const requireStub = id => id === 'turndown' ? TextOnlyConverter
    : id === 'turndown-plugin-gfm' ? { gfm() {} } : localRequire(id);
  vm.runInNewContext(adapterSource, { module, exports: module.exports, require: requireStub,
    URL, Date, setTimeout, clearTimeout, console }, { filename: adapterFile });
  return module.exports.WebsiteAdapter;
}
const selectors = { input: ['textarea'], send: ['#send'], assistant: ['.assistant'], stop: [], new_chat: [] };
const job = id => ({ job_id: id, request_id: id, purpose: 'candidate', prompt: `TASK ${id}`,
  timeout_seconds: 35, stable_seconds: 0.01, min_wait_seconds: 0, recovery_timeout_seconds: 0 });
function site(options = {}) {
  const provider = options.provider || 'glm';
  const configuredURL = provider === 'qwen' ? 'https://chat.qwen.ai/' : 'https://chat.z.ai/';
  let url = options.url || (options.cold ? 'about:blank' : configuredURL + 'c/previous');
  let hasHistory = options.history !== false, draft = options.draft || '', inputCount = 0, sent = 0, loads = 0, claimed = false;
  let selected = options.unknownModel ? null : { label: 'GLM-5.3-Flash', key: 'glm-5.3-flash' };
  let answers = hasHistory ? [{ text: 'OLD ANSWER', html: '<p>OLD ANSWER</p>' }] : [];
  let latestPrompt = '', newChatCount = 0;
  const commands = [], logs = [], calls = [], statuses = [];
  let verificationUntil = 0, verificationTriggered = false, acquired = 0, released = 0;
  const activateVerification = () => { verificationTriggered = true; verificationUntil = options.verificationNeverClears ? Infinity : Date.now() + 1800; };
  if (options.verification === 'initial') activateVerification();
  const verification = () => ({ required: Date.now() < verificationUntil, kind: 'slider' });
  const wc = new EventEmitter();
  const page = { visibilityState: 'visible', hasFocus: true };
  const session = () => ({ model: selected, answers: answers.length, userCount: hasHistory ? 1 : 0,
    inputEmpty: !draft.trim(), inputPresent: true, inputReady: true, stopping: false,
    conversation: url, rating: [] });
  const inspect = () => ({ page, verification: verification(), summary: {}, answers: [...answers], inputReady: true, inputEmpty: !draft,
    stopping: false, userCount: hasHistory ? 1 : 0, lastUserMatchesPrompt: latestPrompt === draft || !!sent });
  const domAction = (action, args) => {
    calls.push(action);
    if (action === 'abort') return true;
    if (action === 'inspect') return inspect();
    if (action === `${provider}Session`) return session();
    if (action === `${provider}NewChatTarget`) return options.noNewChat
      ? { ready: false, reason: 'new_chat_missing' } : { ready: true, method: 'button', x: 10, y: 10, target: { id: 'new-chat' } };
    if (action === 'prepare') {
      claimed = false;
      return { page, ready: !draft, reason: 'input_not_empty', inputFocused: true, inputMethod: 'cdp_insert_text' };
    }
    if (action === 'canSubmit' || action === 'claimSubmit') {
      if (options.verification === 'claim' && action === 'claimSubmit' && !verificationTriggered) activateVerification();
      if (verification().required) return { ready: false, reason: 'verification_required', verification: verification() };
      if (action === 'claimSubmit') { assert.equal(claimed, false, 'Never claim a send twice'); claimed = true; }
      return { page, ready: draft === args.prompt, method: 'button', x: 100, y: 100,
        inputLength: draft.length, expectedLength: args.prompt.length };
    }
    if (action === 'recoveryInspect') return options.contextChanged && sent ? { currentError: false, contextChanged: true, reason: 'recovery_user_changed', userCount: 1, baselineUserCount: 0, lastUserMatchesPrompt: false } : { currentError: false };
    throw new Error(`Unexpected page action: ${action}`);
  };
  Object.assign(wc, {
    getURL: () => url, isDestroyed: () => false, stop() {},
    async loadURL(next) {
      loads++; url = next;
      hasHistory = false; answers = []; draft = '';
      if (options.redirectOnLoad) {
        wc.emit('will-redirect', { preventDefault() {} }, 'https://other.invalid/login', false, true);
      }
    },
    async executeJavaScriptInIsolatedWorld(_world, sources) {
      const code = sources[0].code;
      if (code.includes("})('abort',")) return true;
      const tail = code.match(/\}\)\(("(?:[^"\\]|\\.)*"),(\{[^\n]*\})\); \}\)\(\)$/);
      assert.ok(tail, 'Read the serialized production pageAction arguments');
      return domAction(JSON.parse(tail[1]), JSON.parse(tail[2]));
    },
    debugger: {
      attached: false, isAttached() { return this.attached; },
      attach() { assert.equal(this.attached, false); this.attached = true; },
      detach() { this.attached = false; },
      async sendCommand(method, params) {
        commands.push({ method, ...params });
        if (method === 'Input.insertText') {
          assert.equal(draft, ''); draft = params.text; latestPrompt = draft; inputCount++;
          if (options.changeBeforeSend) selected = { label: 'GLM-5.3', key: 'glm-5.3' };
          if (options.hideBeforeSend) selected = null;
          if (options.verification === 'before-send') activateVerification();
        }
        if (method !== 'Input.dispatchMouseEvent' || params.type !== 'mouseReleased') return;
        if (params.x === 10) {
          newChatCount++;
          if (options.fullNavigation || options.fullRedirect) {
            let prevented = false;
            wc.emit(options.fullRedirect ? 'will-redirect' : 'will-navigate',
              { preventDefault() { prevented = true; } }, configuredURL, false, true);
            assert.equal(prevented, true, 'The adapter must block full document navigation');
            return;
          }
          if (options.ignoreNewChat) return;
          url = `${configuredURL}c/new-${newChatCount}`;
          if (options.routeOnly) return;
          hasHistory = false; answers = [];
          if (options.changeModel) selected = { label: 'GLM-5.3', key: 'glm-5.3' };
          if (options.reformatLabel) selected = { label: 'glm-5.3 flash', key: 'glm-5.3-flash' };
        } else {
          assert.equal(params.x, 100); assert.equal(claimed, true); sent++;
          answers.push({ text: `NEW ANSWER ${sent}`, html: `<p>NEW ANSWER ${sent}</p>` });
          hasHistory = true; draft = '';
          if (options.verification === 'after-send') activateVerification();
        }
      },
    },
  });
  const WebsiteAdapter = loadAdapter();
  const adapter = new WebsiteAdapter(wc, { id: provider, url: configuredURL, selectors },
    (state, message) => statuses.push({state, message}), (event, fields) => logs.push({ event, ...fields }),
    options.useLease ? { acquireInput: async () => {acquired++; return async () => {released++;};} } : {});
  return { adapter, wc, commands, logs, calls, statuses, get acquired() {return acquired;}, get released() {return released;}, get loads() { return loads; }, get sent() { return sent; },
    get newChats() { return newChatCount; }, get inserted() { return inputCount; }, get draft() { return draft; },
    get model() { return selected; } };
}
async function settle(t, promise) {
  let result;
  promise.then(value => { result = { value }; }, error => { result = { error }; });
  for (let elapsed = 0; !result && elapsed <= 60000; elapsed += 100) {
    await new Promise(resolve => setImmediate(resolve));
    t.mock.timers.tick(100);
  }
  assert.ok(result, 'Operation finishes within its deadline');
  if (result.error) throw result.error;
  return result.value;
}
function checkCleanup(s) {
  assert.equal(s.adapter.active, false);
  assert.equal(s.wc.debugger.isAttached(), false);
  assert.equal(s.wc.listenerCount('will-navigate'), 0);
  assert.equal(s.wc.listenerCount('will-redirect'), 0);
}
function timedTest(name, fn) {
  test(name, async t => {
    t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
    try { await fn(t); } finally { t.mock.timers.reset(); }
  });
}
for (const provider of ['glm', 'qwen']) {
  timedTest(`${provider}: two warm jobs preserve selected model with zero loadURL calls`, async t => {
    const s = site({ provider });
    assert.equal(await settle(t, s.adapter.run(job('one'))), 'NEW ANSWER 1');
    assert.equal(await settle(t, s.adapter.run(job('two'))), 'NEW ANSWER 2');
    assert.equal(s.loads, 0); assert.equal(s.newChats, 2); assert.equal(s.sent, 2); assert.equal(s.inserted, 2);
    assert.equal(s.model.key, 'glm-5.3-flash'); checkCleanup(s);
    assert.equal(s.logs.filter(row => row.event === 'adapter.navigation_reused').length, 2);
  });
}
timedTest('GLM cold-start loads once; second request reuses the page', async t => {
  const s = site({ cold: true });
  await settle(t, s.adapter.run(job('cold'))); await settle(t, s.adapter.run(job('warm')));
  assert.equal(s.loads, 1); assert.equal(s.newChats, 1); assert.equal(s.sent, 2); checkCleanup(s);
});
timedTest('an empty GLM page sends directly without a new-chat gesture', async t => {
  const s = site({ history: false }); await settle(t, s.adapter.run(job('empty')));
  assert.equal(s.loads, 0); assert.equal(s.newChats, 0); assert.equal(s.sent, 1); checkCleanup(s);
});
for (const [name, options, code, newChats, inserted] of [
  ['draft in an old chat', { draft: 'KEEP DRAFT' }, 'input_not_empty', 0, 0],
  ['draft in an empty chat', { history: false, draft: 'KEEP DRAFT' }, 'input_not_empty', 0, 0],
  ['missing new-chat control', { noNewChat: true }, 'glm_new_chat_required', 0, 0],
  ['ignored new-chat click', { ignoreNewChat: true }, 'glm_new_chat_unconfirmed', 1, 0],
  ['route changes without clearing old messages', { routeOnly: true }, 'glm_new_chat_unconfirmed', 1, 0],
  ['full document navigation', { fullNavigation: true }, 'glm_new_chat_requires_reload', 1, 0],
  ['full document redirect', { fullRedirect: true }, 'glm_new_chat_requires_reload', 1, 0],
  ['selected model reset by new-chat', { changeModel: true }, 'glm_model_changed', 1, 0],
  ['model changes just before Send', { changeBeforeSend: true }, 'glm_model_changed', 1, 1],
  ['known model becomes unreadable before Send', { hideBeforeSend: true }, 'glm_model_unverified', 1, 1],
]) {
  timedTest(`GLM ${name} fails safely without reload or duplicate input`, async t => {
    const s = site(options);
    await assert.rejects(settle(t, s.adapter.run(job(name))), error => error.code === code);
    assert.equal(s.loads, 0); assert.equal(s.sent, 0); assert.equal(s.newChats, newChats); assert.equal(s.inserted, inserted);
    if (options.draft) assert.equal(s.draft, options.draft);
    checkCleanup(s);
  });
}
timedTest('GLM tolerates a label-format change for the same normalized model ID', async t => {
  const s = site({ reformatLabel: true }); await settle(t, s.adapter.run(job('normalized')));
  assert.equal(s.sent, 1); assert.equal(s.loads, 0); checkCleanup(s);
});
timedTest('unknown GLM model is not falsely logged as verified', async t => {
  const s = site({ unknownModel: true }); await settle(t, s.adapter.run(job('unknown')));
  const row = s.logs.find(row => row.event === 'adapter.glm_session_ready');
  assert.equal(row.model_preservation, 'no_reload_unverified_model'); assert.equal(s.loads, 0); checkCleanup(s);
});
timedTest('cold GLM navigation preserves the cross-origin security check', async t => {
  const s = site({ cold: true, redirectOnLoad: true });
  await assert.rejects(settle(t, s.adapter.run(job('redirect'))), error => error.code === 'provider_origin_changed');
  assert.equal(s.inserted, 0); assert.equal(s.sent, 0); checkCleanup(s);
});
timedTest('cancelled GLM rate-limit wait never reloads or sends', async t => {
  const s = site({ history: false }); s.adapter.lastSubmittedAt = Date.now();
  const controller = new AbortController();
  const pending = s.adapter.run({ ...job('cancel'), access_interval_seconds: 60 }, controller.signal);
  controller.abort();
  await assert.rejects(settle(t, pending), error => error.code === 'cancelled');
  assert.equal(s.loads, 0); assert.equal(s.inserted, 0); assert.equal(s.sent, 0); checkCleanup(s);
});

// Exercise the exact GLM selection-reader block with minimal control objects;
// this isolates parsing/ambiguity rules without claiming live-site DOM coverage.
const domSource = fs.readFileSync(path.join(__dirname, '../electron/provider-dom.cjs'), 'utf8');
const modelStart = domSource.indexOf("    if (provider === 'glm') {\n      // Read only");
const modelEnd = domSource.indexOf('\n    const ratingSelectors', modelStart);
const modelCode = 'let model = null;\n' + domSource.slice(modelStart, modelEnd) + '\nmodel;';
function control(text, options = {}) {
  return { tagName: options.select ? 'SELECT' : 'BUTTON', textContent: text, value: options.value,
    selectedOptions: options.select ? [{ textContent: options.selected }] : undefined,
    getAttribute: name => (options.attrs || {})[name] || null,
    closest: () => options.excluded ? {} : null,
    matches: () => !options.wrapper,
  };
}
function readSelection(nodes) {
  return vm.runInNewContext(modelCode, { provider: 'glm', document: {}, modelSelectors: ['[data-testid="model-selector"]'],
    usable: () => true, accessibleLabel: node => node.getAttribute('aria-label') || node.textContent,
    query: (_document, selector) => selector === '[data-testid="model-selector"]' || selector === 'button,[role="button"],[role="combobox"]' ? nodes : [],
  });
}
for (const label of ['glm-5.3-flash', 'GLM-5.3-Flash', 'GLM 5.3 Flash', 'GLM5.3-Flash']) {
  test(`GLM model reader normalizes ${label}`, () => {
    assert.equal(readSelection([control(label)]).key, 'glm-5.3-flash');
  });
}
test('GLM model reader uses selected option, not all dropdown options', () => {
  const node = control('GLM-5.3 GLM-5.3-Flash', { select: true, selected: 'GLM-5.3-Flash', value: 'glm-5.3-flash' });
  assert.equal(readSelection([node]).key, 'glm-5.3-flash');
});
test('GLM generic accessible name does not hide the visible selected model', () => {
  assert.equal(readSelection([control('GLM-5.3-Flash', { attrs: { 'aria-label': 'Select model' } })]).key, 'glm-5.3-flash');
});
for (const [name, nodes] of [
  ['a menu option', [control('GLM-5.3-Flash', { excluded: true })]],
  ['a wrapper showing every available model', [control('GLM-5.3 GLM-5.3-Flash', { wrapper: true })]],
  ['conflicting visible model controls', [control('GLM-5.3'), control('GLM-5.3-Flash')]],
  ['a button listing multiple model IDs', [control('GLM-5.3 GLM-5.3-Flash')]],
  ['an unknown model name', [control('Select model')]],
  ['a promotional switch-model button', [control('Try GLM-5.3-Flash')]],
]) test(`GLM selection is not inferred from ${name}`, () => assert.equal(readSelection(nodes), null));

const mainSource = fs.readFileSync(path.join(__dirname, '../electron/main.cjs'), 'utf8');
const applyConfigSource = mainSource.slice(mainSource.indexOf('async function applyConfig(next)'), mainSource.indexOf('\nfunction validJob(job)'));
function configFixture() {
  const provider = { id: 'glm', name: 'GLM', enabled: true, url: 'https://chat.z.ai/', proxy: '', access_interval_seconds: 0 };
  const events = [];
  const wc = { session: { async setProxy() { events.push('proxy'); }, async closeAllConnections() { events.push('close'); } },
    async loadURL(url) { events.push(['load', url]); } };
  const item = { provider, adapter: { provider }, view: { webContents: wc } };
  const context = { browserLogin: { busy: false }, runningJobs: new Map(), providers: new Map([['glm', item]]),
    PROVIDER_ID: /^[a-z][a-z0-9_-]{0,39}$/, safeWebURL: () => true,
    providerLayout: { setTitle() {}, remove() { assert.fail('Do not recreate a retained provider'); } },
    createProvider: () => assert.fail('Do not recreate a retained provider'), appliedConfigSerial: 0,
    log() {}, layoutProviders() {}, config: {} };
  vm.runInNewContext(applyConfigSource, context);
  return { provider, item, events, apply: context.applyConfig };
}
test('saving GLM name/rate-limit settings reuses the exact WebContents without loadURL', async () => {
  const f = configFixture(), oldView = f.item.view;
  await f.apply({ providers: [{ ...f.provider, name: 'GLM · Flash', access_interval_seconds: 15 }], fusion: {} });
  assert.equal(f.item.view, oldView); assert.equal(f.item.adapter.provider.access_interval_seconds, 15);
  assert.deepEqual(f.events, []);
});
test('an explicit GLM URL change retains the intentional network reload behavior', async () => {
  const f = configFixture();
  await f.apply({ providers: [{ ...f.provider, url: 'https://chat.z.ai/?changed=1' }], fusion: {} });
  assert.deepEqual(f.events, ['proxy', 'close', ['load', 'https://chat.z.ai/?changed=1']]);
});

for (const timing of ['initial', 'before-send', 'claim', 'after-send']) {
  timedTest(`GLM challenge ${timing}: wait for human-clear, no reload and exactly one Send`, async t => {
    const s = site({history: false, verification: timing, useLease: true});
    const stages = [];
    await settle(t, s.adapter.run(job('verification-' + timing), undefined, row => stages.push(row.stage)));
    assert.equal(s.loads, 0); assert.equal(s.inserted, 1); assert.equal(s.sent, 1);
    assert.ok(stages.includes('verification_required')); assert.ok(stages.includes('verification_cleared'));
    assert.ok(s.statuses.some(row => row.state === 'verification_required'));
    assert.equal(s.acquired, s.released);
    if (timing === 'before-send' || timing === 'claim') assert.equal(s.acquired, 2, 'Release exclusive input during human verification');
    assert.equal(s.commands.filter(row => row.method === 'Input.dispatchMouseEvent').length, 2, 'Only Send press/release, never a slider gesture');
    checkCleanup(s);
  });
}
timedTest('persistent GLM verification times out without sending or refreshing', async t => {
  const s = site({history: false, verification: 'initial', verificationNeverClears: true});
  await assert.rejects(settle(t, s.adapter.run({...job('verify-timeout'), total_timeout_seconds: 3, recovery_timeout_seconds: 180})),
    error => error.code === 'verification_timeout');
  assert.equal(s.inserted, 0); assert.equal(s.sent, 0); assert.equal(s.loads, 0); checkCleanup(s);
});
timedTest('a global candidate deadline is not extended by the recovery budget', async t => {
  const s = site({history: false}); const started = Date.now();
  await assert.rejects(settle(t, s.adapter.run({...job('budget'), total_timeout_seconds: 1, recovery_timeout_seconds: 180})),
    error => error.code === 'timeout');
  assert.ok(Date.now() - started <= 1500); assert.equal(s.sent, 0); checkCleanup(s);
});

for (const recovery of [0, 15]) test(`GLM context failure retains diagnostic reason with recovery timeout ${recovery}`, async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const s = site({ contextChanged: true });
  try {
    await assert.rejects(settle(t, s.adapter.run({ ...job('context'), recovery_timeout_seconds: recovery })), error => {
      assert.equal(error.code, 'recovery_context_changed');
      assert.equal(error.details.recovery.reason, 'recovery_user_changed');
      assert.match(error.message, /recovery_user_changed/);
      return true;
    });
    assert.equal(s.loads, 0);
    assert.equal(s.sent, 1);
    assert.ok(s.logs.some(item => item.event === 'adapter.context_check'));
  } finally { t.mock.timers.reset(); }
});
