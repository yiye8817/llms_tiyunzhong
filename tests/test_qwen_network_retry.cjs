'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { EventEmitter } = require('node:events');

// Production adapter + network tracking + deadlines, with a controlled DOM/CDP
// boundary and plain-text conversion. No npm/Electron/live Qwen dependency.
// Real DOM selectors/hit-testing are covered in browser_qwen1176_smoke.py.
const adapterFile = path.join(process.env.FUSION_TEST_SOURCE_ROOT || path.join(__dirname, '..'), 'electron/adapter.cjs');
function loadAdapter() {
  const module = { exports: {} }, localRequire = createRequire(adapterFile);
  class PlainText { use() {} remove() {} addRule() {} turndown(html) { return html.replace(/<[^>]*>/g, ''); } }
  const requireStub = id => id === 'turndown' ? PlainText : id === 'turndown-plugin-gfm' ? { gfm() {} } : localRequire(id);
  vm.runInNewContext(fs.readFileSync(adapterFile, 'utf8'), { module, exports: module.exports, require: requireStub,
    URL, Date, setTimeout, clearTimeout, console }, { filename: adapterFile });
  return module.exports.WebsiteAdapter;
}
const selectors = { input: ['textarea'], send: ['#send'], assistant: ['.assistant'], stop: [], new_chat: [] };
const networkText = 'Oops! There was an issue connecting to Qwen3.8-Max.网络错误';
function fixture(options = {}) {
  const state = { draft: '', accepted: false, error: false, button: false, stopping: false, answer: '', claimed: false,
    contextChanged: false, model: 'qwen3.8-max' };
  let url = 'https://chat.qwen.ai/', attached = false, requestNumber = 0, loads = 0, leases = 0, releases = 0;
  const logs = [], statuses = [], progress = [], gestures = [], commands = [], actions = [];
  const debug = new EventEmitter(), wc = new EventEmitter();
  const page = { visibilityState: 'visible', hasFocus: true };
  const answers = () => state.answer ? [{ text: state.answer, html: `<p>${state.answer}</p>` }] : [];
  const recovery = () => ({ contextValid: state.accepted && !state.contextChanged, contextChanged: state.contextChanged,
    currentTurnAccepted: state.accepted, currentError: state.error, errorKind: state.error ? 'qwen_network' : null,
    retryAvailable: state.error && state.button && !state.claimed && !state.stopping, recoveryUsed: state.claimed,
    reason: state.contextChanged ? 'recovery_user_changed' : !state.accepted ? 'recovery_waiting_acceptance' : state.error ?
      state.claimed ? 'recovery_already_claimed' : state.button ? 'recovery_ready' : 'recovery_target_missing' : 'recovery_no_current_error',
    userCount: state.accepted ? 1 : 0, baselineUserCount: 0, lastUserMatchesPrompt: state.accepted,
    stopping: state.stopping });
  const inspect = () => ({ page, summary: {}, inputReady: true, inputEmpty: !state.draft, answers: answers(), stopping: state.stopping,
    userCount: state.accepted ? 1 : 0, lastUserMatchesPrompt: state.accepted });
  const session = () => ({ model: { key: state.model, label: state.model }, answers: answers().length,
    userCount: state.accepted ? 1 : 0, inputEmpty: !state.draft, inputPresent: true, inputReady: true, stopping: state.stopping,
    conversation: url, rating: [] });
  const dom = async (action, args) => {
    actions.push(action);
    if (action === 'abort') return true;
    if (action === 'inspect') return inspect();
    if (action === 'qwenSession') return session();
    if (action === 'prepare') return { page, ready: !state.draft, inputFocused: true, inputMethod: 'cdp_insert_text' };
    if (action === 'canSubmit' || action === 'claimSubmit') return { page, ready: state.draft === args.prompt,
      inputLength: state.draft.length, expectedLength: args.prompt.length, method: 'button', x: 100, y: 100 };
    if (action === 'recoveryInspect') return recovery();
    if (action === 'recoveryTarget') return { ...recovery(), ready: recovery().retryAvailable, nonce: 'retry-once', method: 'button',
      target: { id: 'retry', selector: 'qwen-current-turn-network-adjacent-icon' }, x: 200, y: 100 };
    if (action === 'recoveryClaim') {
      await options.beforeClaim?.(env);
      const ready = recovery().retryAvailable && args.nonce === 'retry-once';
      if (ready) state.claimed = true;
      return { ...recovery(), ready, method: 'button', x: 200, y: 100 };
    }
    throw new Error(`Unexpected DOM action ${action}`);
  };
  const network = (status = 200, finished = true) => {
    const requestId = `net-${++requestNumber}`, endpoint = 'https://chat.qwen.ai/api/v2/chat/completions';
    debug.emit('message', {}, 'Network.requestWillBeSent', { requestId, type: 'Fetch', request: { method: 'POST', url: endpoint } });
    debug.emit('message', {}, 'Network.responseReceived', { requestId, type: 'Fetch', response: { status, url: endpoint, mimeType: 'text/event-stream' } });
    if (finished) finish(requestId);
    return requestId;
  };
  const finish = requestId => debug.emit('message', {}, 'Network.loadingFinished', { requestId });
  const accept = () => { state.accepted = true; state.draft = ''; url = 'https://chat.qwen.ai/c/same-job'; };
  const error = (button = true) => { accept(); state.error = true; state.button = button; state.answer = networkText; state.stopping = false; };
  const done = (text = 'Recovered answer') => { accept(); state.error = false; state.button = false; state.stopping = false; state.answer = text; };
  Object.assign(debug, {
    isAttached: () => attached, attach: () => { attached = true; }, detach: () => { attached = false; },
    async sendCommand(method, params = {}) {
      commands.push({ method, params });
      if (method === 'Input.insertText') state.draft = params.text;
      if (method === 'Input.dispatchMouseEvent' && params.type === 'mouseReleased') {
        if (params.x === 100) { gestures.push('send'); await options.send?.(env); }
        else if (params.x === 200) { gestures.push('retry'); await options.retry?.(env); if (options.ambiguous) throw new Error('Lost CDP click acknowledgement'); }
        else throw new Error('Unexpected click');
      }
    },
  });
  Object.assign(wc, { debugger: debug, isDestroyed: () => false, getURL: () => url, async loadURL() { loads++; }, stop() {},
    async executeJavaScriptInIsolatedWorld(_world, sources) {
      const code = sources[0].code;
      if (code.includes("})('abort',")) return true;
      const tail = code.match(/\}\)\(("(?:[^"\\]|\\.)*"),(\{[^\n]*\})\); \}\)\(\)$/);
      assert.ok(tail, 'serialized production pageAction and JSON arguments');
      return dom(JSON.parse(tail[1]), JSON.parse(tail[2]));
    },
  });
  const adapterOptions = options.leases ? { async acquireInput(context) {
    leases++; await options.acquire?.(env, leases, context);
    return async () => { releases++; };
  } } : {};
  const Adapter = loadAdapter();
  const adapter = new Adapter(wc, { id: 'qwen', url: 'https://chat.qwen.ai/', selectors }, (stateName, message) => {
    statuses.push({ state: stateName, message }); options.status?.(stateName, env);
  }, (event, fields) => { logs.push({ event, ...fields }); }, adapterOptions);
  const env = { state, wc, debug, adapter, logs, statuses, progress, gestures, commands, actions, network, finish, accept, error, done,
    get loads() { return loads; }, get leases() { return leases; }, get releases() { return releases; } };
  return env;
}
// These are legacy single-click-policy regression cases; staged recovery is covered in 1179 tests.
const job = extra => ({ qwen_retry_stages: false, job_id: 'same-job', request_id: 'same-request', purpose: 'candidate', prompt: 'Current task',
  timeout_seconds: 6, submission_timeout_seconds: 0.7, recovery_timeout_seconds: 6, stable_seconds: 0.05, min_wait_seconds: 0, ...extra });
