'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const root = path.resolve(__dirname, '..');
const clone = value => JSON.parse(JSON.stringify(value));
const configExample = JSON.parse(fs.readFileSync(path.join(root, 'config.example.json'), 'utf8'));
const savedConversation = {
  id: 'saved-thread', title: '历史内存分析',
  messages: [{ role: 'system', content: '使用中文分析。' }, { role: 'user', content: '先分析 Java。' }, { role: 'assistant', content: '检查 GC roots。' }],
  runs: [{ request_id: 'saved-run', mode: 'web', sources: [{ provider: 'chatgpt', markdown: '原始 GC 分析' }], errors: [] }],
};
const browserProfiles = {
  ok: true,
  profiles: [
    { id: 'firefox-fixture', browser: 'Firefox', family: 'firefox', name: 'default-release', path: '/fixture/.mozilla/firefox/fixture.default-release' },
    { id: 'chrome-fixture', browser: 'Chrome', family: 'chromium', name: 'Default', path: '/fixture/.config/google-chrome/Default' },
  ],
  warnings: [], capabilities: { chromium_decryption: true },
};
const loginReport = { provider_id: 'chatgpt', imported: 4, skipped: 2, storage_imported: 1, warnings: [{ code: 'partitioned', message: '已跳过不支持的分区 Cookie。' }], source: 'Firefox', authenticated: false };

function response(content = '## 整合结果\n\n检查 Java、Native 和内核。', extra = {}) {
  return {
    id: 'chatcmpl-fixture', object: 'chat.completion', model: 'web-fusion',
    choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }],
    fusion: { conversation_id: 'new-thread', request_id: 'fixture-run', mode: 'web', sources: [], errors: [], ...extra },
  };
}

async function waitFor(predicate, label) {
  // The complete Node suite runs files concurrently; loaded CI hosts can spend
  // more than two seconds scheduling jsdom timers even though the UI state
  // transition itself is immediate. Keep this a bounded assertion without
  // turning host scheduling jitter into a product regression.
  const deadline = Date.now() + 5000;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(`UI did not reach: ${label}`);
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}

