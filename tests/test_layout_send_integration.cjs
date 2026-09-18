'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { JSDOM } = require('jsdom');
const { ProviderLayout } = require('../electron/provider-layout.cjs');
const { WebsiteAdapter } = require('../electron/adapter.cjs');
const defaults = require('../config.example.json').providers;

// This integrates the real host scheduler, adapter, and serialized DOM function.
// Native Electron/CDP and the model websites are synthetic: dispatch is rejected
// unless the actual scheduled view is visible and owns the shared native focus.
function environment(mode = 'tabs', options = {}) {
  const firstProvider = options.firstProvider || 'qwen';
  const state = { focus: null, critical: null, events: [], violations: [], windows: [], models: new Map() };
  function host(title, visible = true) {
    const window = new EventEmitter();
    Object.assign(window, {
      title, visible, destroyed: false, children: [], activationCalls: 0,
      isDestroyed() { return this.destroyed; },
      isVisible() { return this.visible; },
      isMinimized: () => false,
      getContentSize: () => [1280, 800],
      setMenuBarVisibility() {}, setTitle(value) { this.title = value; },
      showInactive() { this.visible = true; }, hide() { this.visible = false; },
      focus() { this.activationCalls++; }, show() { this.activationCalls++; this.visible = true; },
      destroy() { this.destroyed = true; this.visible = false; },
    });
    window.contentView = {
      addChildView(view) {
        window.children = window.children.filter(child => child !== view);
        window.children.push(view); view.host = window;
      },
      removeChildView(view) { window.children = window.children.filter(child => child !== view); view.host = null; },
    };
    state.windows.push(window);
    return window;
  }
  const main = host('main');
  const controllers = new Map();
  const layout = new ProviderLayout({
    mainWindow: main,
    createWindow: ({ title }) => host(title, false),
    getFocusedWebContents: () => state.focus,
    onInputInterrupted: job => controllers.get(job.provider_id)?.abort('input_view_hidden'),
    log(event, fields) {
      state.events.push({ event, ...fields });
      if (event === 'input_lease.acquired') {
        if (state.critical) state.violations.push(`Overlapping input: ${state.critical} and ${fields.provider_id}`);
        state.critical = fields.provider_id;
      }
      if (event === 'input_lease.released') {
        if (state.critical !== fields.provider_id) state.violations.push(`Incorrect release: ${fields.provider_id}`);
        state.critical = null;
      }
    },
  });
  state.layout = layout;

  function model(id) {
    const provider = { ...defaults.find(candidate => candidate.id === id), url: `https://${id}.invalid/` };
    const record = { id, dom: null, clicks: 0, loads: 0, inserted: [], sent: [], commands: [], actions: [], emulated: false, destroyed: false };
    const view = {
      host: null, visible: false, bounds: { x: 0, y: 0, width: 880, height: 650 },
      getVisible() { return this.visible; }, setVisible(value) { this.visible = value; },
      getBounds() { return { ...this.bounds }; }, setBounds(value) { this.bounds = { ...value }; },
    };
    const interactive = operation => {
      assert.equal(view.getVisible(), true, `${id}: ${operation} requires a visible native view`);
      assert.equal(view.host?.isVisible(), true, `${id}: ${operation} requires a visible host`);
      assert.equal(state.focus, wc, `${id}: ${operation} requires native focus`);
      assert.equal(layout.lease?.item.view, view, `${id}: ${operation} requires this model's input lease`);
      assert.equal(state.critical, id, `${id}: ${operation} may not overlap another model's input`);
    };
    const debug = new EventEmitter();
    Object.assign(debug, {
      attached: false,
      isAttached() { return this.attached; },
      attach() { assert.equal(this.attached, false); this.attached = true; },
      detach() { assert.equal(this.attached, true); this.attached = false; },
      async sendCommand(method, params = {}) {
        assert.equal(this.attached, true);
        record.commands.push({ method, ...params });
        if (method === 'Network.enable') return {};
        if (method === 'Emulation.setFocusEmulationEnabled') { record.emulated = params.enabled; return {}; }
        interactive(method);
        assert.equal(record.emulated, true, 'Focus emulation must be established before native input');
        const w = record.dom.window;
        if (method === 'Input.insertText') {
          const input = w.document.activeElement;
          assert.equal(input.id, id === 'chatgpt' ? 'prompt-textarea' : 'chat-input');
          assert.equal('value' in input ? input.value : input.textContent, '', 'Input must be inserted only once');
          record.inserted.push(params.text);
          if (id === 'chatgpt') {
            input.replaceChildren(...params.text.replace(/\r\n?/g, '\n').split('\n').map(line => {
              const paragraph = w.document.createElement('p'); paragraph.textContent = line;
              if (!line) paragraph.innerHTML = '<br class="ProseMirror-trailingBreak">';
              return paragraph;
            }));
            record.nativeEdited = true;
          } else input.value = params.text;
          input.dispatchEvent(new w.InputEvent('input', { bubbles: true, inputType: 'insertText', data: params.text }));
        } else if (method === 'Input.dispatchMouseEvent') {
          const button = w.document.querySelector('button');
          assert.equal(w.document.elementFromPoint(params.x, params.y), button, 'Hit-test the actual target coordinates');
          if (params.type === 'mousePressed' && options.cancelOnPress === id) controllers.get(id).abort();
          if (params.type === 'mouseReleased') {
            assert.equal(button.disabled, false);
            button.click();
          }
        } else {
          assert.fail(`Unexpected input or fallback: ${method}`);
        }
        return {};
      },
    });
    const wc = {
      debugger: debug,
      isDestroyed: () => record.destroyed,
      isFocused: () => state.focus === wc,
      focus() { state.focus = wc; }, stop() {}, close() { record.destroyed = true; },
      async loadURL(url) {
        assert.equal(url, provider.url);
        record.loads++;
        record.dom?.window.close();
        const markdownClass = id === 'qwen' ? 'qwen-markdown' : 'ds-markdown';
        const composer = id === 'chatgpt' ? '<textarea data-id="root" hidden></textarea><div id="prompt-textarea" class="ProseMirror" contenteditable="true"><p><br class="ProseMirror-trailingBreak"></p></div>' : '<textarea id="chat-input"></textarea>';
        record.dom = new JSDOM(`<form>${composer}<button ${id === 'chatgpt' ? 'id="composer-submit-button"' : ''} class="send-button" aria-label="Send" type="button" disabled>↑</button></form>${id === 'qwen' ? '' : `<article data-role="assistant" data-message-author-role="assistant"><div class="${markdownClass}">Old reply</div></article>`}`, { url, runScripts: 'outside-only' });
        const w = record.dom.window, doc = w.document;
        Object.defineProperty(doc, 'visibilityState', { get: () => view.visible && view.host?.visible ? 'visible' : 'hidden' });
        Object.defineProperty(doc, 'hidden', { get: () => doc.visibilityState !== 'visible' });
        doc.hasFocus = () => doc.visibilityState === 'visible' && state.focus === wc;
        Object.defineProperty(w, 'innerWidth', { get: () => view.bounds.width });
        Object.defineProperty(w, 'innerHeight', { get: () => view.bounds.height });
        w.HTMLElement.prototype.getClientRects = function () { return this.isConnected ? [this.getBoundingClientRect()] : []; };
        w.HTMLElement.prototype.getBoundingClientRect = function () {
          return this.tagName === 'BUTTON' ? { left: 300, top: 210, width: 50, height: 40 } : { left: 20, top: 100, width: 250, height: 100 };
        };
        doc.elementFromPoint = (x, y) => x >= 300 && x < 350 && y >= 210 && y < 250 ? doc.querySelector('button') : doc.body;
        const input = doc.querySelector(id === 'chatgpt' ? '#prompt-textarea' : 'textarea'), button = doc.querySelector('button');
        const value = () => id === 'chatgpt' ? [...input.querySelectorAll('p')].map(p => p.textContent).join('\n') : input.value;
        if (id === 'chatgpt') Object.defineProperty(input, 'innerText', { get: () => [...input.querySelectorAll('p')].map(p => p.textContent).join('\n\n') });
        input.addEventListener('input', () => { interactive('editor input event'); button.disabled = !value().trim() || id === 'chatgpt' && !record.nativeEdited; });
        button.addEventListener('click', () => {
          interactive('site send click');
          record.clicks++;
          record.sent.push(value());
          if (options.unconfirmed === id) return; // The site receives a click but accepts no request.
          if (options.streamResponse === id) {
            debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: 'response-' + id, type: 'Fetch', request: { method: 'POST', url: provider.url + 'api/v2/chat/completions' } });
            debug.emit('message', {}, 'Network.responseReceived', { requestId: 'response-' + id, type: 'Fetch', response: { status: 200, mimeType: 'text/event-stream', url: provider.url + 'api/v2/chat/completions' } });
          }
          const user = doc.createElement('article');
          user.dataset.role = 'user'; user.dataset.messageAuthorRole = 'user'; user.textContent = value();
          doc.body.append(user); if (id === 'chatgpt') input.replaceChildren(); else input.value = '';
          doc.body.insertAdjacentHTML('beforeend', `<article data-role="assistant" data-message-author-role="assistant"><div class="${markdownClass}"><h2>${id} reply</h2><p>Accepted exactly once.</p><pre><code class="language-python">print(42)\n</code></pre></div></article>`);
          record.completeResponse = (fail = false) => {
            if (fail) {
              debug.emit('message', {}, 'Network.loadingFailed', { requestId: 'response-' + id, errorText: 'net::ERR_CONNECTION_RESET' });
            } else {
              [...doc.querySelectorAll('[data-role="assistant"]')].at(-1).querySelector('p').textContent = 'All server chunks received.';
              debug.emit('message', {}, 'Network.loadingFinished', { requestId: 'response-' + id });
            }
          };
        });
      },
      executeJavaScriptInIsolatedWorld(_world, sources) {
        const code = sources[0].code;
        const action = code.match(/\)\(["'](inspect|prepare|canSubmit|claimSubmit|abort)["']/)?.[1];
        if (action) record.actions.push(action);
        if (['prepare', 'canSubmit', 'claimSubmit'].includes(action)) interactive(`DOM ${action}`);
        return Promise.resolve(record.dom.window.eval(code));
      },
    };
    view.webContents = wc;
    record.view = view; record.wc = wc;
    record.controller = new AbortController(); controllers.set(id, record.controller);
    record.adapter = new WebsiteAdapter(wc, provider, () => {}, (event, fields) => state.events.push({ event, ...fields }), {
      acquireInput: request => layout.acquireInput(request),
    });
    record.prompt = `${id}: preserve this complete prompt\r\nprint(1)`;
    record.run = (timeoutSeconds = 12) => record.adapter.run({
      provider_id: id, job_id: `job-${id}`, request_id: 'combined-request', purpose: 'candidate',
      prompt: record.prompt, timeout_seconds: timeoutSeconds, stable_seconds: 0.01, min_wait_seconds: 0,
    }, record.controller.signal);
    state.models.set(id, record);
    layout.add(id, view, provider.name);
    return record;
  }
  const qwen = model(firstProvider), deepseek = model('deepseek');
  const panes = mode === 'split' ? [
    { provider_id: firstProvider, bounds: { x: 320, y: 90, width: 440, height: 650 } },
    { provider_id: 'deepseek', bounds: { x: 770, y: 90, width: 440, height: 650 } },
  ] : [{ provider_id: 'deepseek', bounds: { x: 320, y: 90, width: 890, height: 650 } }];
  layout.setLayout({ mode, active_provider: 'deepseek', panes, hidden: false });
  deepseek.wc.focus();
  const initialLayout = JSON.parse(JSON.stringify(layout.state));
  const initialBounds = new Map([...state.models].map(([id, record]) => [id, record.view.getBounds()]));
  function assertRestored() {
    assert.deepEqual(state.violations, []);
    assert.equal(state.critical, null);
    assert.equal(layout.lease, null);
    assert.equal(layout.queue.length, 0);
    assert.deepEqual(layout.state, initialLayout, 'User-selected tab, panes, and layout survive the temporary input lease');
    assert.equal(state.focus, deepseek.wc, 'Original native focus is restored');
    assert.equal(deepseek.view.getVisible(), true);
    assert.equal(qwen.view.getVisible(), mode !== 'tabs');
    for (const [id, record] of state.models) {
      assert.deepEqual(record.view.getBounds(), initialBounds.get(id));
      assert.equal(record.wc.debugger.isAttached(), false);
      assert.equal(record.emulated, false);
      assert.equal(record.adapter.active, false);
    }
    assert.equal(state.windows.reduce((sum, window) => sum + window.activationCalls, 0), 0, 'Sending never activates an operating-system window');
  }
  const close = () => { layout.shutdown(); for (const record of state.models.values()) record.dom?.window.close(); };
  return { state, layout, qwen, deepseek, assertRestored, close };
}