async function settle(t, promise, limit = 18000) {
  let result;
  promise.then(value => { result = { value }; }, error => { result = { error }; });
  for (let elapsed = 0; !result && elapsed < limit; elapsed += 100) {
    await new Promise(resolve => setImmediate(resolve)); t.mock.timers.tick(100);
  }
  assert.ok(result, 'adapter must stop at its bounded deadline');
  if (result.error) throw result.error;
  return result.value;
}
async function withSite(t, options, callback) {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const env = fixture(options);
  try { await callback(env); }
  finally { t.mock.timers.reset(); }
}
const run = (s, extra = {}, signal) => s.adapter.run(job(extra), signal, state => s.progress.push(state));
const once = s => {
  assert.deepEqual(s.gestures, ['send', 'retry']);
  assert.equal(s.commands.filter(c => c.method === 'Input.insertText').length, 1);
  assert.equal(s.loads, 0);
  assert.equal(s.logs.filter(c => c.event === 'adapter.recovery_retry').length, 1);
  assert.equal(s.debug.listenerCount('message'), 0);
};

test('Qwen application network error under HTTP 200 clicks retry once and returns only the complete answer', async t => withSite(t, {
  send(s) { s.error(); s.network(200); }, retry(s) { s.network(200); s.done(); },
}, async s => {
  assert.equal(await settle(t, run(s)), 'Recovered answer'); once(s);
  const row = s.logs.find(row => row.event === 'adapter.recovery_retry');
  assert.equal(row.error_kind, 'qwen_network'); assert.equal(row.attempt, 1); assert.equal(row.max_attempts, 1);
  assert.equal(s.progress.filter(row => row.stage === 'retrying').length, 1);
  assert.ok(s.progress.some(row => row.stage === 'recovering'));
  assert.ok(s.statuses.some(row => /Qwen 网络错误/.test(row.message)));
}));

