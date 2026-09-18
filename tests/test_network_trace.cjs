'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { startNetworkTrace } = require('../electron/adapter.cjs');

test('network trace records HTTP failure status without bodies cookies or query tokens', async () => {
  const debug = new EventEmitter(), rows = [], calls = [];
  debug.sendCommand = async method => { calls.push(method); };
  const stop = await startNetworkTrace({ debugger: debug }, (event, fields) => rows.push({ event, ...fields }), Date.now() + 1000);
  debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: 'n1', type: 'Fetch', request: { method: 'POST', url: 'https://chat.example.test/api/chat?session=SECRET', headers: { Cookie: 'SESSION_SECRET' }, postData: 'PRIVATE_BODY' } });
  debug.emit('message', {}, 'Network.responseReceived', { requestId: 'n1', type: 'Fetch', response: { url: 'https://chat.example.test/api/chat?session=SECRET', status: 429, headers: { 'set-cookie': 'SESSION_SECRET' } } });
  debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: 'image', type: 'Image', request: { url: 'https://example.test/image.png' } });
  assert.deepEqual(calls, ['Network.enable']);
  assert.equal(rows.length, 2);
  assert.equal(rows[1].status, 429);
  assert.equal(rows[1].method, 'POST');
  assert.equal(rows[1].payload.url, 'https://chat.example.test/api/chat');
  assert.doesNotMatch(JSON.stringify(rows), /SECRET|PRIVATE_BODY|set-cookie/);
  stop();
  assert.equal(debug.listenerCount('message'), 0);
});

test('network observer is removed if diagnostic domain is unavailable', async () => {
  const debug = new EventEmitter(), rows = [];
  debug.sendCommand = async () => { throw new Error('unsupported'); };
  const stop = await startNetworkTrace({ debugger: debug }, (event, fields) => rows.push({ event, ...fields }), Date.now() + 1000);
  assert.equal(debug.listenerCount('message'), 0);
  assert.equal(rows[0].event, 'adapter.network_trace_unavailable');
  stop();
});

test('network loading failure reports the tracked request and ignores unrelated traffic', async () => {
  const debug = new EventEmitter(), rows = [];
  debug.sendCommand = async () => {};
  const stop = await startNetworkTrace({ debugger: debug }, (event, fields) => rows.push({ event, ...fields }), Date.now() + 1000);
  debug.emit('message', {}, 'Network.loadingFailed', { requestId: 'unknown', errorText: 'ignore' });
  debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: 'n1', type: 'XHR', request: { method: 'POST', url: 'https://example.test/generate' } });
  debug.emit('message', {}, 'Network.loadingFailed', { requestId: 'n1', errorText: 'net::ERR_CONNECTION_RESET' });
  assert.equal(rows.length, 2);
  assert.equal(rows[1].event, 'adapter.network_failed');
  assert.equal(rows[1].payload.error, 'net::ERR_CONNECTION_RESET');
  stop();
});


test('only post-send generation requests affect completion; unrelated streams remain diagnostic', async () => {
  const debug = new EventEmitter(), rows = [];
  debug.sendCommand = async () => {};
  const stop = await startNetworkTrace({ debugger: debug }, (event, fields) => rows.push({ event, ...fields }), Date.now() + 1000);
  const start = (id, url, method = 'POST') => debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: id, type: 'Fetch', request: { method, url } });
  start('before', 'https://qwen.example/api/v2/chat/completions');
  stop.arm();
  start('analytics', 'https://analytics.example/events');
  start('poll', 'https://qwen.example/api/v2/chat/completions', 'GET');
  assert.deepEqual(stop.state(), { observed: false, pending: 0, failed: [] });
  start('chat', 'https://qwen.example/api/v2/chat/completions');
  assert.equal(stop.state().pending, 1);
  debug.emit('message', {}, 'Network.loadingFinished', { requestId: 'chat' });
  assert.deepEqual(stop.state(), { observed: true, pending: 0, failed: [] });
  assert.ok(rows.some(row => row.event === 'adapter.network_response_finished'));
  start('other-stream', 'https://qwen.example/opaque-endpoint');
  debug.emit('message', {}, 'Network.responseReceived', { requestId: 'other-stream', type: 'Fetch', response: { url: 'https://qwen.example/opaque-endpoint', status: 200, mimeType: 'text/event-stream' } });
  assert.equal(stop.state().pending, 0);
  debug.emit('message', {}, 'Network.loadingFailed', { requestId: 'other-stream', errorText: 'net::ERR_CONNECTION_RESET' });
  assert.equal(stop.state().pending, 0);
  assert.deepEqual(stop.state().failed, []);
  const unrelatedResponse = rows.find(row => row.event === 'adapter.network_response' && row.network_id === 'other-stream');
  assert.equal(unrelatedResponse.mime_type, 'text/event-stream');
  assert.equal(unrelatedResponse.tracked_response, false);
  stop();
});

