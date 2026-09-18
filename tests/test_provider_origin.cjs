'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { JSDOM } = require('jsdom');
const { WebsiteAdapter, dispatchTrustedInput } = require('../electron/adapter.cjs');

const selectors = { input: ['textarea'], send: ['#send'], assistant: ['.assistant'], stop: ['#stop'], new_chat: [] };
const job = { job_id: 'origin-job', request_id: 'origin-request', purpose: 'candidate', prompt: 'Private prompt',
  timeout_seconds: 8, submission_timeout_seconds: 1, recovery_timeout_seconds: 0, stable_seconds: 0.01, min_wait_seconds: 0 };

function site() {
  const dom = new JSDOM('<textarea></textarea><button id="send" aria-label="发送">发送</button><main></main>',
    { url: 'https://chat.example.test/start', runScripts: 'outside-only' });
  const w = dom.window;
  w.HTMLElement.prototype.getClientRects = () => [{ width: 80, height: 30 }];
  w.HTMLElement.prototype.getBoundingClientRect = () => ({ left: 10, top: 10, width: 80, height: 30 });
  w.document.elementFromPoint = () => w.document.querySelector('#send');
  Object.defineProperty(w.document, 'visibilityState', { value: 'visible' });
  w.document.hasFocus = () => true;
  const debug = new EventEmitter();
  const commands = [];
  debug.attached = false;
  debug.isAttached = () => debug.attached;
  debug.attach = () => { debug.attached = true; };
  debug.detach = () => { debug.attached = false; };
  debug.sendCommand = async (method, params) => { commands.push({ method, params }); };
  let reportedURL = w.location.href;
  const wc = Object.assign(new EventEmitter(), {
    debugger: debug, isDestroyed: () => false, stop() {},
    getURL: () => reportedURL,
    setURL: value => { reportedURL = value; },
    async loadURL() {},
    executeJavaScriptInIsolatedWorld(_world, sources) {
      try { return Promise.resolve(w.eval(sources[0].code)); } catch (error) { return Promise.reject(error); }
    },
  });
  return { dom, w, wc, commands };
}

test('a configured provider never enters or reads a cross-origin redirected page', async () => {
  const fixture = site();
  fixture.wc.setURL('https://login.attacker.invalid/phish');
  const adapter = new WebsiteAdapter(fixture.wc, { id: 'glm', url: 'https://chat.example.test/', selectors });
  await assert.rejects(adapter.run(job, new AbortController().signal), error => error.code === 'provider_origin_changed');
  assert.equal(fixture.commands.some(row => row.method.startsWith('Input.')), false);
  assert.equal(fixture.w.document.querySelector('textarea').value, '');
  fixture.dom.window.close();
});

test('send diagnostics refuse to inspect a cross-origin login or redirect page', async () => {
  const fixture = site();
  let evaluations = 0;
  fixture.wc.setURL('https://identity.example.invalid/login');
  const execute = fixture.wc.executeJavaScriptInIsolatedWorld;
  fixture.wc.executeJavaScriptInIsolatedWorld = (...args) => { evaluations++; return execute(...args); };
  const adapter = new WebsiteAdapter(fixture.wc, { id: 'kimi', url: 'https://chat.example.test/', selectors });
  await assert.rejects(adapter.diagnoseSend(), error => error.code === 'provider_origin_changed');
  assert.equal(evaluations, 0);
  fixture.dom.window.close();
});

test('a main-frame cross-origin navigation is blocked while a model job is active', async () => {
  const fixture = site();
  // GLM now reuses a loaded same-origin page; exercise the initial navigation.
  fixture.wc.setURL('about:blank');
  let prevented = false;
  fixture.wc.loadURL = async () => fixture.wc.emit('will-redirect', { preventDefault() { prevented = true; } },
    'https://untrusted.example.invalid/login', false, true);
  const adapter = new WebsiteAdapter(fixture.wc, { id: 'glm', url: 'https://chat.example.test/', selectors });
  await assert.rejects(adapter.run(job, new AbortController().signal), error => error.code === 'provider_origin_changed');
  assert.equal(prevented, true);
  assert.equal(fixture.commands.some(row => row.method.startsWith('Input.')), false);
  assert.equal(fixture.wc.listenerCount('will-redirect'), 0);
  fixture.dom.window.close();
});

test('an origin change immediately after DOM preparation blocks native input', async () => {
  const fixture = site();
  let calls = 0;
  const execute = fixture.wc.executeJavaScriptInIsolatedWorld;
  fixture.wc.executeJavaScriptInIsolatedWorld = (...args) => {
    const result = execute(...args);
    if (++calls === 2) fixture.wc.setURL('https://other.example.test/');
    return result;
  };
  const adapter = new WebsiteAdapter(fixture.wc, { id: 'kimi', url: 'https://chat.example.test/', selectors });
  await assert.rejects(adapter.run(job, new AbortController().signal), error => error.code === 'provider_origin_changed');
  assert.equal(fixture.commands.some(row => row.method === 'Input.insertText'), false);
  fixture.dom.window.close();
});

test('trusted input checks the provider origin before every event', async () => {
  const calls = [];
  let checks = 0;
  const wc = { debugger: { sendCommand: async (method, params) => calls.push({ method, params }) } };
  await assert.rejects(dispatchTrustedInput(wc, { method: 'button', x: 1, y: 2 }, Date.now() + 1000,
    new AbortController().signal, () => {}, () => { if (++checks === 2) throw Object.assign(new Error('moved'), { code: 'provider_origin_changed' }); }),
  error => error.code === 'provider_origin_changed');
  assert.deepEqual(calls.map(row => row.params.type), ['mousePressed']);
});

test('same-origin paths and queries remain eligible for provider operations', async () => {
  const fixture = site();
  fixture.wc.setURL('https://chat.example.test/conversation/one?q=2');
  let evaluations = 0;
  const execute = fixture.wc.executeJavaScriptInIsolatedWorld;
  fixture.wc.executeJavaScriptInIsolatedWorld = (...args) => { evaluations++; return execute(...args); };
  const adapter = new WebsiteAdapter(fixture.wc, { id: 'glm', url: 'https://chat.example.test/', selectors });
  const controller = new AbortController();
  const pending = adapter.run(job, controller.signal);
  setTimeout(() => controller.abort(), 20);
  await assert.rejects(pending, error => error.code === 'cancelled');
  assert.ok(evaluations > 0, 'same-origin DOM inspection must be allowed');
  fixture.dom.window.close();
});
