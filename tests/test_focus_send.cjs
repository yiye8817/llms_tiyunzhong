'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { WebsiteAdapter, AdapterError } = require('../electron/adapter.cjs');

// These are local behavioral fixtures, not assertions about Qwen's live markup.
const selectors = { input: ['textarea'], send: ['#old-missing-selector'], assistant: ['[data-role="assistant"]'], stop: [] };
const job = id => ({ job_id: id, prompt: `TASK ${id}`, purpose: 'candidate', timeout_seconds: 10, stable_seconds: 0.01, min_wait_seconds: 0 });

function page(id, shared, { unsupportedFocus = false, ignoredClick = false, acceptanceDelayMs = 0, onInsert } = {}) {
  const commands = [], logs = [], lifecycle = [];
  let dom, focused = false, clicks = 0, visible = false;
  const wc = {
    isDestroyed: () => false, stop() {},
    focus() { assert.fail('CDP focus must not steal OS focus'); },
    debugger: {
      attached: false,
      isAttached() { return this.attached; }, attach() { this.attached = true; }, detach() { this.attached = false; },
      async sendCommand(method, params) {
        commands.push({ method, ...params });
        if (method === 'Emulation.setFocusEmulationEnabled') {
          if (unsupportedFocus) throw new Error('Method not found');
          focused = params.enabled;
        } else if (method === 'Input.insertText') {
          assert.equal(shared.owner, id, 'Native insertion holds the visibility lease');
          assert.ok(visible && focused);
          const input = dom.window.document.activeElement;
          input.value = params.text;
          input.dispatchEvent(new dom.window.InputEvent('input', { bubbles: true }));
          onInsert?.();
        } else if (params.type === 'mouseReleased') {
          assert.equal(shared.owner, id, 'Click belongs to the same visible page as its input');
          assert.ok(visible && focused);
          clicks++;
          if (!ignoredClick) {
            const accept = () => {
              dom.window.document.querySelector('textarea').value = '';
              dom.window.document.body.insertAdjacentHTML('beforeend', `<article data-role="assistant">ANSWER ${id}</article>`);
            };
            if (acceptanceDelayMs) setTimeout(accept, acceptanceDelayMs); else accept();
          }
        }
      },
    },
    async loadURL() {
      dom = new JSDOM('<textarea></textarea><div role="button" aria-label="发送">↑</div>', { url: 'http://localhost/', runScripts: 'outside-only' });
      dom.window.HTMLElement.prototype.getClientRects = () => [{ width: 100, height: 30 }];
      dom.window.HTMLElement.prototype.getBoundingClientRect = () => ({ left: 20, top: 20, width: 100, height: 30 });
      Object.defineProperty(dom.window.document, 'visibilityState', { get: () => visible ? 'visible' : 'hidden' });
      Object.defineProperty(dom.window.document, 'hidden', { get: () => !visible });
      dom.window.document.hasFocus = () => visible && focused;
      dom.window.document.elementFromPoint = () => dom.window.document.querySelector('[role="button"]');
    },
    executeJavaScriptInIsolatedWorld(_id, sources) {
      if (sources[0].code.includes(')("prepare",')) {
        assert.equal(shared.owner, id);
        lifecycle.push('prepared');
      }
      return Promise.resolve(dom.window.eval(sources[0].code));
    },
  };
  let releaseCalls = 0;
  const adapter = new WebsiteAdapter(wc, { id: 'qwen', url: 'http://localhost/', selectors }, () => {}, (event, fields) => {
    logs.push({ event, ...fields }); lifecycle.push(event);
  }, {
    async acquireInput({ signal, deadline }) {
      await shared.acquire(id, signal, deadline);
      visible = true;
      return async () => { releaseCalls++; visible = false; shared.release(id); };
    },
  });
  return { adapter, wc, commands, logs, lifecycle, get clicks() { return clicks; }, get releaseCalls() { return releaseCalls; }, close() { dom?.window.close(); } };
}

function queue() {
  const waiting = [];
  return {
    owner: null,
    async acquire(id) {
      if (this.owner) await new Promise(resolve => waiting.push(resolve));
      assert.equal(this.owner, null); this.owner = id;
    },
    release(id) { assert.equal(this.owner, id); this.owner = null; waiting.shift()?.(); },
  };
}

