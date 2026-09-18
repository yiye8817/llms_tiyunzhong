'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { webProgress } = require('../electron/web-progress.cjs');
const { startNetworkTrace } = require('../electron/adapter.cjs');

test('send acknowledgement is separate from website acceptance and never forwards private data', () => {
  assert.deepEqual(webProgress('adapter.rate_limit_wait_started', { wait_ms: 1250, payload: 'PRIVATE' }), { stage: 'rate_limited', wait_ms: 1250 });
  assert.equal(webProgress('adapter.dispatch', { payload: 'PRIVATE' }), null);
  assert.equal(webProgress('adapter.cdp_acknowledged', { method: 'Input.dispatchMouseEvent' }), null);
  assert.deepEqual(webProgress('adapter.submission_dispatched'), { stage: 'send_dispatched' });
  assert.deepEqual(webProgress('adapter.submission_accepted', { evidence: 'new_user_turn', payload: 'PRIVATE' }), { stage: 'accepted' });
  assert.deepEqual(webProgress('adapter.wait', { reason: 'streaming', capture: 'PRIVATE' }), { stage: 'generating' });
  assert.deepEqual(webProgress('adapter.wait', { reason: 'waiting_network_response' }), { stage: 'waiting_response' });
  assert.deepEqual(webProgress('adapter.wait', { reason: 'incomplete_structured_response' }), { stage: 'collecting' });
});

test('only observed generation response HTTP status becomes server-returned progress', async () => {
  const debug = new EventEmitter(), progress = [];
  debug.sendCommand = async () => {};
  const stop = await startNetworkTrace({ debugger: debug }, (event, fields) => {
    const row = webProgress(event, fields); if (row) progress.push(row);
  }, Date.now() + 1000);
  stop.arm();
  for (const [id, url] of [['notify', 'https://example.test/notifications'], ['chat', 'https://example.test/api/chat/completions']]) {
    debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: id, type: 'Fetch', request: { method: 'POST', url, postData: 'PRIVATE' } });
    debug.emit('message', {}, 'Network.responseReceived', { requestId: id, type: 'Fetch', response: { url, status: 429, headers: { Cookie: 'PRIVATE' } } });
  }
  assert.deepEqual(progress, [{ stage: 'server_responded', http_status: 429 }]);
  stop();
  assert.equal(debug.listenerCount('message'), 0);
});