test('notification GET SSE cannot delay or fail a completed generation', async () => {
  const debug = new EventEmitter(), rows = [];
  debug.sendCommand = async () => {};
  const stop = await startNetworkTrace({ debugger: debug }, (event, fields) => rows.push({ event, ...fields }), Date.now() + 1000);
  stop.arm();
  debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: 'chat', type: 'Fetch', request: { method: 'POST', url: 'https://qwen.example/api/v2/chat/completions' } });
  debug.emit('message', {}, 'Network.loadingFinished', { requestId: 'chat' });
  for (const [id, url] of [
    ['same-origin-feed', 'https://qwen.example/events'],
    ['cross-origin-feed', 'https://notifications.example/events'],
    ['known-path-get', 'https://qwen.example/api/v2/chat/completions'],
  ]) {
    debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: id, type: 'Fetch', request: { method: 'GET', url } });
    debug.emit('message', {}, 'Network.responseReceived', { requestId: id, type: 'Fetch', response: { status: 200, url, mimeType: 'text/event-stream' } });
    assert.deepEqual(stop.state(), { observed: true, pending: 0, failed: [] });
    debug.emit('message', {}, 'Network.loadingFailed', { requestId: id, errorText: 'net::ERR_CONNECTION_RESET' });
    assert.deepEqual(stop.state(), { observed: true, pending: 0, failed: [] });
    assert.ok(rows.some(row => row.event === 'adapter.network_failed' && row.network_id === id));
  }
  stop();
});

test('failure of a known generation stream is retained after a successful HTTP status', async () => {
  const debug = new EventEmitter(); debug.sendCommand = async () => {};
  const stop = await startNetworkTrace({ debugger: debug }, () => {}, Date.now() + 1000);
  stop.arm();
  debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: 'chat', type: 'Fetch', request: { method: 'POST', url: 'https://qwen.example/api/v2/chat/completions' } });
  debug.emit('message', {}, 'Network.responseReceived', { requestId: 'chat', type: 'Fetch', response: { status: 200, url: 'https://qwen.example/api/v2/chat/completions', mimeType: 'text/event-stream' } });
  assert.equal(stop.state().pending, 1);
  debug.emit('message', {}, 'Network.loadingFailed', { requestId: 'chat', errorText: 'net::ERR_CONNECTION_RESET' });
  assert.deepEqual(stop.state(), { observed: true, pending: 0, failed: [{ network_id: 'chat', code: 'net::ERR_CONNECTION_RESET' }] });
  stop();
});

test('HTTP rejection on a tracked response is retained even after loadingFinished', async () => {
  const debug = new EventEmitter(); debug.sendCommand = async () => {};
  const stop = await startNetworkTrace({ debugger: debug }, () => {}, Date.now() + 1000);
  stop.arm();
  debug.emit('message', {}, 'Network.requestWillBeSent', { requestId: 'chat', type: 'Fetch', request: { method: 'POST', url: 'https://qwen.example/api/v2/chat/completions' } });
  debug.emit('message', {}, 'Network.responseReceived', { requestId: 'chat', type: 'Fetch', response: { status: 503, url: 'https://qwen.example/api/v2/chat/completions' } });
  debug.emit('message', {}, 'Network.loadingFinished', { requestId: 'chat' });
  assert.deepEqual(stop.state(), { observed: true, pending: 0, failed: [{ network_id: 'chat', status: 503 }] });
  stop();
});
