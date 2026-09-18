'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { JSDOM } = require('jsdom');
const { WebsiteAdapter, startNetworkTrace } = require('../electron/adapter.cjs');
const { webProgress } = require('../electron/web-progress.cjs');

const selectors = { input: ['textarea'], send: ['#send'], assistant: ['[data-role="assistant"]'], stop: ['#stop'], new_chat: [] };
const errorHTML = '<div role="alert">服务器繁忙，请稍后再试<button id="retry" data-x="400" data-y="100">重试</button></div>';
const qwenBusyHTML = '<div role="alert" data-x="90" data-y="100">Oops! There was an issue connecting to Qwen3.8-Max. 目前服务访问量较大，请稍后再试。</div><button id="retry" aria-label="重试" data-x="90" data-y="150"><svg><path d="M4 12a8 8 0 1 0 2-5"/></svg></button>';
function site(options = {}) {
  const dom = new JSDOM('<main id="messages"></main><textarea data-x="10" data-y="300"></textarea><button id="send" aria-label="发送" data-x="300" data-y="300">发送</button>', { url: 'https://chat.qwen.ai/', runScripts: 'outside-only' });
  const w = dom.window, doc = w.document;
  w.Date.now = () => Date.now();
  w.HTMLElement.prototype.getClientRects = function () { return [{ width: 80, height: 30 }]; };
  w.HTMLElement.prototype.getBoundingClientRect = function () { return { left: Number(this.dataset.x || 10), top: Number(this.dataset.y || 10), width: 80, height: 30 }; };
  doc.elementFromPoint = (x, y) => [...doc.querySelectorAll('button,textarea')].find(node => {
    const rect = node.getBoundingClientRect();
    return !node.closest('[hidden]') && x >= rect.left && x <= rect.left + 80 && y >= rect.top && y <= rect.top + 30;
  }) || doc.body;
  Object.defineProperty(doc, 'visibilityState', { value: 'visible', configurable: true }); doc.hasFocus = () => true;
  const debug = new EventEmitter(), logs = [], statuses = [], gestures = [], progress = [], commands = [];
  let loads = 0, requestNumber = 0, stopCalls = 0;
  const network = (status = 200, finish = true) => {
    const requestId = `response-${++requestNumber}`, url = 'https://chat.qwen.ai/api/v2/chat/completions';
    debug.emit('message', {}, 'Network.requestWillBeSent', { requestId, type: 'Fetch', request: { method: 'POST', url } });
    debug.emit('message', {}, 'Network.responseReceived', { requestId, type: 'Fetch', response: { url, status, mimeType: 'text/event-stream' } });
    if (finish) debug.emit('message', {}, 'Network.loadingFinished', { requestId });
    return requestId;
  };
  const finish = requestId => debug.emit('message', {}, 'Network.loadingFinished', { requestId });
  const accept = (html = errorHTML) => {
    if (!doc.querySelector('[data-role="user"]')) {
      const user = doc.createElement('article'); user.setAttribute('data-role', 'user'); user.textContent = doc.querySelector('textarea').value;
      doc.querySelector('#messages').append(user); doc.querySelector('textarea').value = '';
      w.history.pushState({}, '', '/c/this-task');
    }
    let answer = doc.querySelector('[data-role="assistant"]');
    if (!answer) { answer = doc.createElement('article'); answer.setAttribute('data-role', 'assistant'); doc.querySelector('#messages').append(answer); }
    answer.innerHTML = html;
    const retry = doc.querySelector('#retry'); if (retry) retry.onclick = () => options.retry?.(env);
  };
  doc.querySelector('#send').onclick = () => options.send?.(env);
  debug.attached = false; debug.isAttached = () => debug.attached; debug.attach = () => { debug.attached = true; }; debug.detach = () => { debug.attached = false; };
  debug.sendCommand = async (method, params) => {
    commands.push({ method, params });
    if (method === 'Input.insertText') doc.querySelector('textarea').value = params.text;
    if (method === 'Input.dispatchMouseEvent' && params.type === 'mouseReleased') {
      const target = doc.elementFromPoint(params.x, params.y); gestures.push(target.id); target.click();
      if (target.id === 'retry' && options.ambiguousRetry) throw new Error('CDP acknowledgement lost');
    }
  };
  const wc = { debugger: debug, isDestroyed: () => false, getURL: () => w.location.href,
    async loadURL() { loads++; }, stop() { stopCalls++; },
    executeJavaScriptInIsolatedWorld(_world, sources) { try { return Promise.resolve(w.eval(sources[0].code)); } catch (error) { return Promise.reject(error); } },
  };
  const adapter = new WebsiteAdapter(wc, { id: 'qwen', url: 'https://chat.qwen.ai/', selectors }, (state, message) => {
    statuses.push({ state, message }); options.status?.(state, env);
  }, (event, fields) => logs.push({ event, ...fields }), options.adapterOptions || {});
  const env = { dom, doc, w, debug, wc, adapter, logs, statuses, gestures, progress, commands, network, finish, accept,
    get loads() { return loads; }, get stopCalls() { return stopCalls; }, close() { w.close(); } };
  return env;
}
// Legacy recovery-policy tests; 1179 default tiered policy has separate unit/Chromium coverage.
const job = extra => ({ qwen_retry_stages: false, job_id: 'same-original-job', request_id: 'same-http-request', purpose: 'candidate', prompt: 'Inspect current task',
  timeout_seconds: 6, submission_timeout_seconds: 0.7, recovery_timeout_seconds: 8, stable_seconds: 0.05, min_wait_seconds: 0, ...extra });