test('Qwen 503 followed by a delayed network-error card retries in the same job', async t => withSite(t, {
  send(s) { s.accept(); s.network(503); setTimeout(() => s.error(), 1100); },
  retry(s) { s.network(200); s.done(); },
}, async s => { assert.equal(await settle(t, run(s)), 'Recovered answer'); once(s); }));

test('Qwen delayed Retry control is rechecked rather than permanently missed on recovery entry', async t => withSite(t, {
  send(s) { s.error(false); s.network(200); setTimeout(() => { s.state.button = true; }, 1300); },
  retry(s) { s.network(200); s.done(); },
}, async s => { assert.equal(await settle(t, run(s)), 'Recovered answer'); once(s); }));

test('Qwen keeps waiting while the original response is pending then retries after it closes', async t => withSite(t, {
  send(s) { s.error(); const id = s.network(200, false); setTimeout(() => s.finish(id), 1400); },
  retry(s) { s.network(200); s.done(); },
}, async s => { assert.equal(await settle(t, run(s)), 'Recovered answer'); once(s); }));

test('one failed automatic retry never loops or returns the error card', async t => withSite(t, {
  send(s) { s.error(); s.network(503); }, retry(s) { s.network(503); s.error(); },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 3 })), e => e.code === 'recovery_timeout');
  once(s); assert.ok(!s.logs.some(row => row.event === 'adapter.complete'));
}));

test('lost click acknowledgement is not retried and can be recovered manually', async t => withSite(t, {
  send(s) { s.error(); s.network(200); }, retry() {}, ambiguous: true,
  status(name, s) { if (name === 'manual_retry_required') setTimeout(() => { s.network(200); s.done('Manual recovery'); }, 300); },
}, async s => { assert.equal(await settle(t, run(s)), 'Manual recovery'); once(s); }));

