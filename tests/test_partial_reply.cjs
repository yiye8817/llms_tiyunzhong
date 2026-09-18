'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { structuredReplyState } = require('../electron/reply-extraction.cjs');
const { CompletionTracker, WebsiteAdapter } = require('../electron/adapter.cjs');

const answer = rawText => ({ rawText, text: rawText, html: `<pre>${rawText}</pre>`, codeLanguages: ['json'], jsonTextSafe: true });
const fragments = ['{"type":"action","', '{"type":"action","tool":"shell.run","arguments', '{"type":"action","tool":"shell'];
const complete = '{"type":"action","tool":"shell.run","arguments":{"argv":["pwd"]},"summary":"查看目录"}';

test('the three exact agent2 log fragments cannot complete during a DOM pause', () => {
  for (const raw of fragments) {
    const tracker = new CompletionTracker({ answers: [] }, { stableSeconds: 1, minWaitSeconds: 0, startedAt: 0 });
    const snapshot = { answers: [answer(raw)], stopping: false, responsePending: false };
    tracker.update(snapshot, 10);
    const paused = tracker.update(snapshot, 60000);
    assert.equal(paused.done, false);
    assert.equal(paused.reason, 'incomplete_structured_response');
    assert.equal(paused.capture.reason, 'unterminated_string');
    assert.equal(paused.capture.chars, raw.length);
    const finished = { answers: [answer(complete)], stopping: false, responsePending: false };
    assert.equal(tracker.update(finished, 61000).done, false);
    assert.equal(tracker.update(finished, 63001).done, true);
  }
});

test('every proper prefix of representative JSON remains pending without rewriting strings', () => {
  const objects = [
    { type: 'final', answer: '汉字 { } [ ] \\ "quoted" \n next', values: [true, false, null, -1.25e-12, {}, []] },
    { arguments: { command: "printf 'literal } ], { \\n'" }, plan: ['one', { nested: ['two'] }] },
  ];
  for (const value of objects) {
    const source = JSON.stringify(value);
    for (let end = 1; end < source.length; end++) assert.equal(structuredReplyState(answer(source.slice(0, end))).incomplete, true, source.slice(0, end));
    assert.equal(structuredReplyState(answer(source)).incomplete, false);
  }
  for (const prefix of ['{"x":-', '{"x":1.', '{"x":1e', '{"x":1e-', '{"x":1.2e+', '{"x":"\\u12', '{"x":"\\']) {
    assert.equal(structuredReplyState(answer(prefix)).incomplete, true, prefix);
  }
});

test('malformed closed JSON reaches caller repair instead of waiting for more text', () => {
  for (const source of [
    '{"command":"python3 -c "print(1)"","plan":[]}',
    '{"type":"final","answer":"done",}',
    '{"type":"action","plan":\\["one"\\]}',
    '{"x":01}', '{"x":truth}', '{"x":"\\q"}', '{"x":1e+}',
    '{"type":"final"}\n{"type":"action"}',
  ]) {
    const state = structuredReplyState(answer(source));
    assert.equal(state.incomplete, false, source);
    assert.equal(state.reason, 'invalid_json_syntax', source);
  }
});

test('literal JSON fences wait for object and closing fence but mixed Markdown does not', () => {
  for (const raw of ['```json', '```json\n', '```json\n{"type":"', `~~~json\n${complete}`, `\x60\x60\x60\n${fragments[0]}`]) {
    assert.equal(structuredReplyState(answer(raw)).incomplete, true, raw);
  }
  for (const raw of [`\x60\x60\x60json\n${complete}\n\x60\x60\x60`, `~~~json\n${complete}\n~~~`]) {
    assert.equal(structuredReplyState(answer(raw)).incomplete, false);
  }
  for (const raw of [
    'Use { and [ to start a container.',
    'An example:\n```json\n{"type":"action"\n```',
    '```python\nresult = {',
    '```\nfunction example() {',
    '{name} is a placeholder.',
  ]) assert.equal(structuredReplyState(answer(raw)), null, raw);
  for (const override of [{ jsonTextSafe: false }, { codeLanguages: ['python'] }, { codeLanguages: ['json', 'json'] }]) {
    assert.equal(structuredReplyState({ ...answer(fragments[0]), ...override }), null);
  }
  assert.equal(structuredReplyState(answer(`\x60\x60\x60json\n${complete}\n\x60\x60\x60\nSome explanation.`)).incomplete, false);
});