async function settle(t, promise, limit = 18000) {
  let settled; promise.then(value => { settled = { value }; }, error => { settled = { error }; });
  for (let elapsed = 0; !settled && elapsed < limit; elapsed += 100) { await new Promise(resolve => setImmediate(resolve)); t.mock.timers.tick(100); }
  assert.ok(settled, 'web recovery must finish within a bounded deadline');
  if (settled.error) throw settled.error;
  return settled.value;
}
async function withSite(t, options, callback) {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const env = site(options);
  try { await callback(env); } finally { env.close(); t.mock.timers.reset(); }
}
function run(env, extra, signal) { return env.adapter.run(job(extra), signal, stage => env.progress.push(stage)); }

test('one trusted current-turn retry recovers the original job without navigation or prompt refill', async t => withSite(t, {
  send(s) { s.accept(); s.network(503); }, retry(s) { s.network(200); s.accept('<h2>Recovered complete answer</h2>'); },
}, async s => {
  assert.equal(await settle(t, run(s)), '## Recovered complete answer');
  assert.deepEqual(s.gestures, ['send', 'retry']); assert.equal(s.loads, 0);
  assert.equal(s.commands.filter(row => row.method === 'Input.insertText').length, 1);
  assert.ok(s.progress.some(row => row.stage === 'retrying')); assert.ok(s.progress.some(row => row.stage === 'recovering'));
  assert.equal(s.logs.filter(row => row.event === 'adapter.complete').length, 1);
  assert.ok(s.logs.every(row => row.job_id === 'same-original-job')); assert.equal(s.debug.listenerCount('message'), 0);
}));

test('Qwen3.8-Max busy card clicks its adjacent circular-arrow retry once and collects the recovered answer', async t => withSite(t, {
  send(s) { s.accept(qwenBusyHTML); s.network(200); },
  retry(s) { s.network(200); s.accept('<h2>Qwen busy recovery completed</h2>'); },
}, async s => {
  assert.equal(await settle(t, run(s)), '## Qwen busy recovery completed');
  assert.deepEqual(s.gestures, ['send', 'retry']);
  assert.equal(s.commands.filter(row => row.method === 'Input.insertText').length, 1);
  assert.equal(s.progress.filter(row => row.stage === 'retrying').length, 1);
  assert.equal(s.logs.filter(row => row.event === 'adapter.recovery_retry').length, 1);
  assert.equal(s.logs.filter(row => row.event === 'adapter.complete').length, 1);
}));

test('ambiguous retry acknowledgement is never clicked twice and manual retry can recover', async t => withSite(t, {
  send(s) { s.accept(); s.network(503); }, retry() {}, ambiguousRetry: true,
  status(state, s) { if (state === 'manual_retry_required') setTimeout(() => { s.network(200); s.accept('Manual retry finished'); }, 300); },
}, async s => {
  assert.equal(await settle(t, run(s)), 'Manual retry finished');
  assert.deepEqual(s.gestures, ['send', 'retry']);
  assert.equal(s.progress.filter(row => row.stage === 'manual_retry_required').length, 1);
}));