test('manual generation before the delayed button appears suppresses automation even while stale error remains', async t => withSite(t, {
  send(s) { s.error(false); s.network(503); },
  status(name, s) { if (name === 'manual_retry_required') setTimeout(() => {
    const id = s.network(200, false); s.state.button = true;
    setTimeout(() => { s.done('Manual answer'); s.finish(id); }, 800);
  }, 500); },
}, async s => {
  assert.equal(await settle(t, run(s)), 'Manual answer'); assert.deepEqual(s.gestures, ['send']);
  assert.ok(s.logs.some(row => row.event === 'adapter.recovery_auto_skipped'));
}));

test('a failed manual attempt cannot re-enable automatic retry by resetting the failure checkpoint', async t => withSite(t, {
  send(s) { s.error(false); s.network(503); },
  status(name, s) { if (name === 'manual_retry_required') setTimeout(() => { s.network(503); s.state.button = true; }, 700); },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 3 })), e => e.code === 'recovery_timeout');
  assert.deepEqual(s.gestures, ['send']);
}));

test('a manual request starting while the retry input lease is acquired prevents an extra click', async t => withSite(t, {
  leases: true,
  send(s) { s.error(); s.network(503); },
  acquire(s, count) { if (count === 2) { const id = s.network(200, false); setTimeout(() => { s.done('Lease-race answer'); s.finish(id); }, 500); } },
}, async s => {
  assert.equal(await settle(t, run(s)), 'Lease-race answer'); assert.deepEqual(s.gestures, ['send']);
  assert.equal(s.leases, s.releases);
}));

test('a manual request starting during reservation prevents a claimed click', async t => withSite(t, {
  send(s) { s.error(); s.network(503); },
  beforeClaim(s) { const id = s.network(200, false); setTimeout(() => { s.done('Claim-race answer'); s.finish(id); }, 500); },
}, async s => {
  assert.equal(await settle(t, run(s)), 'Claim-race answer'); assert.deepEqual(s.gestures, ['send']);
  assert.ok(s.logs.some(row => row.reason === 'generation_started_during_retry_claim'));
}));

test('changed draft or user turn during retry lease ends recovery rather than clicking', async t => withSite(t, {
  leases: true, send(s) { s.error(); s.network(503); }, acquire(s, count) { if (count === 2) s.state.contextChanged = true; },
}, async s => {
  await assert.rejects(settle(t, run(s)), e => e.code === 'recovery_context_changed');
  assert.deepEqual(s.gestures, ['send']); assert.equal(s.leases, s.releases);
}));

test('changed selected Qwen model is not silently retried', async t => withSite(t, {
  send(s) { s.error(); s.network(200); s.state.model = 'different-model'; },
}, async s => {
  await assert.rejects(settle(t, run(s)), e => e.code === 'qwen_model_changed'); assert.deepEqual(s.gestures, ['send']);
}));

test('cancelling while waiting for the button stops polling and releases observers', async t => {
  const controller = new AbortController();
  await withSite(t, {
    send(s) { s.error(false); s.network(503); setTimeout(() => controller.abort(), 900); },
  }, async s => {
    await assert.rejects(settle(t, run(s, {}, controller.signal)), e => e.code === 'cancelled');
    assert.deepEqual(s.gestures, ['send']); assert.equal(s.adapter.active, false);
    assert.equal(s.debug.listenerCount('message'), 0); assert.equal(s.debug.isAttached(), false);
  });
});

test('candidate total timeout is not extended by Qwen retry polling', async t => withSite(t, {
  send(s) { s.error(false); s.network(503); setTimeout(() => { s.state.button = true; }, 8000); },
}, async s => {
  const started = Date.now();
  await assert.rejects(settle(t, run(s, { total_timeout_seconds: 4, recovery_timeout_seconds: 180 })), e => ['recovery_timeout', 'timeout'].includes(e.code));
  assert.ok(Date.now() - started <= 4400); assert.deepEqual(s.gestures, ['send']);
}));