async function harness(t, options = {}) {
  const consoleErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => consoleErrors.push(error));
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'ui/index.html'), 'utf8'), {
    url: 'file://' + path.join(root, 'ui/index.html'), runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole,
  });
  t.after(() => {
    dom.window.close();
    assert.deepEqual(consoleErrors.map(error => error.message), [], 'No renderer script errors');
  });
  const window = dom.window;
  const intervals = [];
  const nativeSetInterval = window.setInterval.bind(window);
  window.setInterval = (callback, delay, ...args) => {intervals.push({callback, delay}); return nativeSetInterval(callback, delay, ...args);};
  const calls = [];
  let config = clone(options.config || configExample);
  let statusCallback;
  const record = (name, ...args) => { const call = { name, args: clone(args) }; calls.push(call); return call; };
  const status = { bridge_connected: true, busy: false, queue_size: 0, providers: {
    chatgpt: { state: 'ready', message: 'Fixture ready' }, deepseek: { state: 'ready', message: 'Fixture ready' },
  } };
  window.fusion = {
    async request(method, route, body) {
      record('request', method, route, body ?? null);
      if (method === 'GET' && route === '/internal/config') return clone(config);
      if (method === 'GET' && route === '/internal/status') return clone(status);
      if (method === 'GET' && route === '/internal/history') return { conversations: [{ id: savedConversation.id, title: savedConversation.title }] };
      if (method === 'GET' && route === '/internal/history/saved-thread') return clone(savedConversation);
      if (method === 'PUT' && route === '/internal/config') { config = clone(body); return clone(config); }
      if (method === 'POST' && route === '/v1/chat/completions') return options.complete ? options.complete(clone(body)) : response();
      throw new Error(`Unexpected renderer route: ${method} ${route}`);
    },
    async showProvider(id) { record('showProvider', id); return { ok: true }; },
    async setBounds(bounds) { record('setBounds', bounds); return { ok: true }; },
    async reloadProvider(id) { record('reloadProvider', id); return { ok: true }; },
    async copyText(value) { record('copyText', value); },
    async saveMarkdown(value) { record('saveMarkdown', value); return { canceled: false, filePath: '/fixture/export.md' }; },
    async runtimeInfo() { record('runtimeInfo'); return { baseUrl: 'http://127.0.0.1:8765/v1', token: 'fixture-secret-api-token', dataDir: '/fixture/data', version: '1.0.0' }; },
    async listBrowserProfiles() { record('listBrowserProfiles'); return options.listBrowserProfiles ? options.listBrowserProfiles() : clone(browserProfiles); },
    async importBrowserLogin(input) { record('importBrowserLogin', input); return options.importBrowserLogin ? options.importBrowserLogin(clone(input)) : { ...clone(loginReport), provider_id: input.provider_id }; },
    async importLoginFile(input) { record('importLoginFile', input); return options.importLoginFile ? options.importLoginFile(clone(input)) : { ...clone(loginReport), provider_id: input.provider_id, source: 'extension' }; },
    onStatus(callback) { statusCallback = callback; return () => { statusCallback = undefined; }; },
  };
  const storage = options.storage || new Map();
  Object.defineProperty(window, 'localStorage', {value: {getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)}});
  if (options.layoutApi) {
    window.fusion.setLayout = async (packet) => {record('setLayout', packet); return options.setLayout ? options.setLayout(clone(packet)) : {ok: true};};
    window.fusion.openRecoveryProvider = async id => {
      record('openRecoveryProvider', id);
      if (options.openRecoveryProvider) return options.openRecoveryProvider(id);
      const packet = clone(calls.filter(call => call.name === 'setLayout').at(-1).args[0]);
      packet.active_provider = id;
      if (packet.mode === 'tabs') packet.panes[0].provider_id = id;
      return {...packet, ok: true};
    };
    window.fusion.diagnoseSend = async (id) => {record('diagnoseSend', id); return options.diagnoseSend ? options.diagnoseSend(id) : {provider_id: id, send: {found: true, disabled: false}, input: {found: true}, page_visible: true};};
  }
  window.ResizeObserver = class { observe() {} disconnect() {} };
  window.HTMLElement.prototype.getBoundingClientRect = function () {
    if (options.rects?.[this.id]) {const {x = 0, y = 0, width, height} = options.rects[this.id]; return {left: x, top: y, x, y, width, height, right: x + width, bottom: y + height};}
    return {left: 650, top: 150, x: 650, y: 150, width: 650, height: 600, right: 1300, bottom: 750};
  };
  // Execute the exact shipped browser dependencies, in the same order as index.html.
  for (const script of window.document.querySelectorAll('script[src]')) {
    const file = path.resolve(root, 'ui', script.getAttribute('src'));
    assert.ok(file.startsWith(root + path.sep), 'Renderer scripts must be project-local');
    window.eval(fs.readFileSync(file, 'utf8') + `\n//# sourceURL=${file}`);
  }
  const $ = id => window.document.getElementById(id);
  await waitFor(() => !$('new-chat').disabled && calls.some(call => call.name === 'request' && call.args[1] === '/internal/status'), 'initialized application');
  const requests = (method, route) => calls.filter(call => call.name === 'request' && call.args[0] === method && call.args[1] === route);
  const fill = (id, value) => { $(id).value = value; $(id).dispatchEvent(new window.Event('input', { bubbles: true })); };
  const submit = draft => {
    if (draft !== undefined) fill('prompt', draft);
    $('prompt-form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  };
  return { window, $, calls, requests, fill, submit, config: () => clone(config), status, storage, poll: () => intervals.find(item => item.delay === 2000).callback(), emit: event => statusCallback(event) };
}

async function openBrowserLogin(h) {
  h.$('open-settings').click();
  await waitFor(() => !h.$('settings-view').hidden, 'settings displayed');
  h.window.document.querySelector('[data-section="browser-login"]').click();
  await waitFor(() => !h.$('detect-browser-profiles').disabled, 'browser profile detection settled');
}

test('actual renderer initializes model tabs and hides native views while validating and saving settings', async t => {
  const h = await harness(t);
  const tabs = () => [...h.$('provider-tabs').querySelectorAll('[role="tab"]')];
  assert.deepEqual(tabs().map(tab => tab.dataset.provider), ['chatgpt', 'deepseek']);
  assert.match(h.$('login-tip').textContent, /分别登录每个模型/);
  assert.equal(h.$('messages').children.length, 0, 'No prefilled model answers');
  await waitFor(() => h.calls.some(call => call.name === 'setBounds' && call.args[0]?.width > 0), 'native view bounds');
  tabs()[1].click();
  await waitFor(() => !h.$('new-chat').disabled && h.calls.some(call => call.name === 'showProvider' && call.args[0] === 'deepseek'), 'switch model tab');
  h.$('open-settings').click();
  await waitFor(() => !h.$('settings-view').hidden, 'settings displayed');
  assert.equal(h.$('workspace').hidden, true);
  assert.equal(h.calls.filter(call => call.name === 'setBounds').at(-1).args[0], null);
  const chatgptCard = h.window.document.querySelector('.provider-card[data-provider="chatgpt"]');
  const selectors = chatgptCard.querySelector('.provider-selectors');
  const originalSelectors = selectors.value;
  selectors.value = '{invalid';
  h.$('save-settings').click();
  assert.match(h.$('settings-error').textContent, /不是有效 JSON/);
  assert.equal(h.requests('PUT', '/internal/config').length, 0);
  selectors.value = originalSelectors;
  h.window.document.querySelector('.provider-card[data-provider="deepseek"] .provider-enabled').checked = false;
  h.window.document.querySelector('.provider-card[data-provider="deepseek"] .provider-enabled').dispatchEvent(new h.window.Event('change', { bubbles: true }));
  h.fill('fusion-model', 'local-synthesis-model');
  h.$('fusion-mode').value = 'api';
  h.$('fusion-mode').dispatchEvent(new h.window.Event('change', { bubbles: true }));
  assert.equal(h.$('api-fusion-fields').hidden, false);
  h.$('save-settings').click();
  await waitFor(() => h.$('settings-view').hidden, 'settings saved');
  assert.equal(h.requests('PUT', '/internal/config').length, 1);
  assert.equal(h.config().fusion.mode, 'api');
  assert.equal(h.config().fusion.model, 'local-synthesis-model');
  assert.equal(h.config().providers.find(provider => provider.id === 'deepseek').enabled, false);
  assert.deepEqual(tabs().map(tab => tab.dataset.provider), ['chatgpt']);
});

test('timing settings display migrated defaults, validate confirmation wait and preserve schema on save', async t => {
  const config = clone(configExample);
  config.schema_version = 3; config.allow_partial = false;
  config.generation.timeout_seconds = 600; config.generation.submission_timeout_seconds = 120;
  config.fusion.timeout_seconds = 600;
  const h = await harness(t, {layoutApi: true, config});
  h.$('open-settings').click();
  await waitFor(() => !h.$('settings-view').hidden, 'timing settings displayed');
  h.window.document.querySelector('[data-section="advanced"]').click();
  assert.equal(h.$('generation-submission-timeout').value, '120');
  assert.equal(h.$('generation-timeout').value, '600');
  assert.equal(h.$('generation-recovery-timeout').value, '180');
  assert.equal(h.$('fusion-timeout').value, '600');
  assert.equal(h.$('allow-partial').checked, false);
  const chatgptInterval = h.window.document.querySelector('.provider-card[data-provider="chatgpt"] .provider-access-interval');
  assert.equal(chatgptInterval.value, '0');
  chatgptInterval.value = '12.5';
  h.fill('generation-submission-timeout', '601'); h.$('save-settings').click();
  assert.match(h.$('settings-error').textContent, /接收确认等待时间不能超过 600/);
  assert.equal(h.requests('PUT', '/internal/config').length, 0);
  h.fill('generation-submission-timeout', '240'); h.fill('generation-timeout', '900');
  h.fill('generation-recovery-timeout', '601'); h.$('save-settings').click();
  assert.match(h.$('settings-error').textContent, /网页恢复等待时间不能超过 600/);
  assert.equal(h.requests('PUT', '/internal/config').length, 0);
  h.fill('generation-recovery-timeout', '0');
  h.$('save-settings').click();
  await waitFor(() => h.$('settings-view').hidden, 'timing settings saved');
  assert.equal(h.config().generation.submission_timeout_seconds, 240);
  assert.equal(h.config().generation.timeout_seconds, 900);
  assert.equal(h.config().generation.recovery_timeout_seconds, 0);
  assert.equal(h.config().generation.stable_seconds, config.generation.stable_seconds);
  assert.equal(h.config().fusion.timeout_seconds, 600);
  assert.equal(h.config().schema_version, 3);
  assert.equal(h.config().allow_partial, false);
  assert.equal(h.config().providers.find(p => p.id === 'chatgpt').access_interval_seconds, 12.5);
});

test('provider access interval validates its independent 0 to 3600 second range', async t => {
  const h = await harness(t, {config: clone(configExample)});
  h.$('open-settings').click();
  await waitFor(() => !h.$('settings-view').hidden, 'settings displayed');
  const input = h.window.document.querySelector('.provider-card[data-provider="chatgpt"] .provider-access-interval');
  input.value = '3601'; h.$('save-settings').click();
  assert.match(h.$('settings-error').textContent, /0–3600/);
  assert.equal(h.requests('PUT', '/internal/config').length, 0);
});

test('older config without confirmation field can be edited without dropping its schema or provider settings', async t => {
  const config = clone(configExample);
  config.schema_version = 1; delete config.generation.submission_timeout_seconds;
  config.generation.timeout_seconds = 450;
  config.providers.find(p => p.id === 'qwen').proxy = 'http://127.0.0.1:10822';
  const h = await harness(t, {config});
  h.$('open-settings').click();
  await waitFor(() => !h.$('settings-view').hidden, 'older config editable');
  assert.equal(h.$('generation-submission-timeout').value, '120');
  h.$('save-settings').click();
  await waitFor(() => h.$('settings-view').hidden, 'compatible timing settings saved');
  assert.equal(h.config().generation.submission_timeout_seconds, 120);
  assert.equal(h.config().generation.timeout_seconds, 450);
  assert.equal(h.config().schema_version, 1, 'Backend remains responsible for migrating older persisted schema');
  assert.equal(h.config().providers.find(p => p.id === 'qwen').proxy, 'http://127.0.0.1:10822');
});

test('waiting UI lists the remaining active model and retains the all-responses requirement', async t => {
  let resolveCompletion; const pending = new Promise(resolve => {resolveCompletion = resolve;});
  const config = clone(configExample); config.allow_partial = false;
  config.providers.forEach(p => {p.enabled = ['deepseek', 'qwen'].includes(p.id);});
  config.fusion.provider = 'deepseek';
  const h = await harness(t, {layoutApi: true, config, complete: () => pending});
  h.submit('等待慢响应');
  h.emit({type: 'provider', provider_id: 'deepseek', state: 'ready', message: '回答完成'});
  h.emit({type: 'provider', provider_id: 'qwen', state: 'submitting', message: '等待网页确认接收'});
  assert.match(h.$('run-status-text').textContent, /Qwen 正在处理/);
  assert.doesNotMatch(h.$('run-status-text').textContent, /DeepSeek/);
  assert.match(h.$('composer-note').textContent, /等待所有候选模型成功完成回答/);
  assert.equal(h.$('messages').querySelectorAll('.message.assistant').length, 0);
  assert.equal(h.requests('POST', '/v1/chat/completions').length, 1);
  resolveCompletion(response());
  await waitFor(() => !h.$('prompt').disabled, 'all response request completed');
});

test('one in-flight submission freezes mutations and safely renders/exports merged and source Markdown', async t => {
  let resolveCompletion;
  const pending = new Promise(resolve => { resolveCompletion = resolve; });
  const h = await harness(t, { complete: () => pending });
  h.submit('分析内存\n请保留 Markdown。');
  h.submit();
  assert.equal(h.requests('POST', '/v1/chat/completions').length, 1);
  for (const id of ['prompt', 'new-chat', 'open-settings', 'open-api', 'history-select']) assert.equal(h.$(id).disabled, true, `${id} locked during generation`);
  const markdown = '## 结论\n\n**已整合**\n\n```python\nprint(1)\n```\n\n<img src="x" onerror="window.pwned=1"><script>window.pwned=2</script>\n\n[危险](javascript:alert(1))';
  const source = '## 原始回答\n\n<svg onload="window.pwned=3"></svg>保留原文';
  resolveCompletion(response(markdown, { sources: [{ provider: 'chatgpt', markdown: source }], errors: [{ provider: 'deepseek', message: 'Fixture timeout' }] }));
  await waitFor(() => !h.$('prompt').disabled && h.$('messages').querySelector('.message.assistant'), 'response complete');
  const rendered = h.$('messages');
  assert.match(rendered.textContent, /已整合/);
  assert.match(rendered.textContent, /保留原文/);
  assert.match(rendered.textContent, /DeepSeek：Fixture timeout/);
  assert.equal(rendered.querySelectorAll('script,[onerror],[onload],a[href^="javascript:"]').length, 0);
  assert.equal(h.window.pwned, undefined);
  assert.equal(rendered.querySelector('pre code').textContent.trim(), 'print(1)');
  const buttons = [...rendered.querySelectorAll('button')];
  buttons.find(button => button.textContent === '导出 .md ↗').click();
  buttons.find(button => button.textContent === '导出原始 Markdown ↗').click();
  assert.deepEqual(h.calls.filter(call => call.name === 'saveMarkdown').map(call => call.args[0].content), [markdown, source]);
  assert.equal(h.$('prompt').value, '');
});

test('request failure preserves draft and does not duplicate a failed user message on retry', async t => {
  let attempt = 0;
  const h = await harness(t, { complete: async () => { if (++attempt === 1) throw new Error('fixture: 请先登录'); return response(); } });
  const draft = '  请分析 Binder 内存。\n保留这段草稿。  ';
  h.submit(draft);
  await waitFor(() => !h.$('prompt').disabled && !h.$('chat-error').hidden, 'error displayed');
  assert.equal(h.$('prompt').value, draft);
  assert.equal(h.$('messages').querySelectorAll('.message').length, 0);
  assert.match(h.$('chat-error').textContent, /请先登录/);
  h.submit();
  await waitFor(() => !h.$('prompt').disabled && h.$('messages').querySelector('.assistant'), 'retry complete');
  const posts = h.requests('POST', '/v1/chat/completions');
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[1].args[2].messages, [{ role: 'user', content: draft.trim() }]);
  assert.equal(h.$('chat-error').hidden, true);
});