for (const mode of ['tabs', 'split', 'windows']) {
  test(`real host and adapter send Qwen plus DeepSeek once in ${mode} and restore the user's layout`, async () => {
    const env = environment(mode);
    try {
      if (mode === 'tabs') assert.equal(env.qwen.view.getVisible(), false, 'Qwen starts in the inactive tab');
      const replies = await Promise.all([env.qwen.run(), env.deepseek.run()]);
      for (const [index, record] of [env.qwen, env.deepseek].entries()) {
        assert.equal(record.loads, 1);
        assert.equal(record.clicks, 1);
        assert.deepEqual(record.sent, [record.prompt.replace(/\r\n/g, '\n')]);
        assert.equal(record.actions.filter(action => action === 'prepare').length, 1);
        assert.equal(record.actions.filter(action => action === 'claimSubmit').length, 1);
        assert.equal(record.commands.filter(command => command.type === 'mousePressed').length, 1);
        assert.equal(record.commands.filter(command => command.type === 'mouseReleased').length, 1);
        assert.match(replies[index], new RegExp(`## ${record.id} reply`));
        assert.match(replies[index], /```python\nprint\(42\)/);
        assert.doesNotMatch(replies[index], /Old reply/);
      }
      assert.deepEqual(env.qwen.inserted, [env.qwen.prompt.replace(/\r\n?/g, '\n')], 'Qwen receives the complete prompt with browser-normalized line endings');
      assert.deepEqual(env.deepseek.inserted, [], 'DeepSeek uses its existing DOM input route');
      const leases = env.state.events.filter(event => ['input_lease.acquired', 'input_lease.released'].includes(event.event));
      assert.deepEqual(leases.map(event => [event.event, event.provider_id]), [
        ['input_lease.acquired', 'qwen'], ['input_lease.released', 'qwen'],
        ['input_lease.acquired', 'deepseek'], ['input_lease.released', 'deepseek'],
      ]);
      assert.ok(env.state.events.findIndex(event => event.event === 'input_lease.acquired' && event.provider_id === 'deepseek') <
        env.state.events.findIndex(event => event.event === 'adapter.complete' && event.provider_id === 'qwen'),
      'The second provider can send while the first is still collecting its reply');
      env.assertRestored();
    } finally { env.close(); }
  });
}

for (const mode of ['tabs', 'split', 'windows']) {
  test(`ChatGPT native ProseMirror edits and DeepSeek send exactly once under real host leases in ${mode}`, async () => {
    const env = environment(mode, { firstProvider: 'chatgpt' });
    const chatgpt = env.qwen;
    chatgpt.prompt = 'Read the full prompt\r\n\r\n  preserve indentation\r\nprint(1)';
    try {
      const results = await Promise.all([chatgpt.run(), env.deepseek.run()]);
      assert.match(results[0], /## chatgpt reply/);
      assert.match(results[1], /## deepseek reply/);
      assert.deepEqual(chatgpt.inserted, [chatgpt.prompt.replace(/\r\n?/g, '\n')]);
      assert.deepEqual(chatgpt.sent, [chatgpt.prompt.replace(/\r\n/g, '\n')]);
      assert.equal(chatgpt.dom.window.document.querySelector('textarea').value, '', 'Hidden legacy editor stays untouched');
      for (const record of [chatgpt, env.deepseek]) {
        assert.equal(record.clicks, 1);
        assert.equal(record.commands.filter(command => command.type === 'mousePressed').length, 1);
        assert.equal(record.commands.filter(command => command.type === 'mouseReleased').length, 1);
        assert.equal(record.commands.some(command => command.method === 'Input.dispatchKeyEvent'), false);
      }
      assert.deepEqual(env.state.events.filter(event => ['input_lease.acquired', 'input_lease.released'].includes(event.event)).map(event => [event.event, event.provider_id]), [
        ['input_lease.acquired', 'chatgpt'], ['input_lease.released', 'chatgpt'],
        ['input_lease.acquired', 'deepseek'], ['input_lease.released', 'deepseek'],
      ]);
      env.assertRestored();
    } finally { env.close(); }
  });
}

test('ChatGPT ignored click is not replayed, and queued DeepSeek still receives its own complete input', async () => {
  const env = environment('tabs', { firstProvider: 'chatgpt', unconfirmed: 'chatgpt' });
  try {
    const [chatgpt, deepseek] = await Promise.allSettled([env.qwen.run(4), env.deepseek.run()]);
    assert.equal(chatgpt.status, 'rejected');
    assert.equal(chatgpt.reason.code, 'submission_unconfirmed');
    assert.equal(deepseek.status, 'fulfilled');
    assert.equal(env.qwen.clicks, 1);
    assert.deepEqual(env.qwen.inserted, [env.qwen.prompt.replace(/\r\n?/g, '\n')]);
    assert.deepEqual(env.deepseek.sent, [env.deepseek.prompt.replace(/\r\n/g, '\n')]);
    env.assertRestored();
  } finally { env.close(); }
});

test('cancel between Qwen mouse press and release frees the real host lease for the queued model', async () => {
  const env = environment('tabs', { cancelOnPress: 'qwen' });
  try {
    const [qwenResult, deepseekResult] = await Promise.allSettled([env.qwen.run(), env.deepseek.run()]);
    assert.equal(qwenResult.status, 'rejected');
    assert.equal(qwenResult.reason.code, 'cancelled');
    assert.equal(env.qwen.clicks, 0);
    assert.equal(env.qwen.commands.filter(command => command.type === 'mousePressed').length, 1);
    assert.equal(env.qwen.commands.filter(command => command.type === 'mouseReleased').length, 0);
    assert.equal(env.qwen.commands.some(command => command.method === 'Input.dispatchKeyEvent'), false);
    assert.equal(deepseekResult.status, 'fulfilled');
    assert.equal(env.deepseek.clicks, 1);
    assert.match(deepseekResult.value, /## deepseek reply/);
    assert.equal(env.qwen.dom.window.__fusionJob.cancelled, true);
    env.assertRestored();
  } finally { env.close(); }
});

test('an unconfirmed Qwen click is not repeated and releases the real host lease before the next send', async () => {
  const env = environment('tabs', { unconfirmed: 'qwen' });
  try {
    const [qwenResult, deepseekResult] = await Promise.allSettled([env.qwen.run(4), env.deepseek.run()]);
    assert.equal(qwenResult.status, 'rejected');
    assert.equal(qwenResult.reason.code, 'submission_unconfirmed');
    assert.equal(env.qwen.clicks, 1);
    assert.deepEqual(env.qwen.sent, [env.qwen.prompt.replace(/\r\n/g, '\n')]);
    assert.equal(env.qwen.dom.window.document.querySelector('textarea').value, env.qwen.prompt.replace(/\r\n/g, '\n'));
    assert.equal(env.qwen.commands.filter(command => command.type === 'mouseReleased').length, 1);
    assert.equal(env.qwen.commands.some(command => command.method === 'Input.dispatchKeyEvent'), false);
    assert.equal(env.state.events.some(event => event.event === 'adapter.submission_accepted' && event.provider_id === 'qwen'), false);
    assert.equal(deepseekResult.status, 'fulfilled');
    assert.equal(env.deepseek.clicks, 1);
    assert.match(deepseekResult.value, /## deepseek reply/);
    env.assertRestored();
  } finally { env.close(); }
});


async function advance(t, iterations) {
  for (let i = 0; i < iterations; i++) {
    await new Promise(resolve => setImmediate(resolve));
    t.mock.timers.tick(500);
  }
}

test('a paused Qwen response stays pending after stable visible content until the network stream finishes', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const env = environment('split', { streamResponse: 'qwen' });
  let settled = false;
  const promise = Promise.all([env.qwen.run(60), env.deepseek.run(60)]);
  promise.then(() => { settled = true; }, () => { settled = true; });
  try {
    await advance(t, 40);
    assert.equal(env.qwen.clicks, 1);
    assert.equal(env.deepseek.clicks, 1);
    assert.equal(settled, false, 'Twenty seconds of unchanged text is not complete while the server stream is open');
    assert.equal(env.qwen.adapter.active, true);
    assert.equal(env.deepseek.adapter.active, false);
    assert.ok(env.state.events.some(row => row.provider_id === 'qwen' && row.event === 'adapter.wait' && row.reason === 'waiting_network_response'));
    env.qwen.completeResponse();
    await advance(t, 10);
    const result = await promise;
    assert.match(result[0], /All server chunks received/);
    const completed = env.state.events.find(row => row.event === 'adapter.complete' && row.provider_id === 'qwen');
    assert.equal(completed.completion_strategy, 'network_and_dom');
    assert.equal(completed.payload.completion_evidence.network.pending, 0);
    env.assertRestored();
  } finally { env.close(); t.mock.timers.reset(); }
});

test('a failed Qwen stream never returns the partial text as a complete response', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const env = environment('split', { streamResponse: 'qwen' });
  const promise = Promise.allSettled([env.qwen.run(60), env.deepseek.run(60)]);
  try {
    await advance(t, 12);
    env.qwen.completeResponse(true);
    await advance(t, 8);
    const [qwen, deepseek] = await promise;
    assert.equal(qwen.status, 'rejected');
    assert.equal(qwen.reason.code, 'response_network_failed');
    assert.equal(qwen.reason.details.responses[0].code, 'net::ERR_CONNECTION_RESET');
    assert.equal(deepseek.status, 'fulfilled');
    assert.equal(env.state.events.some(row => row.event === 'adapter.complete' && row.provider_id === 'qwen'), false);
    assert.equal(env.qwen.clicks, 1);
    env.assertRestored();
  } finally { env.close(); t.mock.timers.reset(); }
});


test('a response stream which never closes reaches the total deadline without returning its visible fragment', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const env = environment('split', { streamResponse: 'qwen' });
  const checked = assert.rejects(env.qwen.run(12), error => error.code === 'timeout');
  try {
    await advance(t, 30);
    await checked;
    assert.equal(env.qwen.clicks, 1);
    assert.equal(env.state.events.some(row => row.event === 'adapter.complete' && row.provider_id === 'qwen'), false);
    assert.equal(env.qwen.adapter.active, false);
    assert.equal(env.layout.lease, null);
    assert.equal(env.qwen.wc.debugger.isAttached(), false);
  } finally { env.close(); t.mock.timers.reset(); }
});