test('zero recovery budget never clicks or returns Qwen network-error text as an answer', async t => withSite(t, {
  send(s) { s.error(); s.network(200); },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 0 })), e => e.code === 'response_page_error');
  assert.deepEqual(s.gestures, ['send']); assert.ok(!s.logs.some(row => row.event === 'adapter.complete'));
}));

test('a normal answer does not trigger recovery or refill the prompt', async t => withSite(t, {
  send(s) { s.network(200); s.done('Normal answer'); },
}, async s => {
  assert.equal(await settle(t, run(s)), 'Normal answer'); assert.deepEqual(s.gestures, ['send']);
  assert.ok(!s.logs.some(row => row.event.startsWith('adapter.recovery_')));
}));

test('missing retry target gives bounded manual recovery instead of guessed refresh or Enter', async t => withSite(t, {
  send(s) { s.error(false); s.network(200); },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 2 })), e => e.code === 'recovery_timeout');
  assert.deepEqual(s.gestures, ['send']); assert.equal(s.loads, 0);
  assert.ok(!s.commands.some(row => row.method === 'Input.dispatchKeyEvent'));
}));

test('a changed conversation before delayed target stops collection', async t => withSite(t, {
  send(s) { s.error(false); s.network(503); setTimeout(() => { s.state.contextChanged = true; s.state.button = true; }, 700); },
}, async s => {
  await assert.rejects(settle(t, run(s)), e => e.code === 'recovery_context_changed');
  assert.deepEqual(s.gestures, ['send']);
}));


// 1.17.8: a retry click needs the same bounded commit window as first send.
test('retry keeps its input lease for a deferred request, not just the old observed request', async t => withSite(t, {
  leases: true, send(s) { s.error(); s.network(503); },
  retry(s) { setTimeout(() => { if (s.leases > s.releases) { s.network(200); s.done('Deferred retry'); } }, 700); },
}, async s => {
  assert.equal(await settle(t, run(s)), 'Deferred retry'); once(s); assert.equal(s.leases, s.releases);
  assert.equal(s.logs.find(row => row.event === 'adapter.submit_settle_finished' && row.operation === 'retry_current_turn')?.evidence, 'generation_request_started');
}));

test('retry commit window is bounded even when the click does not produce a request', async t => withSite(t, {
  leases: true, send(s) { s.error(); s.network(503); }, retry() {},
}, async s => {
  await assert.rejects(settle(t, run(s, { submit_settle_seconds: .4, recovery_timeout_seconds: 3 })), e => e.code === 'recovery_timeout');
  once(s); assert.equal(s.leases, s.releases);
  const row = s.logs.find(row => row.event === 'adapter.submit_settle_finished' && row.operation === 'retry_current_turn');
  assert.equal(row?.evidence, 'bounded_settle_elapsed');
}));

test('cancelling during deferred retry commit releases input and never submits a third time', async t => {
  const controller = new AbortController();
  await withSite(t, {
    leases: true, send(s) { s.error(); s.network(503); },
    retry() { setTimeout(() => controller.abort(), 200); },
  }, async s => {
    await assert.rejects(settle(t, run(s, {}, controller.signal)), e => e.code === 'cancelled');
    once(s); assert.equal(s.leases, s.releases); assert.equal(s.adapter.active, false);
  });
});

test('new retry HTTP failure is never cleared merely because the commit window observed it', async t => withSite(t, {
  leases: true, send(s) { s.error(); s.network(503); },
  retry(s) { setTimeout(() => { s.network(429); s.error(); }, 400); },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 3 })), e => e.code === 'recovery_timeout');
  once(s); assert.equal(s.leases, s.releases); assert.ok(!s.logs.some(row => row.event === 'adapter.complete'));
}));