test('history continuation sends the full logical transcript and new conversation clears its identity', async t => {
  const h = await harness(t);
  h.$('history-select').value = savedConversation.id;
  h.$('history-select').dispatchEvent(new h.window.Event('change', { bubbles: true }));
  await waitFor(() => !h.$('prompt').disabled && h.$('messages').querySelectorAll('.message').length === 3, 'history loaded');
  assert.match(h.$('messages').textContent, /原始 GC 分析/);
  h.submit('继续分析 Native。');
  await waitFor(() => !h.$('prompt').disabled && h.$('messages').querySelectorAll('.message').length === 5, 'history continuation');
  const continued = h.requests('POST', '/v1/chat/completions')[0].args[2];
  assert.equal(continued.conversation_id, savedConversation.id);
  assert.deepEqual(continued.messages, [...savedConversation.messages, { role: 'user', content: '继续分析 Native。' }]);
  h.$('new-chat').click();
  assert.equal(h.$('messages').children.length, 0);
  h.submit('新的问题');
  await waitFor(() => !h.$('prompt').disabled && h.requests('POST', '/v1/chat/completions').length === 2, 'new thread response');
  const fresh = h.requests('POST', '/v1/chat/completions')[1].args[2];
  assert.equal(fresh.conversation_id, undefined);
  assert.deepEqual(fresh.messages, [{ role: 'user', content: '新的问题' }]);
});