test('parallel Qwen jobs acquire visible input serially, send once and release before waiting for completion', async () => {
  const shared = queue(), a = page('a', shared), b = page('b', shared);
  try {
    assert.deepEqual(await Promise.all([a.adapter.run(job('a')), b.adapter.run(job('b'))]), ['ANSWER a', 'ANSWER b']);
    for (const current of [a, b]) {
      assert.equal(current.clicks, 1);
      assert.equal(current.releaseCalls, 1);
      assert.deepEqual(current.commands.map(item => item.method), ['Emulation.setFocusEmulationEnabled', 'Input.insertText', 'Input.dispatchMouseEvent', 'Input.dispatchMouseEvent', 'Emulation.setFocusEmulationEnabled']);
      assert.deepEqual(current.commands.filter(item => item.method.startsWith('Emulation.')).map(item => item.enabled), [true, false]);
      assert.ok(current.lifecycle.indexOf('adapter.input_lease_released') < current.lifecycle.indexOf('adapter.submission_accepted'));
      assert.ok(current.lifecycle.indexOf('adapter.input_lease_released') < current.lifecycle.indexOf('adapter.complete'));
      assert.equal(current.wc.debugger.isAttached(), false);
    }
    assert.equal(shared.owner, null);
  } finally { a.close(); b.close(); }
});

test('missing focus acknowledgement plus unfocused page fails before input and releases the view', async () => {
  const shared = queue(), current = page('unsupported', shared, { unsupportedFocus: true });
  try {
    await assert.rejects(current.adapter.run(job('unsupported')), error => error.code === 'page_not_interactive' && error.details.page.hasFocus === false);
    assert.equal(current.commands.some(item => item.method.startsWith('Input.')), false);
    assert.equal(current.releaseCalls, 1);
    assert.equal(shared.owner, null);
    assert.equal(current.wc.debugger.isAttached(), false);
  } finally { current.close(); }
});

test('cancellation after native insertion does not click and cleans up focus and the input lease', async () => {
  const controller = new AbortController(), shared = queue();
  const current = page('cancel', shared, { onInsert: () => controller.abort('input_view_hidden') });
  try {
    await assert.rejects(current.adapter.run(job('cancel'), controller.signal), error => error.code === 'cancelled');
    assert.equal(current.clicks, 0);
    assert.equal(current.commands.filter(item => item.method === 'Input.insertText').length, 1);
    assert.equal(current.commands.at(-1).method, 'Emulation.setFocusEmulationEnabled');
    assert.equal(current.commands.at(-1).enabled, false);
    assert.equal(current.releaseCalls, 1);
    assert.equal(shared.owner, null);
  } finally { current.close(); }
});

test('cancellation while acquiring input never prepares, attaches or loses a late lease handle', async () => {
  const controller = new AbortController(), shared = queue(), current = page('queued', shared);
  current.adapter.options.acquireInput = async ({ signal }) => {
    controller.abort();
    if (signal.aborted) throw new AdapterError('cancelled', 'Cancelled while queued');
    assert.fail('No handle may be returned after cancelled queue acquisition');
  };
  try {
    await assert.rejects(current.adapter.run(job('queued'), controller.signal), error => error.code === 'cancelled');
    assert.deepEqual(current.commands, []);
    assert.equal(current.lifecycle.includes('prepared'), false);
    assert.equal(current.adapter.active, false);
  } finally { current.close(); }
});

test('host queue errors preserve the actionable reason and always identify the actual adapter provider', async () => {
  const current = page('hidden-window', queue());
  current.adapter.options.acquireInput = async ({ job: acquired }) => {
    assert.equal(acquired.provider_id, 'qwen');
    throw Object.assign(new Error('等待网页输入区域超时；请恢复模型窗口。'), { code: 'input_lease_timeout' });
  };
  try {
    await assert.rejects(current.adapter.run({ ...job('hidden-window'), provider_id: 'mismatched-caller' }), error => {
      assert.equal(error.code, 'input_lease_timeout');
      assert.match(error.message, /请恢复模型窗口/);
      return true;
    });
    assert.deepEqual(current.commands, []);
    assert.equal(current.adapter.active, false);
  } finally { current.close(); }
});