test('HTTP 200 with the full unaccepted composer asks for manual send and does not auto-resubmit', async t => withSite(t, {
  send(s) { s.network(200); },
  status(state, s) { if (state === 'manual_retry_required') setTimeout(() => { s.accept('Accepted by manual send'); s.network(200); }, 300); },
}, async s => {
  assert.equal(await settle(t, run(s)), 'Accepted by manual send'); assert.deepEqual(s.gestures, ['send']);
  const stages = s.progress.map(row => row.stage);
  assert.ok(stages.indexOf('server_responded') < stages.indexOf('manual_retry_required'));
  assert.ok(stages.indexOf('accepted') > stages.indexOf('manual_retry_required'));
  assert.equal(s.commands.filter(row => row.method === 'Input.insertText').length, 1);
}));

test('original slow stream can finish during the recovery window without another request', async t => withSite(t, {
  send(s) { s.accept('<p>Partial answer</p>'); const id = s.network(200, false); setTimeout(() => { s.accept('<h2>Complete delayed answer</h2>'); s.finish(id); }, 5500); },
}, async s => {
  assert.equal(await settle(t, run(s, { timeout_seconds: 4 })), '## Complete delayed answer');
  assert.deepEqual(s.gestures, ['send']); assert.ok(s.progress.some(row => row.stage === 'manual_retry_required'));
  assert.ok(s.progress.some(row => row.stage === 'recovering'));
}));

test('a failed stream and stable old partial content never become a recovered answer', async t => withSite(t, {
  send(s) { s.accept('Partial must not be returned'); const id = s.network(200, false); s.debug.emit('message', {}, 'Network.loadingFailed', { requestId: id, errorText: 'net::ERR_CONNECTION_RESET' }); },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 2 })), error => error.code === 'recovery_timeout');
  assert.deepEqual(s.gestures, ['send']); assert.ok(!s.logs.some(row => row.event === 'adapter.complete'));
}));

test('a second failed response is retained instead of being cleared with the first failure', async t => withSite(t, {
  send(s) { s.accept(); s.network(503); }, retry(s) { s.network(503); s.accept('Old incomplete fragment'); },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 2 })), error => error.code === 'recovery_timeout');
  assert.deepEqual(s.gestures, ['send', 'retry']); assert.ok(!s.logs.some(row => row.event === 'adapter.complete'));
}));

test('new conversation during manual wait stops collection and excludes unrelated answers', async t => withSite(t, {
  send(s) { s.network(200); }, status(state, s) { if (state === 'manual_retry_required') {
    setTimeout(() => { s.accept('OTHER TASK ANSWER'); s.w.history.pushState({}, '', '/c/wrong-task');
      s.doc.querySelector('[data-role="user"]').textContent = 'Unrelated user question'; }, 300);
  } },
}, async s => {
  await assert.rejects(settle(t, run(s)), error => error.code === 'recovery_context_changed');
  assert.ok(!s.logs.some(row => row.event === 'adapter.complete')); assert.deepEqual(s.gestures, ['send']);
}));

test('cancellation interrupts manual recovery and releases observers without retrying', async t => {
  const controller = new AbortController();
  await withSite(t, { send(s) { s.network(200); }, status(state) { if (state === 'manual_retry_required') setTimeout(() => controller.abort(), 300); } }, async s => {
    await assert.rejects(settle(t, run(s, {}, controller.signal)), error => error.code === 'cancelled');
    assert.equal(s.adapter.active, false); assert.equal(s.debug.listenerCount('message'), 0); assert.equal(s.debug.isAttached(), false);
    assert.deepEqual(s.gestures, ['send']);
  });
});

test('zero recovery budget preserves immediate unconfirmed-submission diagnostics', async t => withSite(t, {
  send(s) { s.network(200); },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 0 })), error => error.code === 'submission_unconfirmed');
  assert.deepEqual(s.gestures, ['send']); assert.ok(!s.progress.some(row => row.stage === 'manual_retry_required'));
}));