test('closed JSON still waits for tracked network and observed stop state', () => {
  const tracker = new CompletionTracker({ answers: [] }, { stableSeconds: 1, minWaitSeconds: 0, startedAt: 0 });
  const snapshot = { answers: [answer(complete)], stopping: true, responsePending: true };
  tracker.update(snapshot, 10);
  assert.equal(tracker.update(snapshot, 60000).reason, 'waiting_network_response');
  assert.equal(tracker.update({ ...snapshot, responsePending: false }, 61000).reason, 'streaming');
  assert.equal(tracker.update({ ...snapshot, responsePending: false, stopping: false }, 62001).done, true);
});

function adapterFixture({ finish }) {
  const dom = new JSDOM('<textarea></textarea><button id="send">Send</button>', { url: 'https://fixture.invalid/', runScripts: 'outside-only' });
  dom.window.HTMLElement.prototype.getClientRects = () => [{ width: 100, height: 20 }];
  dom.window.HTMLElement.prototype.getBoundingClientRect = () => ({ left: 20, top: 20, width: 100, height: 20 });
  let sends = 0, observations = 0;
  const events = [];
  dom.window.document.querySelector('#send').addEventListener('click', () => {
    sends++;
    const article = dom.window.document.createElement('article');
    article.className = 'assistant';
    article.textContent = fragments[0];
    dom.window.document.body.append(article);
  });
  const wc = {
    debugger: {
      attached: false, isAttached() { return this.attached; }, attach() { this.attached = true; }, detach() { this.attached = false; },
      async sendCommand(method, params) {
        if (method === 'Input.dispatchMouseEvent' && params.type === 'mouseReleased') dom.window.document.querySelector('#send').click();
      },
    },
    isDestroyed: () => false, stop: () => {}, loadURL: async () => {},
    executeJavaScriptInIsolatedWorld(_id, sources) {
      if (sends && sources[0].code.includes('("inspect",')) {
        observations++;
        if (finish && observations >= 3) dom.window.document.querySelector('.assistant').textContent = complete;
      }
      return Promise.resolve(dom.window.eval(sources[0].code));
    },
  };
  const selectors = { input: ['textarea'], send: ['#send'], assistant: ['.assistant'], stop: [], new_chat: [] };
  return {
    dom, events, sends: () => sends,
    adapter: new WebsiteAdapter(wc, { id: 'deepseek', url: 'https://fixture.invalid/', selectors }, () => {}, (event, fields) => events.push({ event, ...fields })),
  };
}

test('adapter waits through stable partial text and emits exactly one complete reply from one send', async () => {
  const context = adapterFixture({ finish: true });
  try {
    const result = await context.adapter.run({ job_id: 'fragment-then-complete', purpose: 'candidate', prompt: 'Return one JSON object.', timeout_seconds: 8, stable_seconds: 0.02, min_wait_seconds: 0 }, new AbortController().signal);
    assert.equal(result, complete);
    assert.equal(context.sends(), 1);
    const waiting = context.events.find(event => event.event === 'adapter.wait' && event.reason === 'incomplete_structured_response');
    assert.equal(waiting.capture.chars, 18);
    const completions = context.events.filter(event => event.event === 'adapter.complete');
    assert.equal(completions.length, 1);
    assert.equal(completions[0].payload.markdown, complete);
    assert.equal(completions[0].payload.completion_evidence.capture.incomplete, false);
  } finally { context.dom.window.close(); }
});

test('an unfinished JSON response times out with capture evidence and never returns or resends a fragment', async () => {
  const context = adapterFixture({ finish: false });
  try {
    await assert.rejects(context.adapter.run({ job_id: 'fragment-timeout', purpose: 'candidate', prompt: 'Return one JSON object.', timeout_seconds: 3.5, stable_seconds: 0.02, min_wait_seconds: 0 }, new AbortController().signal), error => {
      assert.equal(error.code, 'timeout');
      assert.match(error.message, /结构化回复仍不完整/);
      assert.equal(error.details.capture.incomplete, true);
      assert.equal(error.details.capture.chars, 18);
      return true;
    });
    assert.equal(context.sends(), 1);
    assert.equal(context.events.filter(event => event.event === 'adapter.complete').length, 0);
    const failed = context.events.find(event => event.event === 'adapter.failed');
    assert.equal(failed.wait_reason, 'incomplete_structured_response');
    assert.equal(failed.payload.diagnostics.capture.reason, 'unterminated_string');
  } finally { context.dom.window.close(); }
});