test('API credentials are requested on explicit settings entry and curl copies the correct endpoint', async t => {
  const h = await harness(t);
  assert.equal(h.calls.filter(call => call.name === 'runtimeInfo').length, 0);
  h.$('open-api').click();
  await waitFor(() => h.$('local-api-token').value.length > 0, 'API settings loaded');
  assert.equal(h.$('local-api-url').value, 'http://127.0.0.1:8765/v1');
  assert.equal(h.$('local-api-token').type, 'password');
  assert.doesNotMatch(h.$('curl-example').textContent, /fixture-secret-api-token/);
  h.$('copy-curl').click();
  const copied = h.calls.filter(call => call.name === 'copyText').at(-1).args[0];
  assert.match(copied, /http:\/\/127\.0\.0\.1:8765\/v1\/chat\/completions/);
  assert.match(copied, /Authorization: Bearer fixture-secret-api-token/);
  assert.match(copied, /\n  -H/);
  assert.doesNotMatch(copied, /\/v1\/v1\//);
});

test('API model selection updates the preview and copied command without changing chat or settings', async t => {
  const h = await harness(t);
  h.$('open-api').click();
  await waitFor(() => h.$('local-api-token').value.length > 0, 'API settings loaded');
  const select = h.$('local-api-model');
  assert.deepEqual([...select.options].map(option => option.value), ['web-fusion', 'web-chatgpt', 'web-deepseek']);
  assert.match(h.$('curl-example').textContent, /"model":"web-fusion"/);
  assert.match(h.$('local-api-model-help').textContent, /不进行融合/);
  assert.match(h.$('local-api-model-help').textContent, /chatgpt、deepseek、qwen/);
  select.value = 'web-chatgpt';
  select.dispatchEvent(new h.window.Event('input', { bubbles: true }));
  select.dispatchEvent(new h.window.Event('change', { bubbles: true }));
  assert.match(h.$('curl-example').textContent, /"model":"web-chatgpt"/);
  assert.doesNotMatch(h.$('curl-example').textContent, /fixture-secret-api-token/);
  h.$('copy-curl').click();
  const copied = h.calls.filter(call => call.name === 'copyText').at(-1).args[0];
  assert.match(copied, /"model":"web-chatgpt"/);
  assert.match(copied, /Authorization: Bearer fixture-secret-api-token/);
  assert.equal(h.$('save-settings').classList.contains('dirty-dot'), false);
  assert.equal(h.requests('PUT', '/internal/config').length, 0);
  h.$('close-settings').click();
  h.$('open-api').click();
  await waitFor(() => !h.$('settings-view').hidden, 'API settings reopened');
  assert.equal(select.value, 'web-chatgpt', 'Valid selected model survives reopening');
  h.$('close-settings').click();
  h.submit('原聊天仍使用整合模型');
  await waitFor(() => h.requests('POST', '/v1/chat/completions').length === 1 && !h.$('prompt').disabled, 'chat request completed');
  assert.equal(h.requests('POST', '/v1/chat/completions')[0].args[2].model, 'web-fusion');
});

test('API models reflect saved enabled providers and preserve only valid choices after configuration changes', async t => {
  const h = await harness(t);
  h.$('open-api').click();
  await waitFor(() => h.$('local-api-token').value.length > 0, 'API settings loaded');
  const select = h.$('local-api-model');
  select.value = 'web-deepseek';
  select.dispatchEvent(new h.window.Event('change', { bubbles: true }));
  h.window.document.querySelector('[data-section="models"]').click();
  function toggleProvider(id, enabled) {
    const toggle = h.window.document.querySelector(`.provider-card[data-provider="${id}"] .provider-enabled`);
    toggle.checked = enabled;
    toggle.dispatchEvent(new h.window.Event('input', { bubbles: true }));
    toggle.dispatchEvent(new h.window.Event('change', { bubbles: true }));
  }
  toggleProvider('deepseek', false);
  toggleProvider('qwen', true);
  h.window.document.querySelector('[data-section="api"]').click();
  assert.deepEqual([...select.options].map(option => option.value), ['web-fusion', 'web-chatgpt', 'web-deepseek'], 'Unsaved settings do not advertise models unavailable in backend');
  h.window.document.querySelector('[data-section="models"]').click();
  h.$('save-settings').click();
  await waitFor(() => h.$('settings-view').hidden, 'changed provider configuration saved');
  assert.deepEqual([...select.options].map(option => option.value), ['web-fusion', 'web-chatgpt', 'web-qwen']);
  assert.equal(select.value, 'web-fusion', 'Disabled provider falls back to fusion');
  assert.match(h.$('curl-example').textContent, /"model":"web-fusion"/);
  h.$('open-api').click();
  await waitFor(() => !h.$('settings-view').hidden, 'API settings reopened');
  select.value = 'web-qwen';
  select.dispatchEvent(new h.window.Event('change', { bubbles: true }));
  h.$('copy-curl').click();
  assert.match(h.calls.filter(call => call.name === 'copyText').at(-1).args[0], /"model":"web-qwen"/);
  h.window.document.querySelector('[data-section="models"]').click();
  toggleProvider('deepseek', true);
  h.$('save-settings').click();
  await waitFor(() => h.$('settings-view').hidden, 'another provider enabled');
  assert.equal(select.value, 'web-qwen', 'Still enabled model survives configuration refresh');
  assert.deepEqual([...select.options].map(option => option.value), ['web-fusion', 'web-chatgpt', 'web-deepseek', 'web-qwen']);
});

test('browser login discovers metadata only on explicit tab entry and imports the selected source and enabled model', async t => {
  const h = await harness(t);
  assert.equal(h.calls.filter(call => call.name === 'listBrowserProfiles').length, 0, 'No browser profile reading at startup');
  assert.equal(h.calls.filter(call => call.name === 'importBrowserLogin').length, 0, 'No automatic login import');
  await openBrowserLogin(h);
  assert.equal(h.calls.filter(call => call.name === 'listBrowserProfiles').length, 1);
  assert.equal(h.$('workspace').hidden, true);
  assert.equal(h.calls.filter(call => call.name === 'setBounds').at(-1).args[0], null);
  assert.deepEqual([...h.$('browser-profile-select').options].map(option => option.textContent), ['Firefox — default-release', 'Chrome — Default']);
  assert.deepEqual([...h.$('browser-login-provider').options].map(option => option.value), ['chatgpt', 'deepseek']);
  h.$('browser-profile-select').value = 'chrome-fixture';
  h.$('browser-profile-select').dispatchEvent(new h.window.Event('change', { bubbles: true }));
  assert.match(h.$('browser-profile-details').textContent, /Chrome · Default\n\/fixture\/\.config\/google-chrome\/Default/);
  h.$('browser-login-provider').value = 'deepseek';
  h.$('browser-login-provider').dispatchEvent(new h.window.Event('change', { bubbles: true }));
  h.$('import-browser-login').click();
  await waitFor(() => !h.$('browser-login-result').hidden && !h.$('close-settings').disabled, 'login import completed');
  assert.deepEqual(h.calls.filter(call => call.name === 'importBrowserLogin')[0].args[0], { profile_id: 'chrome-fixture', provider_id: 'deepseek' });
  assert.match(h.$('browser-login-summary').textContent, /已导入 4 项 Cookie；请在网页确认登录/);
  assert.match(h.$('browser-login-counts').textContent, /DeepSeek.*localStorage：1 项.*跳过：2 项/);
  assert.match(h.$('browser-login-warnings').textContent, /分区 Cookie/);
  assert.doesNotMatch(h.$('browser-login-result').textContent, /登录成功|已成功登录/);
  assert.equal(h.requests('PUT', '/internal/config').length, 0);
  assert.equal(h.$('save-settings').classList.contains('dirty-dot'), false, 'Profile selection is not a config edit');
  h.$('detect-browser-profiles').click();
  await waitFor(() => h.calls.filter(call => call.name === 'listBrowserProfiles').length === 2 && !h.$('detect-browser-profiles').disabled, 'profile refresh completed');
  assert.equal(h.$('browser-profile-select').value, 'chrome-fixture', 'Refresh preserves selected source');
});

test('enabled GLM and Kimi appear as explicit browser-login import targets', async t => {
  const config = clone(configExample);
  config.providers.forEach(provider => { provider.enabled = ['glm', 'kimi'].includes(provider.id); });
  config.fusion.provider = 'glm';
  const h = await harness(t, {config});
  await openBrowserLogin(h);
  assert.deepEqual([...h.$('browser-login-provider').options].map(option => option.value), ['glm', 'kimi']);
  assert.deepEqual([...h.$('browser-login-provider').options].map(option => option.textContent), ['GLM（Z.ai）', 'Kimi']);
  assert.equal(h.requests('PUT', '/internal/config').length, 0);
});

test('login import locks mutations, ignores provider status changes for hidden views and resumes after completion', async t => {
  let resolveImport;
  const pending = new Promise(resolve => { resolveImport = resolve; });
  const h = await harness(t, { importBrowserLogin: () => pending });
  await openBrowserLogin(h);
  const beforeShows = h.calls.filter(call => call.name === 'showProvider').length;
  const beforeBounds = h.calls.filter(call => call.name === 'setBounds').length;
  h.$('import-browser-login').click();
  await waitFor(() => h.calls.some(call => call.name === 'importBrowserLogin'), 'pending login import');
  for (const id of ['prompt', 'send-button', 'new-chat', 'open-settings', 'open-api', 'history-select', 'save-settings', 'discard-settings', 'reload-provider', 'close-settings', 'browser-profile-select', 'browser-login-provider', 'detect-browser-profiles', 'import-browser-login', 'import-login-file']) {
    assert.equal(h.$(id).disabled, true, `${id} locked during import`);
  }
  assert.equal(h.window.document.querySelector('.provider-url').disabled, true);
  h.submit('导入时不应发送');
  h.$('settings-form').dispatchEvent(new h.window.Event('submit', { bubbles: true, cancelable: true }));
  h.$('brand-home').click();
  h.emit({ type: 'provider', provider_id: 'chatgpt', state: 'ready', message: 'Fixture state changed' });
  h.emit({ type: 'browser-maintenance', active: true });
  h.emit({ type: 'browser-maintenance', active: false });
  h.$('provider-tabs').querySelector('[data-provider="deepseek"]').click();
  await new Promise(resolve => setTimeout(resolve, 35));
  assert.equal(h.requests('POST', '/v1/chat/completions').length, 0);
  assert.equal(h.requests('PUT', '/internal/config').length, 0);
  assert.equal(h.$('settings-view').hidden, false);
  assert.equal(h.$('close-settings').disabled, true, 'Import remains locked even if a maintenance event is released early');
  assert.equal(h.calls.filter(call => call.name === 'showProvider').length, beforeShows);
  assert.ok(h.calls.filter(call => call.name === 'setBounds').slice(beforeBounds).every(call => call.args[0] === null));
  resolveImport(clone(loginReport));
  await waitFor(() => !h.$('close-settings').disabled && !h.$('browser-login-result').hidden, 'import lock released');
  assert.equal(h.$('browser-login-progress').hidden, true);
  h.$('close-settings').click();
  await waitFor(() => h.calls.filter(call => call.name === 'setBounds').at(-1).args[0]?.width > 0, 'native views restored after returning');
  assert.equal(h.$('new-chat').disabled, false);
  h.submit('导入完成后发送');
  await waitFor(() => h.requests('POST', '/v1/chat/completions').length === 1 && !h.$('prompt').disabled, 'chat resumed');
});

test('missing profiles and crypto dependency retain an actionable extension-file fallback', async t => {
  const h = await harness(t, { listBrowserProfiles: async () => ({ ok: true, profiles: [], warnings: [{ code: 'profile_unavailable', message: '一个配置目录无法访问。' }], capabilities: { chromium_decryption: false } }) });
  await openBrowserLogin(h);
  assert.equal(h.$('browser-profiles-empty').hidden, false);
  assert.match(h.$('browser-profiles-empty').textContent, /Snap、Flatpak/);
  assert.equal(h.$('browser-crypto-guidance').hidden, false);
  assert.equal(h.$('import-browser-login').disabled, true);
  assert.equal(h.$('import-login-file').disabled, false);
  assert.match(h.$('browser-profiles-warnings').textContent, /无法访问/);
  assert.match(h.$('browser-login-section').textContent, /browser-extension\/chromium/);
  assert.match(h.$('browser-login-section').textContent, /browser-extension\/firefox\/manifest\.json/);
  h.$('import-login-file').click();
  await waitFor(() => !h.$('browser-login-result').hidden && !h.$('close-settings').disabled, 'fallback file imported');
  assert.deepEqual(h.calls.filter(call => call.name === 'importLoginFile')[0].args[0], { provider_id: 'chatgpt' });
  assert.equal(h.calls.filter(call => call.name === 'importBrowserLogin').length, 0);
});

test('canceled login-file selection restores controls without claiming a result', async t => {
  const h = await harness(t, { importLoginFile: async () => ({ canceled: true }) });
  await openBrowserLogin(h);
  h.$('import-login-file').click();
  await waitFor(() => h.calls.some(call => call.name === 'importLoginFile') && !h.$('close-settings').disabled, 'file dialog canceled');
  assert.equal(h.$('browser-login-result').hidden, true);
  assert.equal(h.$('browser-login-error').hidden, true);
  assert.match(h.$('toast').textContent, /已取消/);
  assert.equal(h.$('import-login-file').disabled, false);
});

test('browser-helper failures are visible, retryable, and do not lock the application', async t => {
  let attempt = 0;
  const h = await harness(t, {
    listBrowserProfiles: async () => ++attempt === 1 ? { ok: false, error: { code: 'unavailable', message: 'Fixture 配置目录不可访问' } } : clone(browserProfiles),
    importBrowserLogin: async () => { throw new Error('Fixture 系统密钥环不可用'); },
  });
  await openBrowserLogin(h);
  assert.match(h.$('browser-login-error').textContent, /配置目录不可访问/);
  h.$('detect-browser-profiles').click();
  await waitFor(() => !h.$('import-browser-login').disabled, 'retry detection ready');
  h.$('import-browser-login').click();
  await waitFor(() => /系统密钥环不可用/.test(h.$('browser-login-error').textContent) && !h.$('close-settings').disabled, 'import failure restored controls');
  assert.match(h.$('browser-login-error').textContent, /配套扩展/);
  assert.equal(h.$('browser-login-result').hidden, true);
  assert.equal(h.$('import-browser-login').disabled, false);
  assert.equal(h.$('new-chat').disabled, false);
});

test('invalid import report never renders raw cookie fields or a false login claim', async t => {
  const h = await harness(t, { importBrowserLogin: async () => ({ provider_id: 'chatgpt', imported: '4', cookies: [{ name: 'fixture-session-name', value: 'fixture-session-secret' }], authenticated: true }) });
  await openBrowserLogin(h);
  h.$('import-browser-login').click();
  await waitFor(() => !h.$('browser-login-error').hidden && !h.$('close-settings').disabled, 'invalid report rejected');
  assert.match(h.$('browser-login-error').textContent, /结果格式异常/);
  assert.equal(h.$('browser-login-result').hidden, true);
  assert.doesNotMatch(h.window.document.body.textContent, /fixture-session-name|fixture-session-secret|登录成功/);
});


const layoutKey = 'multillm-fusion.layout.v2';
const legacyLayoutKey = 'multillm-fusion.layout.v1';
const lastLayout = h => h.calls.filter(call => call.name === 'setLayout').at(-1)?.args[0];
async function chooseLayout(h, mode) {
  h.$('layout-mode').value = mode;
  h.$('layout-mode').dispatchEvent(new h.window.Event('change', {bubbles: true}));
  await waitFor(() => !h.$('layout-mode').disabled && lastLayout(h)?.mode === mode, `layout ${mode} applied`);
}
function pointer(h, element, type, props = {}) {
  const event = new h.window.Event(type, {bubbles: true, cancelable: true});
  Object.assign(event, {button: 0, pointerId: 7, clientX: 900, ...props}); element.dispatchEvent(event);
}

test('new installation defaults to two side-by-side native panes and persists the accepted layout', async t => {
  const h = await harness(t, {layoutApi: true});
  assert.equal(h.$('layout-mode').value, 'split');
  assert.equal(h.$('provider-tabs').hidden, true);
  assert.equal(h.$('model-pane-1').hidden, false);
  assert.deepEqual(lastLayout(h).panes.map(pane => pane.provider_id), ['chatgpt', 'deepseek']);
  assert.equal(JSON.parse(h.storage.get(layoutKey)).mode, 'split');
});

test('legacy tabs upgrade to split once while preserving selected providers and pane widths', async t => {
  const config = clone(configExample); config.providers.find(p => p.id === 'qwen').enabled = true;
  const legacy = {mode: 'tabs', providers: ['qwen', 'deepseek'], activeProvider: 'qwen', chatShare: 29, paneShare: 64};
  const storage = new Map([[legacyLayoutKey, JSON.stringify(legacy)]]);
  const h = await harness(t, {layoutApi: true, config, storage});
  assert.equal(lastLayout(h).mode, 'split');
  assert.deepEqual(lastLayout(h).panes.map(pane => pane.provider_id), ['qwen', 'deepseek']);
  assert.equal(lastLayout(h).active_provider, 'qwen');
  assert.equal(h.$('workspace').style.getPropertyValue('--chat-share'), '29fr');
  assert.equal(h.$('webview-group').style.getPropertyValue('--left-share'), '64fr');
  assert.equal(JSON.parse(storage.get(layoutKey)).mode, 'split');
  await chooseLayout(h, 'tabs');
  const restarted = await harness(t, {layoutApi: true, config, storage});
  assert.equal(lastLayout(restarted).mode, 'tabs', 'Explicit choice survives later starts despite old split migration');
  assert.deepEqual(lastLayout(restarted).panes.map(pane => pane.provider_id), ['qwen']);
  await chooseLayout(restarted, 'windows');
  const windowsRestarted = await harness(t, {layoutApi: true, config, storage});
  assert.equal(lastLayout(windowsRestarted).mode, 'windows', 'Explicit independent windows also survive restart');
});

test('default split falls back to one enabled model without claiming a second native pane', async t => {
  const config = clone(configExample); config.providers.forEach(p => {p.enabled = p.id === 'qwen';});
  const h = await harness(t, {layoutApi: true, config});
  assert.equal(lastLayout(h).mode, 'tabs');
  assert.deepEqual(lastLayout(h).panes.map(pane => pane.provider_id), ['qwen']);
  assert.equal(h.$('model-pane-1').hidden, true);
  assert.equal(h.$('layout-mode').querySelector('[value="split"]').disabled, true);
});

test('native layout restores persisted split choices and forwards distinct measured content rectangles', async t => {
  const config = clone(configExample); config.providers.find(p => p.id === 'qwen').enabled = true;
  const storage = new Map([[layoutKey, JSON.stringify({mode: 'split', providers: ['qwen', 'deepseek'], activeProvider: 'qwen', chatShare: 31, paneShare: 57})]]);
  const h = await harness(t, {layoutApi: true, config, storage, rects: {
    'webview-placeholder': {x: 450, y: 260, width: 360, height: 550}, 'webview-placeholder-1': {x: 822, y: 260, width: 420, height: 550},
  }});
  assert.equal(h.$('layout-mode').value, 'split'); assert.equal(h.$('provider-tabs').hidden, true);
  assert.equal(h.$('pane-provider-0').value, 'qwen'); assert.equal(h.$('pane-provider-1').value, 'deepseek');
  assert.deepEqual(lastLayout(h).panes, [
    {provider_id: 'qwen', bounds: {x: 451, y: 261, width: 358, height: 548}},
    {provider_id: 'deepseek', bounds: {x: 823, y: 261, width: 418, height: 548}},
  ]);
  assert.equal(lastLayout(h).active_provider, 'qwen'); assert.equal(lastLayout(h).hidden, false);
  assert.equal(h.$('workspace').style.getPropertyValue('--chat-share'), '31fr');
  assert.equal(h.$('webview-group').style.getPropertyValue('--left-share'), '57fr');
  assert.equal(h.calls.filter(call => call.name === 'showProvider' || call.name === 'setBounds').length, 0);
});

test('layout transitions and distinct pane selection persist without changing chat API model', async t => {
  const config = clone(configExample); config.providers.find(p => p.id === 'qwen').enabled = true;
  const h = await harness(t, {layoutApi: true, config});
  await chooseLayout(h, 'split');
  const first = h.$('pane-provider-0'), second = h.$('pane-provider-1');
  assert.equal(first.querySelector('[value="deepseek"]').disabled, true);
  first.value = 'deepseek'; first.dispatchEvent(new h.window.Event('change', {bubbles: true}));
  assert.equal(first.value, 'chatgpt', 'Cannot bind one WebContents to both panes');
  second.value = 'qwen'; second.dispatchEvent(new h.window.Event('change', {bubbles: true}));
  await waitFor(() => !second.disabled && lastLayout(h).panes[1]?.provider_id === 'qwen', 'Qwen second pane selected');
  assert.equal(lastLayout(h).active_provider, 'qwen');
  assert.deepEqual(JSON.parse(h.storage.get(layoutKey)).providers, ['chatgpt', 'qwen']);
  assert.match(h.$('layout-active-provider').textContent, /Qwen/);
  await chooseLayout(h, 'tabs');
  assert.equal(lastLayout(h).panes.length, 1); assert.equal(lastLayout(h).panes[0].provider_id, 'qwen');
  h.submit('布局切换不增加请求');
  await waitFor(() => !h.$('prompt').disabled && h.requests('POST', '/v1/chat/completions').length === 1, 'single chat request');
  assert.equal(h.requests('POST', '/v1/chat/completions')[0].args[2].model, 'web-fusion');
});

test('disabled and stale saved pane choices fall back to enabled providers without duplicate native hosts', async t => {
  const config = clone(configExample); config.providers.find(p => p.id === 'deepseek').enabled = false;
  const h = await harness(t, {layoutApi: true, config, storage: new Map([[layoutKey, JSON.stringify({mode: 'split', providers: ['missing', 'qwen'], activeProvider: 'missing', chatShare: -200, paneShare: 500})]])});
  assert.equal(h.$('layout-mode').value, 'tabs'); assert.equal(h.$('layout-mode').querySelector('[value="split"]').disabled, true);
  assert.deepEqual(lastLayout(h).panes.map(p => p.provider_id), ['chatgpt']);
  assert.equal(h.$('workspace-splitter').getAttribute('aria-valuenow'), '20');
  assert.equal(h.$('panes-splitter').getAttribute('aria-valuenow'), '80');
});

test('window mode restores only on explicit action, focuses selected windows, and stays visible when main document hides', async t => {
  const h = await harness(t, {layoutApi: true});
  await chooseLayout(h, 'windows');
  assert.equal(h.$('windows-placeholder').hidden, false); assert.equal(h.$('webview-group').hidden, true);
  assert.deepEqual(lastLayout(h).panes, []); assert.equal(lastLayout(h).reopen_windows, undefined);
  const beforeReopen = h.calls.length;
  h.$('show-model-windows').click();
  // Reopen is a one-shot command, not persistent layout state. A pending frame
  // can publish ordinary bounds before the polling assertion sees the command.
  h.window.document.dispatchEvent(new h.window.Event('visibilitychange'));
  await waitFor(() => {
    const layouts = h.calls.slice(beforeReopen).filter(call => call.name === 'setLayout');
    return layouts.some(call => call.args[0].reopen_windows === true)
      && layouts.at(-1)?.args[0].reopen_windows === undefined;
  }, 'explicit window reopen followed by ordinary layout notification');
  assert.equal(h.calls.slice(beforeReopen).filter(call => call.name === 'setLayout' && call.args[0].reopen_windows === true).length, 1,
    'Passive layout updates must not repeat an explicit window reopen');
  const before = h.calls.filter(c => c.name === 'setLayout').length;
  Object.defineProperty(h.window.document, 'hidden', {value: true, configurable: true});
  h.window.document.dispatchEvent(new h.window.Event('visibilitychange'));
  await waitFor(() => h.calls.filter(c => c.name === 'setLayout').length > before, 'minimized main visibility notification');
  assert.equal(lastLayout(h).hidden, false); assert.equal(lastLayout(h).reopen_windows, undefined);
  h.$('provider-tabs').querySelector('[data-provider="deepseek"]').click();
  await waitFor(() => !h.$('layout-mode').disabled && h.calls.some(c => c.name === 'showProvider' && c.args[0] === 'deepseek'), 'explicit native window focus');
  h.emit({type: 'layout-window-closed', provider_id: 'qwen'});
  assert.match(h.$('toast').textContent, /重新显示窗口/);
});

test('pointer resize hides native views, preserves widths, restores after release, and keyboard uses constrained widths', async t => {
  const h = await harness(t, {layoutApi: true, rects: {workspace: {x: 0, y: 80, width: 1500, height: 800}, 'webview-group': {x: 500, y: 230, width: 900, height: 620}}});
  await chooseLayout(h, 'split');
  pointer(h, h.$('panes-splitter'), 'pointerdown', {clientX: 950});
  await waitFor(() => lastLayout(h).hidden, 'views hidden while resizing');
  assert.equal(h.$('resize-overlay').hidden, false);
  pointer(h, h.$('resize-overlay'), 'pointermove', {clientX: 1050});
  assert.ok(Number(h.$('panes-splitter').getAttribute('aria-valuenow')) > 50);
  pointer(h, h.$('resize-overlay'), 'pointerup', {clientX: 1050});
  await waitFor(() => !lastLayout(h).hidden, 'resized views restored');
  assert.equal(h.$('resize-overlay').hidden, true);
  assert.ok(JSON.parse(h.storage.get(layoutKey)).paneShare > 50);
  const first = Number(h.$('workspace-splitter').getAttribute('aria-valuenow'));
  h.$('workspace-splitter').dispatchEvent(new h.window.KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
  assert.equal(Number(h.$('workspace-splitter').getAttribute('aria-valuenow')), first + 2);
  h.$('workspace-splitter').dispatchEvent(new h.window.KeyboardEvent('keydown', {key: 'End', bubbles: true}));
  assert.ok(Number(h.$('workspace-splitter').getAttribute('aria-valuenow')) <= 80);
});

test('running request locks layout, pane selection and resizing; temporary input visibility does not persist a tab change', async t => {
  let resolveCompletion; const pending = new Promise(resolve => {resolveCompletion = resolve;});
  const h = await harness(t, {layoutApi: true, complete: () => pending});
  await chooseLayout(h, 'split');
  const before = JSON.parse(h.storage.get(layoutKey));
  h.submit('只发送一次');
  for (const id of ['layout-mode', 'pane-provider-0', 'pane-provider-1', 'diagnose-send', 'show-model-windows']) assert.equal(h.$(id).disabled, true);
  assert.equal(h.$('workspace-splitter').getAttribute('aria-disabled'), 'true');
  pointer(h, h.$('workspace-splitter'), 'pointerdown');
  assert.equal(h.$('resize-overlay').hidden, true);
  h.$('workspace-splitter').dispatchEvent(new h.window.KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
  h.$('layout-mode').value = 'windows'; h.$('layout-mode').dispatchEvent(new h.window.Event('change'));
  assert.equal(h.$('layout-mode').value, 'split');
  h.emit({type: 'input-visibility', provider_id: 'qwen', active: true});
  assert.match(h.$('input-visibility-note').textContent, /正在向 Qwen 发送/);
  assert.deepEqual(JSON.parse(h.storage.get(layoutKey)), before);
  h.emit({type: 'input-visibility', provider_id: 'qwen', active: false});
  assert.equal(h.$('input-visibility-note').hidden, true);
  h.submit(); assert.equal(h.requests('POST', '/v1/chat/completions').length, 1);
  resolveCompletion(response()); await waitFor(() => !h.$('layout-mode').disabled, 'generation releases controls');
});

test('send diagnostics capture visible page before hiding, copy report, and restore split layout on close', async t => {
  let resolveReport; const pending = new Promise(resolve => {resolveReport = resolve;});
  const h = await harness(t, {layoutApi: true, diagnoseSend: () => pending});
  await chooseLayout(h, 'split');
  const first = h.calls.length;
  h.$('pane-provider-1').focus(); h.$('diagnose-send').click();
  await waitFor(() => h.calls.some(c => c.name === 'diagnoseSend'), 'read only diagnostic requested');
  assert.equal(h.calls.filter(c => c.name === 'diagnoseSend').at(-1).args[0], 'deepseek');
  assert.equal(h.calls.slice(first).some(c => c.name === 'setLayout' && c.args[0].hidden), false, 'Visibility preserved during inspection');
  assert.equal(h.$('send-diagnostic-dialog').hidden, true);
  const report = {provider_id: 'deepseek', send: {found: false, reason: 'occluded'}, input: {found: true}};
  resolveReport(report);
  await waitFor(() => !h.$('send-diagnostic-dialog').hidden, 'diagnostic dialog open');
  assert.equal(lastLayout(h).hidden, true); assert.match(h.$('send-diagnostic-json').textContent, /occluded/);
  h.$('copy-send-diagnostic').click(); assert.deepEqual(JSON.parse(h.calls.filter(c => c.name === 'copyText').at(-1).args[0]), report);
  assert.equal(h.requests('POST', '/v1/chat/completions').length, 0);
  h.$('send-diagnostic-dialog').dispatchEvent(new h.window.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  await waitFor(() => !lastLayout(h).hidden, 'native views restored after diagnostics');
  assert.equal(lastLayout(h).mode, 'split'); assert.equal(h.$('send-diagnostic-dialog').hidden, true);
});

test('rejected native mode change restores prior mode and does not persist an unusable layout', async t => {
  const h = await harness(t, {layoutApi: true, setLayout: async packet => {if (packet.mode === 'windows') throw new Error('fixture native window unavailable'); return {ok: true};}});
  h.$('layout-mode').value = 'windows'; h.$('layout-mode').dispatchEvent(new h.window.Event('change'));
  await waitFor(() => !h.$('layout-mode').disabled, 'mode error controls restored');
  assert.equal(h.$('layout-mode').value, 'split'); assert.match(h.$('toast').textContent, /native window unavailable/);
  assert.notEqual(JSON.parse(h.storage.get(layoutKey) || '{}').mode, 'windows');
  await waitFor(() => lastLayout(h).mode === 'split', 'prior native layout restored');
});

test('settings and login import hide every native pane without changing a saved split mode', async t => {
  const h = await harness(t, {layoutApi: true}); await chooseLayout(h, 'split');
  await openBrowserLogin(h); assert.equal(lastLayout(h).hidden, true); assert.equal(lastLayout(h).panes.length, 2);
  h.$('import-browser-login').click();
  await waitFor(() => !h.$('browser-login-result').hidden && !h.$('close-settings').disabled, 'import finished');
  assert.equal(lastLayout(h).hidden, true); assert.equal(lastLayout(h).mode, 'split');
  assert.equal(h.calls.filter(c => c.name === 'setBounds').length, 0, 'No legacy bounds call overrides the split host');
  h.$('close-settings').click(); await waitFor(() => !lastLayout(h).hidden, 'split mode restored');
  assert.equal(lastLayout(h).panes.length, 2);
});


test('explicit window recovery remains available while an external API job is waiting but is blocked for import and dialogs', async t => {
  const h = await harness(t, {layoutApi: true});
  await chooseLayout(h, 'windows');
  const original = clone(lastLayout(h));
  h.emit({type: 'layout-window-closed', provider_id: 'deepseek'});
  h.status.busy = true; await h.poll();
  assert.equal(h.$('layout-mode').disabled, true, 'Mode mutations remain blocked by API job');
  assert.equal(h.$('show-model-windows').disabled, false, 'Recovery must remain available for a pending input lease');
  h.$('show-model-windows').click();
  await waitFor(() => lastLayout(h).reopen_windows === true, 'explicit recovery sent while busy');
  assert.equal(lastLayout(h).mode, original.mode);
  assert.equal(lastLayout(h).active_provider, original.active_provider);
  assert.deepEqual(lastLayout(h).panes, original.panes);
  assert.equal(lastLayout(h).hidden, false);
  assert.equal(h.requests('POST', '/v1/chat/completions').length, 0, 'Window recovery never reissues chat');
  h.emit({type: 'browser-maintenance', active: true});
  assert.equal(h.$('show-model-windows').disabled, true, 'Import still blocks recovery');
  h.emit({type: 'browser-maintenance', active: false});
  assert.equal(h.$('show-model-windows').disabled, false);
  h.status.busy = false; await h.poll();
  h.$('diagnose-send').click();
  await waitFor(() => !h.$('send-diagnostic-dialog').hidden, 'diagnostic modal opened');
  assert.equal(h.$('show-model-windows').disabled, true, 'Diagnostic modal still hides windows');
});

test('split pane focus changes only inspection target so busy resize packets preserve the acknowledged host selection', async t => {
  const h = await harness(t, {layoutApi: true}); await chooseLayout(h, 'split');
  const hostActive = lastLayout(h).active_provider;
  const saved = h.storage.get(layoutKey);
  h.$('pane-provider-1').focus();
  assert.match(h.$('layout-active-provider').textContent, /DeepSeek/);
  assert.equal(h.storage.get(layoutKey), saved, 'Inspection focus is not a persisted host layout mutation');
  h.$('reload-provider').click();
  await waitFor(() => h.calls.some(c => c.name === 'reloadProvider' && c.args[0] === 'deepseek'), 'reload uses locally inspected pane');
  h.status.busy = true; await h.poll();
  const count = h.calls.filter(c => c.name === 'setLayout').length;
  h.window.dispatchEvent(new h.window.Event('resize'));
  await waitFor(() => h.calls.filter(c => c.name === 'setLayout').length > count, 'native geometry updated during API job');
  assert.equal(lastLayout(h).active_provider, hostActive, 'Same-state packet retains host active provider');
  assert.equal(lastLayout(h).mode, 'split');
  assert.deepEqual(lastLayout(h).panes.map(p => p.provider_id), ['chatgpt', 'deepseek']);
  h.status.busy = false; await h.poll();
  h.$('diagnose-send').click();
  await waitFor(() => !h.$('send-diagnostic-dialog').hidden, 'inspection report opened');
  assert.equal(h.calls.filter(c => c.name === 'diagnoseSend').at(-1).args[0], 'deepseek');
  assert.equal(lastLayout(h).active_provider, hostActive, 'Hiding for inspection also preserves host selection');
});

test('busy renderer offers explicit recovery for each manual-wait provider and retains the opened tab on resize', async t => {
  const storage = new Map([['multillm-fusion.layout.v2', JSON.stringify({mode: 'tabs', providers: ['chatgpt','deepseek'], activeProvider:'chatgpt'})]]);
  const h = await harness(t, {layoutApi:true, storage});
  h.status.busy = true;
  h.status.providers.deepseek = {state:'manual_retry_required', message:'请在网页重试'};
  h.status.providers.chatgpt = {state:'manual_retry_required', message:'请在网页重试'};
  await h.poll();
  assert.equal(h.$('layout-mode').disabled, true);
  assert.ok([...h.$('provider-tabs').querySelectorAll('button')].every(button => button.disabled));
  const target = () => h.$('recovery-actions').querySelector('[data-recovery-provider="deepseek"]');
  assert.equal(h.$('recovery-actions').hidden, false); assert.equal(h.$('recovery-actions').children.length, 2); assert.equal(target().disabled, false);
  const priorReloads = h.calls.filter(call => call.name === 'reloadProvider').length;
  target().click();
  await waitFor(() => h.$('layout-active-provider').textContent.includes('DeepSeek') && !target().disabled, 'recovery tab opened');
  h.window.dispatchEvent(new h.window.Event('resize'));
  await waitFor(() => h.calls.filter(call => call.name === 'setLayout').at(-1).args[0].active_provider === 'deepseek', 'recovery tab retained during resize');
  const packet = h.calls.filter(call => call.name === 'setLayout').at(-1).args[0];
  assert.equal(packet.panes[0].provider_id, 'deepseek');
  assert.equal(h.calls.filter(call => call.name === 'openRecoveryProvider').length, 1);
  assert.equal(h.calls.filter(call => call.name === 'reloadProvider').length, priorReloads);
  assert.equal(h.requests('POST','/v1/chat/completions').length, 0);
  assert.equal(h.requests('PUT','/internal/config').length, 0);
  assert.equal(h.$('layout-mode').disabled, true);
  h.emit({type:'provider',provider_id:'deepseek',state:'recovering',message:'恢复中'});
  assert.equal(target(), null);
});

test('renderer recovery access remains disabled during input lease and browser import', async t => {
  const h = await harness(t,{layoutApi:true});
  h.emit({type:'provider',provider_id:'deepseek',state:'manual_retry_required',message:'重试'});
  const target=()=>h.$('recovery-actions').querySelector('[data-recovery-provider="deepseek"]');
  h.emit({type:'input-visibility',provider_id:'chatgpt',active:true});
  assert.equal(target().disabled,true); target().click();
  assert.equal(h.calls.filter(call=>call.name==='openRecoveryProvider').length,0);
  h.emit({type:'input-visibility',provider_id:'chatgpt',active:false}); assert.equal(target().disabled,false);
  h.emit({type:'browser-maintenance',active:true}); assert.equal(target().disabled,true); target().click();
  assert.equal(h.calls.filter(call=>call.name==='openRecoveryProvider').length,0);
  h.emit({type:'browser-maintenance',active:false}); assert.equal(target().disabled,false);
});

test('rejected stale recovery request preserves the current renderer layout and reports the reason', async t => {
  const storage = new Map([['multillm-fusion.layout.v2', JSON.stringify({mode:'tabs', providers:['chatgpt','deepseek'], activeProvider:'chatgpt'})]]);
  const h=await harness(t,{layoutApi:true,storage,openRecoveryProvider:()=>{throw new Error('该任务已经结束');}});
  h.emit({type:'provider',provider_id:'deepseek',state:'manual_retry_required',message:'重试'});
  h.$('recovery-actions').querySelector('button').click();
  await waitFor(()=>h.$('toast').textContent.includes('该任务已经结束'),'recovery refusal displayed');
  assert.match(h.$('layout-active-provider').textContent,/ChatGPT/);
  assert.equal(h.calls.filter(call=>call.name==='reloadProvider').length,0);
});