test('zero recovery budget rejects the Qwen busy card instead of returning it as Markdown', async t => withSite(t, {
  send(s) { s.accept(qwenBusyHTML); s.network(200); },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 0 })), error => error.code === 'response_page_error');
  assert.deepEqual(s.gestures, ['send']); assert.ok(!s.logs.some(row => row.event === 'adapter.complete'));
  assert.ok(!s.progress.some(row => row.stage === 'manual_retry_required'));
}));

test('Qwen busy card without a user wrapper requests manual recovery but never clicks or completes', async t => withSite(t, {
  send(s) {
    s.doc.querySelector('textarea').value = '';
    s.doc.querySelector('#messages').innerHTML = `<article data-role="assistant">${qwenBusyHTML}</article>`;
    s.network(200);
  },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 2 })), error => error.code === 'recovery_timeout');
  assert.deepEqual(s.gestures, ['send']);
  assert.ok(s.progress.some(row => row.stage === 'manual_retry_required'));
  assert.ok(!s.progress.some(row => row.stage === 'retrying'));
  assert.ok(!s.logs.some(row => row.event === 'adapter.complete'));
}));

test('recovery stages are metadata only and never expose prompt or DOM bodies', () => {
  for (const [event, stage] of [['adapter.recovery_retry', 'retrying'], ['adapter.recovery_manual_required', 'manual_retry_required'], ['adapter.recovery_observed', 'recovering']]) {
    assert.deepEqual(webProgress(event, { payload: 'PRIVATE', reason: 'PRIVATE', url: 'PRIVATE' }), { stage });
  }
});

test('network recovery acknowledgements cannot clear failures of a later generation', async () => {
  const debug = new EventEmitter(); debug.sendCommand = async () => {};
  const stop = await startNetworkTrace({ debugger: debug }, () => {}, Date.now() + 1000);
  stop.arm();
  function fail(id) {
    const url = 'https://example.test/chat/completions';
    debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: id, type: 'Fetch', request: { method: 'POST', url } });
    debug.emit('message', {}, 'Network.responseReceived', { requestId: id, type: 'Fetch', response: { status: 503, url } });
  }
  fail('old'); const checkpoint = stop.checkpoint(); fail('new'); stop.acknowledgeFailures(checkpoint);
  assert.deepEqual(stop.state().failed, [{ network_id: 'new', status: 503 }]); assert.equal(stop.state().pending, 0); stop();
});

test('changed assistant content cannot confirm a prompt still sitting in the composer', async t => withSite(t, {
  send(s) {
    s.network(200);
    s.doc.querySelector('#messages').innerHTML = '<article data-role="assistant">Unrelated changed answer</article>';
  },
  status(state, s) { if (state === 'manual_retry_required') setTimeout(() => {
    s.doc.querySelector('[data-role="assistant"]').textContent = 'Changed again but original prompt is still unsent';
    s.network(200);
  }, 300); },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 2 })), error => error.code === 'recovery_timeout');
  assert.ok(!s.progress.some(row => row.stage === 'accepted'));
  assert.ok(!s.logs.some(row => row.event === 'adapter.complete'));
  assert.deepEqual(s.gestures, ['send']);
}));

test('buffered DOM tail after a failed network response cannot clear its failure', async t => withSite(t, {
  send(s) {
    s.accept('Partial before disconnection');
    const id = s.network(200, false);
    s.debug.emit('message', {}, 'Network.loadingFailed', { requestId: id, errorText: 'net::ERR_CONNECTION_RESET' });
    setTimeout(() => s.accept('Partial plus buffered tail, still incomplete'), 900);
  },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 2 })), error => error.code === 'recovery_timeout');
  assert.ok(!s.logs.some(row => row.event === 'adapter.complete'));
  assert.ok(!s.progress.some(row => row.stage === 'recovering'));
  assert.deepEqual(s.gestures, ['send']);
}));

test('second failed retry cannot be recovered by a buffered DOM flush', async t => withSite(t, {
  send(s) { s.accept(); s.network(503); },
  retry(s) {
    s.accept('Retry partial'); const id = s.network(200, false);
    s.debug.emit('message', {}, 'Network.loadingFailed', { requestId: id, errorText: 'net::ERR_CONNECTION_RESET' });
    setTimeout(() => s.accept('Retry partial with cached tail'), 900);
  },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 2 })), error => error.code === 'recovery_timeout');
  assert.ok(!s.logs.some(row => row.event === 'adapter.complete')); assert.deepEqual(s.gestures, ['send', 'retry']);
}));