test('unconfirmed click carries focus and target evidence and is never retried', async () => {
  const shared = queue(), current = page('ignored', shared, { ignoredClick: true });
  try {
    await assert.rejects(current.adapter.run({ ...job('ignored'), timeout_seconds: 2.4 }), error => {
      assert.equal(error.code, 'submission_unconfirmed');
      assert.match(error.message, /visibility=hidden, focus=false/);
      assert.equal(error.details.send_page.visibilityState, 'visible');
      assert.equal(error.details.send_page.hasFocus, true);
      assert.equal(error.details.send_target.selector, 'accessible-send-name');
      assert.equal(error.details.send_target.obscured, false);
      assert.equal(error.details.dispatched, true);
      assert.equal(error.details.accepted, false);
      return true;
    });
    assert.equal(current.clicks, 1);
    assert.equal(current.releaseCalls, 1);
    assert.equal(shared.owner, null);
  } finally { current.close(); }
});

test('adapter exposes a standalone read-only report without acquiring input or attaching a debugger', async () => {
  const current = page('diagnosis', queue());
  try {
    await current.wc.loadURL();
    const report = await current.adapter.diagnoseSend();
    assert.equal(report.page.visibilityState, 'hidden');
    assert.equal(report.page.hasFocus, false);
    assert.equal(report.sendTarget.label, '发送');
    assert.equal(report.sendTarget.selector, 'accessible-send-name');
    assert.deepEqual(current.commands, []);
    assert.equal(current.releaseCalls, 0);
    assert.ok(current.logs.some(item => item.event === 'adapter.send_diagnostic'));
  } finally { current.close(); }
});


test('server acceptance after 20 seconds does not retain the input lease or send a second click', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const shared = queue(), slow = page('slow', shared, { acceptanceDelayMs: 20000 }), fast = page('fast', shared);
  let finished = false;
  const result = Promise.all([slow.adapter.run({ ...job('slow'), timeout_seconds: 60 }), fast.adapter.run({ ...job('fast'), timeout_seconds: 60 })]);
  result.then(() => { finished = true; }, () => { finished = true; });
  try {
    for (let tick = 0; tick < 100 && !finished; tick++) {
      await new Promise(resolve => setImmediate(resolve));
      t.mock.timers.tick(500);
    }
    assert.equal(finished, true, 'Bounded virtual deadline');
    assert.deepEqual(await result, ['ANSWER slow', 'ANSWER fast']);
    assert.equal(slow.clicks, 1);
    assert.equal(fast.clicks, 1);
    const accepted = slow.logs.find(row => row.event === 'adapter.submission_accepted');
    const fastComplete = fast.logs.find(row => row.event === 'adapter.complete');
    assert.ok(accepted.elapsed_ms > 15000);
    assert.ok(fastComplete.elapsed_ms < accepted.elapsed_ms, 'A second model finishes while the server acceptance is still pending');
    assert.ok(slow.logs.some(row => row.event === 'adapter.submission_waiting'));
    assert.equal(shared.owner, null);
  } finally { slow.close(); fast.close(); t.mock.timers.reset(); }
});


test('an explicit 15 second acceptance limit fails once while preserving the global deadline', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const shared = queue(), current = page('bounded-slow', shared, { acceptanceDelayMs: 20000 });
  const promise = current.adapter.run({ ...job('bounded-slow'), timeout_seconds: 60, submission_timeout_seconds: 15 });
  const checked = assert.rejects(promise, error => error.code === 'submission_unconfirmed');
  let finished = false;
  checked.then(() => { finished = true; }, () => { finished = true; });
  try {
    for (let tick = 0; tick < 40 && !finished; tick++) {
      await new Promise(resolve => setImmediate(resolve));
      t.mock.timers.tick(500);
    }
    assert.equal(finished, true);
    await checked;
    assert.equal(current.clicks, 1);
    assert.equal(shared.owner, null);
    assert.ok(current.logs.find(row => row.event === 'adapter.failed').elapsed_ms < 20000);
  } finally { current.close(); t.mock.timers.reset(); }
});