test('HTTP 200 retry error cannot complete just because its error panel was removed', async t => withSite(t, {
  send(s) { s.accept(); s.network(503); },
  retry(s) {
    const id = s.network(200, false); s.accept('<p>Retry partial</p>');
    setTimeout(() => { s.accept('<p>Retry partial</p>' + errorHTML); s.finish(id); }, 700);
    setTimeout(() => s.accept('<p>Retry partial</p>'), 1400);
  },
}, async s => {
  await assert.rejects(settle(t, run(s, { recovery_timeout_seconds: 3 })), error => error.code === 'recovery_timeout');
  assert.ok(!s.logs.some(row => row.event === 'adapter.complete')); assert.deepEqual(s.gestures, ['send', 'retry']);
}));

test('manual new request can recover after an automatic retry returned an HTTP 200 error panel', async t => withSite(t, {
  send(s) { s.accept(); s.network(503); },
  retry(s) {
    const id = s.network(200, false); s.accept('Retry partial');
    setTimeout(() => { s.accept('<p>Retry partial</p>' + errorHTML); s.finish(id); }, 700);
  },
  status(state, s) { if (state === 'manual_retry_required') setTimeout(() => { s.network(200); s.accept('<h1>Final manual recovery</h1>'); }, 600); },
}, async s => {
  assert.equal(await settle(t, run(s)), '# Final manual recovery'); assert.deepEqual(s.gestures, ['send', 'retry']);
}));

test('cancelling while reacquiring the retry input lease prevents the retry gesture', async t => {
  const controller = new AbortController(); let acquired = 0, released = 0;
  await withSite(t, {
    send(s) { s.accept(); s.network(503); }, retry(s) { s.accept('Must not execute'); },
    adapterOptions: { async acquireInput() {
      acquired++;
      if (acquired === 2) controller.abort('input_view_hidden');
      return () => { released++; };
    } },
  }, async s => {
    await assert.rejects(settle(t, run(s, {}, controller.signal)), error => error.code === 'cancelled');
    assert.deepEqual(s.gestures, ['send']); assert.equal(acquired, 2); assert.equal(released, 2);
    assert.equal(s.debug.listenerCount('message'), 0); assert.equal(s.adapter.active, false);
  });
});

test('a second failure after manual recovery restores the manual-action status and can recover again', async t => {
  let manualPrompts = 0;
  await withSite(t, {
    send(s) { s.accept('Initial partial'); const id = s.network(200, false); s.debug.emit('message', {}, 'Network.loadingFailed', { requestId: id, errorText: 'net::ERR_CONNECTION_RESET' }); },
    status(state, s) { if (state === 'manual_retry_required') {
      manualPrompts++;
      setTimeout(() => {
        if (manualPrompts === 1) {
          s.accept('Manual partial'); const id = s.network(200, false);
          setTimeout(() => s.debug.emit('message', {}, 'Network.loadingFailed', { requestId: id, errorText: 'net::ERR_CONNECTION_RESET' }), 700);
        } else { s.accept('Final complete manual answer'); s.network(200); }
      }, 300);
    } },
  }, async s => {
    assert.equal(await settle(t, run(s)), 'Final complete manual answer');
    assert.equal(manualPrompts, 2); assert.equal(s.progress.filter(row => row.stage === 'manual_retry_required').length, 2);
    assert.deepEqual(s.gestures, ['send']);
  });
});

test('an already-ended HTTP 200 with no answer does not claim recovery or hide the manual action', async t => withSite(t, {
  send(s) { s.accept(''); s.network(200); },
}, async s => {
  await assert.rejects(settle(t, run(s, { timeout_seconds: 4, recovery_timeout_seconds: 2 })), error => error.code === 'recovery_timeout');
  assert.ok(s.progress.some(row => row.stage === 'manual_retry_required'));
  assert.ok(!s.progress.some(row => row.stage === 'recovering'));
  assert.ok(!s.statuses.some(row => row.state === 'recovering'));
  assert.deepEqual(s.gestures, ['send']);
}));
