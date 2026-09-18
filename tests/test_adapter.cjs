'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { WebsiteAdapter, CompletionTracker, markdownFromHTML, pageAction, submissionEvidence, dispatchTrustedInput } = require('../electron/adapter.cjs');

const selectors = { input: ['textarea'], send: ['#send'], assistant: ['.assistant'], stop: ['#stop'], new_chat: [] };
const answer = text => ({ html: `<p>${text}</p>`, text });

test('completion never returns an unchanged preexisting answer', () => {
  const previous = { answers: [answer('old response')], stopping: false };
  const tracker = new CompletionTracker(previous, { stableSeconds: 2, minWaitSeconds: 3, startedAt: 0 });
  assert.equal(tracker.update(previous, 100000).done, false);
});

test('completion requires new content, stopped streaming and stability', () => {
  const tracker = new CompletionTracker({ answers: [answer('old')] }, { stableSeconds: 2, minWaitSeconds: 3, startedAt: 0 });
  assert.equal(tracker.update({ answers: [answer('old'), answer('new')], stopping: true }, 500).done, false);
  assert.equal(tracker.update({ answers: [answer('old'), answer('new')], stopping: true }, 10000).done, false);
  assert.equal(tracker.update({ answers: [answer('old'), answer('new final')], stopping: false }, 11000).done, false);
  assert.equal(tracker.update({ answers: [answer('old'), answer('new final')], stopping: false }, 13001).done, true);
});

test('a site without stop indicators gets twice the configured stable period', () => {
  const tracker = new CompletionTracker({ answers: [] }, { stableSeconds: 2, minWaitSeconds: 0, startedAt: 0 });
  const current = { answers: [answer('response')], stopping: false };
  assert.equal(tracker.update(current, 100).done, false);
  assert.equal(tracker.update(current, 3000).done, false);
  assert.equal(tracker.update(current, 4200).done, true);
});

test('Markdown extraction retains headings, GFM tables, links and embedded fences', () => {
  const md = markdownFromHTML('<h2>Result</h2><p>Check <a href="https://example.com/a">source</a>.</p><pre><code class="language-markdown">```python\nprint(1)\n```\n</code></pre><table><thead><tr><th>Name</th><th>Value</th></tr></thead><tbody><tr><td>A</td><td>2</td></tr></tbody></table>');
  assert.match(md, /## Result/);
  assert.match(md, /\[source\]\(https:\/\/example.com\/a\)/);
  assert.match(md, /````markdown\n```python/);
  assert.match(md, /\| Name \| Value \|/);
});

function fixture(html) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:4567/', runScripts: 'outside-only' });
  dom.window.HTMLElement.prototype.getClientRects = function () { return [{ width: 100, height: 30 }]; };
  dom.window.HTMLElement.prototype.getBoundingClientRect = function () { return { left: 20, top: 20, width: 100, height: 30 }; };
  const args = { selectors, prompt: 'Explain this code\nprint(1)', id: 'test-job', deadline: Date.now() + 10000 };
  const act = (action, changed = {}) => dom.window.eval(`(${pageAction.toString()})(${JSON.stringify(action)},${JSON.stringify({ ...args, ...changed })})`);
  return { dom, args, act };
}

test('DOM adapter reserves exactly one trusted submission and never synthesizes clicks', () => {
  const { dom, act } = fixture('<textarea></textarea><button id="send">Send</button><div class="user">SECRET USER</div><article class="assistant"><h2>Answer</h2><pre><code class="language-python">print(1)</code><button>Copy code</button></pre><button>Copy response</button></article>');
  let clicks = 0;
  dom.window.document.querySelector('#send').addEventListener('click', () => clicks++);
  assert.equal(act('prepare').ready, true);
  assert.equal(act('canSubmit').ready, true);
  assert.equal(act('claimSubmit').method, 'button');
  assert.equal(clicks, 0);
  assert.throws(() => act('claimSubmit'), /already submitted/);
  const result = act('inspect');
  assert.equal(result.answers.length, 1);
  assert.doesNotMatch(result.answers[0].html, /SECRET USER|Copy response|Copy code/);
  assert.match(result.answers[0].html, /language-python/);
  dom.window.close();
});

test('Enter path is reserved without DOM key events and disabled send never falls back', () => {
  const { dom, act } = fixture('<textarea></textarea>');
  let enters = 0;
  dom.window.document.querySelector('textarea').addEventListener('keydown', event => { if (event.key === 'Enter') enters++; });
  act('prepare');
  assert.equal(act('canSubmit').method, 'enter');
  assert.equal(act('claimSubmit').method, 'enter');
  assert.equal(enters, 0);
  assert.throws(() => act('claimSubmit'), /already submitted/);
  dom.window.close();
  const disabled = fixture('<textarea></textarea><button id="send" disabled>Send</button>');
  disabled.act('prepare');
  assert.equal(disabled.act('canSubmit').ready, false);
  assert.equal(disabled.act('claimSubmit').reason, 'send_disabled');
  disabled.dom.window.close();
});

test('cancelled or expired DOM context never submits', () => {
  const { dom, act } = fixture('<textarea></textarea><button id="send">Send</button>');
  let clicks = 0;
  dom.window.document.querySelector('#send').addEventListener('click', () => clicks++);
  act('prepare');
  act('abort');
  assert.throws(() => act('claimSubmit'), /cancelled/);
  assert.throws(() => act('prepare', { deadline: Date.now() - 1 }), /deadline/);
  assert.equal(clicks, 0);
  dom.window.close();
});

test('whole adapter navigates once, sends once, and returns the new assistant reply', async () => {
  let current, sends = 0, loads = 0;
  const logs = [], progress = [];
  const wc = {
    debugger: {
      attached: false, isAttached() { return this.attached; }, attach() { this.attached = true; }, detach() { this.attached = false; },
      async sendCommand(method, params) {
        if (method === 'Input.dispatchMouseEvent' && params.type === 'mouseReleased') current.dom.window.document.querySelector('#send').click();
      },
    },
    isDestroyed: () => false,
    stop: () => {},
    async loadURL() {
      loads++;
      current = fixture('<textarea></textarea><button id="send">Send</button><article class="assistant">Old answer</article>');
      current.dom.window.document.querySelector('#send').addEventListener('click', () => {
        sends++;
        const output = current.dom.window.document.createElement('article');
        output.className = 'assistant';
        output.innerHTML = '<h2>New answer</h2><p>Actual fixture response</p>';
        current.dom.window.document.body.append(output);
      });
    },
    executeJavaScriptInIsolatedWorld(_id, sources) { return Promise.resolve(current.dom.window.eval(sources[0].code)); },
  };
  const adapter = new WebsiteAdapter(wc, { url: 'http://127.0.0.1:4567/', selectors }, () => {}, (event, fields) => logs.push({ event, ...fields }));
  adapter.lastSubmittedAt = Date.now();
  const startedAt = Date.now();
  const markdown = await adapter.run({ job_id: 'integration', purpose: 'candidate', prompt: 'Question', timeout_seconds: 10, stable_seconds: 0.02, min_wait_seconds: 0, access_interval_seconds: 0.05 }, new AbortController().signal, state => progress.push(state));
  assert.ok(Date.now() - startedAt >= 35, 'rapid access waits before navigating');
  assert.ok(logs.some(row => row.event === 'adapter.rate_limit_wait_started'));
  assert.ok(logs.some(row => row.event === 'adapter.rate_limit_wait_finished'));
  assert.ok(progress.some(row => row.stage === 'rate_limited'));
  assert.equal(loads, 1);
  assert.equal(sends, 1);
  assert.match(markdown, /## New answer/);
  assert.doesNotMatch(markdown, /Old answer/);
  assert.equal(adapter.active, false);
  current.dom.window.close();
});

function qwenFixture(html, custom = ['[data-role="assistant"] .qwen-markdown', '.chat-assistant .qwen-markdown', '.qwen-markdown']) {
  const context = fixture(`<textarea></textarea>${html}`);
  const base = context.act;
  context.act = (action, changed = {}) => base(action, { provider_id: 'qwen', selectors: { ...selectors, assistant: custom }, ...changed });
  return context;
}

test('legacy Qwen fragment selectors aggregate paragraphs, code and table into one latest turn', () => {
  const { dom, act } = qwenFixture('<article data-role="assistant"><div class="qwen-markdown">Old answer</div></article><article class="chat-assistant"><div class="qwen-markdown"><h2>Complete answer</h2><p>First paragraph.</p></div><div class="qwen-markdown"><pre><code class="language-python">print(42)\n</code></pre></div><div class="qwen-markdown"><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table></div></article>');
  const result = act('inspect');
  assert.equal(result.answers.length, 2);
  const md = markdownFromHTML(result.answers.at(-1).html);
  assert.match(md, /## Complete answer/);
  assert.match(md, /First paragraph/);
  assert.match(md, /```python\nprint\(42\)/);
  assert.match(md, /\| A \| B \|/);
  assert.doesNotMatch(md, /Old answer/);
  assert.equal((md.match(/print\(42\)/g) || []).length, 1);
  dom.window.close();
});

test('Qwen excludes user, reasoning, hidden copies and controls while retaining final citations', () => {
  const { dom, act } = qwenFixture('<article data-role="user"><div class="qwen-markdown">USER SECRET</div></article><article data-role="assistant"><div class="thinking-content"><div class="qwen-markdown">REASONING SECRET</div></div><div class="qwen-markdown"><p>Final answer <a href="https://example.com/source">source</a></p><div hidden>HIDDEN SECRET</div><div role="toolbar">TOOLBAR SECRET</div><button>Copy response</button></div></article>');
  const result = act('inspect');
  assert.equal(result.answers.length, 1);
  const md = markdownFromHTML(result.answers[0].html);
  assert.match(md, /Final answer \[source\]\(https:\/\/example.com\/source\)/);
  assert.doesNotMatch(md, /SECRET|Copy response/);
  dom.window.close();
});

test('Qwen recognizes a new author-owned turn after older matches using legacy markup', () => {
  const { dom, act } = qwenFixture('<article data-role="assistant"><div class="qwen-markdown">Old response</div></article><article id="response-message-new"><div class="markdown"><h2>Latest response</h2></div></article>');
  assert.equal(act('inspect').answers.length, 2);
  assert.match(markdownFromHTML(act('inspect').answers.at(-1).html), /## Latest response/);
  dom.window.close();
});

test('Qwen keeps the latest empty/thinking turn pending instead of returning previous output', () => {
  const { dom, act } = qwenFixture('<article data-role="assistant"><div class="qwen-markdown">Old response</div></article><article data-role="assistant"><div class="thinking-content">Reasoning only</div></article>');
  const snap = act('inspect');
  assert.equal(snap.answers.length, 2);
  assert.equal(snap.answers.at(-1).text, '');
  const tracker = new CompletionTracker({ answers: [answer('Old response')] }, { stableSeconds: 0, minWaitSeconds: 0, startedAt: 0 });
  assert.equal(tracker.update(snap, 10000).done, false);
  dom.window.close();
});

test('Qwen multipart streaming changes reset stability for the whole turn', () => {
  const { dom, act } = qwenFixture('<article data-role="assistant"><div class="qwen-markdown">First part</div></article>');
  const tracker = new CompletionTracker({ answers: [] }, { stableSeconds: 2, minWaitSeconds: 0, startedAt: 0 });
  assert.equal(tracker.update(act('inspect'), 100).done, false);
  dom.window.document.querySelector('article').insertAdjacentHTML('beforeend', '<div class="qwen-markdown">Second part</div>');
  assert.equal(tracker.update(act('inspect'), 3900).done, false);
  assert.equal(tracker.update(act('inspect'), 4100).done, false);
  const result = tracker.update(act('inspect'), 8000);
  assert.equal(result.done, true);
  assert.match(result.answer.text, /First partSecond part/);
  dom.window.close();
});

test('selected root PRE and heading semantics survive HTML extraction', () => {
  for (const [html, selector, pattern] of [
    ['<pre class="assistant"><code class="language-js">const n = 1;</code></pre>', 'pre.assistant', /```js\nconst n = 1;/],
    ['<h2 class="assistant">Root heading</h2>', 'h2.assistant', /## Root heading/],
  ]) {
    const { dom, act } = fixture(html);
    const result = act('inspect', { selectors: { ...selectors, assistant: [selector] } });
    assert.match(markdownFromHTML(result.answers[0].html), pattern);
    dom.window.close();
  }
});

test('ChatGPT fallback finds composer-submit-button despite unchanged legacy selectors', () => {
  const { dom, act } = fixture('<textarea></textarea><button id="composer-submit-button"><svg></svg></button>');
  act('prepare', { provider_id: 'chatgpt' });
  const target = act('canSubmit', { provider_id: 'chatgpt' });
  assert.equal(target.ready, true);
  assert.equal(target.method, 'button');
  assert.equal(target.sendSource, 'provider');
  dom.window.close();
});

test('custom send selector has priority over provider and semantic fallbacks', () => {
  const { dom, act } = fixture('<textarea></textarea><button id="send">Custom</button><button id="composer-submit-button">Send</button>');
  act('prepare', { provider_id: 'chatgpt' });
  assert.equal(act('canSubmit', { provider_id: 'chatgpt' }).sendSource, 'configured');
  dom.window.close();
});

test('semantic Send prompt control is preferred over Enter but arbitrary form submit is not', () => {
  const { dom, act } = fixture('<form><textarea></textarea><button aria-label="Send prompt">→</button></form>');
  act('prepare');
  assert.equal(act('canSubmit').sendSource, 'semantic');
  dom.window.close();
  const other = fixture('<form><textarea></textarea><button type="submit">Delete account</button></form>');
  other.act('prepare');
  assert.equal(other.act('canSubmit').reason, 'unrecognized_form_submit');
  other.dom.window.close();
});

test('obscured send control does not reserve or send a click', () => {
  const { dom, act } = fixture('<textarea></textarea><button id="send">Send</button><div id="overlay"></div>');
  dom.window.document.elementFromPoint = () => dom.window.document.querySelector('#overlay');
  act('prepare');
  assert.equal(act('claimSubmit').reason, 'send_obscured');
  dom.window.document.elementFromPoint = () => dom.window.document.querySelector('#send');
  assert.equal(act('claimSubmit').ready, true);
  dom.window.close();
});

test('submission confirmation rejects filled or cleared composer without response evidence', () => {
  const before = { answers: [answer('old')], userCount: 2 };
  assert.equal(submissionEvidence(before, { ...before, inputEmpty: false }), null);
  assert.equal(submissionEvidence(before, { ...before, inputEmpty: true }), null);
  assert.equal(submissionEvidence(before, { ...before, inputEmpty: true, userCount: 3, lastUserMatchesPrompt: false }), null);
  assert.equal(submissionEvidence(before, { ...before, inputEmpty: true, userCount: 3, lastUserMatchesPrompt: true }), 'new_user_turn');
  assert.equal(submissionEvidence(before, { ...before, stopping: true }), 'generation_indicator');
  assert.equal(submissionEvidence(before, { answers: [...before.answers, answer('new')] }), 'new_assistant_content');
});

test('CDP dispatches one mouse gesture or one Enter and never retries a rejected command', async () => {
  const calls = [];
  const wc = { debugger: { async sendCommand(method, params) { calls.push({ method, ...params }); } } };
  await dispatchTrustedInput(wc, { method: 'button', x: 10, y: 20 }, Date.now() + 1000);
  assert.deepEqual(calls.map(c => c.type), ['mousePressed', 'mouseReleased']);
  calls.length = 0;
  await dispatchTrustedInput(wc, { method: 'enter' }, Date.now() + 1000);
  assert.deepEqual(calls.map(c => c.type), ['keyDown', 'keyUp']);
  let attempts = 0;
  await assert.rejects(dispatchTrustedInput({ debugger: { sendCommand() { attempts++; return Promise.reject(new Error('lost target')); } } }, { method: 'button', x: 10, y: 20 }, Date.now() + 1000), /lost target/);
  assert.equal(attempts, 1);
});

test('adapter diagnostics put requested content in explicit payloads and retain structural metadata', async () => {
  const messages = []; let current, clicks = 0;
  const wc = {
    isDestroyed: () => false, stop() {},
    debugger: { attached: false, isAttached() { return this.attached; }, attach() { this.attached = true; }, detach() { this.attached = false; }, async sendCommand(method, params) { if (method === 'Input.insertText') current.dom.window.document.querySelector('textarea').value = params.text; if (params.type === 'mouseReleased') { clicks++; current.dom.window.document.body.insertAdjacentHTML('beforeend', '<article class="assistant">PRIVATE RESPONSE</article>'); } } },
    async loadURL() { current = fixture('<textarea></textarea><button id="composer-submit-button"></button>'); },
    executeJavaScriptInIsolatedWorld(_id, sources) { return Promise.resolve(current.dom.window.eval(sources[0].code)); },
  };
  const adapter = new WebsiteAdapter(wc, { id: 'chatgpt', url: 'http://127.0.0.1:4567/', selectors }, () => {}, (event, fields) => messages.push({ event, ...fields }));
  assert.equal(await adapter.run({ job_id: 'logging', purpose: 'candidate', prompt: 'PRIVATE PROMPT', timeout_seconds: 10, stable_seconds: 0.01, min_wait_seconds: 0 }), 'PRIVATE RESPONSE');
  assert.equal(clicks, 1);
  assert.ok(messages.find(m => m.event === 'adapter.submission_accepted'));
  assert.ok(messages.find(m => m.event === 'adapter.complete' && m.markdown_length === 16));
  assert.equal(messages.find(m => m.event === 'adapter.start').payload.prompt, 'PRIVATE PROMPT');
  assert.equal(messages.find(m => m.event === 'adapter.complete').payload.markdown, 'PRIVATE RESPONSE');
  assert.ok(messages.some(m => m.event === 'adapter.input_verified' && m.verified));
  assert.ok(messages.some(m => m.event === 'adapter.cdp_acknowledged' && m.input_event === 'mouseReleased'));
  assert.doesNotMatch(JSON.stringify(messages.map(({payload, ...metadata}) => metadata)), /PRIVATE|<article|textarea|127\.0\.0\.1/);
  current.dom.window.close();
});

test('Qwen extraction retains display:contents wrappers with visible code and tables', () => {
  const { dom, act } = qwenFixture('<article data-role="assistant"><div class="qwen-markdown" style="display:contents"><h2>Visible title</h2><pre><code class="language-python">print(99)</code></pre><table><tr><th>A</th></tr><tr><td>1</td></tr></table></div><div style="display:none">HIDDEN CONTENT</div></article>');
  dom.window.document.querySelector('.qwen-markdown').getClientRects = () => [];
  const md = markdownFromHTML(act('inspect').answers[0].html);
  assert.match(md, /## Visible title/);
  assert.match(md, /```python\nprint\(99\)/);
  assert.match(md, /\| A \|/);
  assert.doesNotMatch(md, /HIDDEN CONTENT/);
  dom.window.close();
});

test('Qwen aria-busy turn keeps completion waiting after stop control disappears', () => {
  const { dom, act } = qwenFixture('<article data-role="assistant" aria-busy="true"><div class="qwen-markdown">Still generating</div></article>');
  const tracker = new CompletionTracker({ answers: [] }, { stableSeconds: 0, minWaitSeconds: 0, startedAt: 0 });
  assert.equal(act('inspect').stopping, true);
  assert.equal(tracker.update(act('inspect'), 1000).done, false);
  assert.equal(tracker.update(act('inspect'), 10000).done, false);
  dom.window.document.querySelector('article').removeAttribute('aria-busy');
  assert.equal(tracker.update(act('inspect'), 10001).done, true);
  dom.window.close();
});

test('adapter reports unconfirmed submission and never retries an ignored trusted click', async () => {
  let current, clicks = 0;
  const messages = [];
  const wc = {
    isDestroyed: () => false, stop() {},
    debugger: { attached: false, isAttached() { return this.attached; }, attach() { this.attached = true; }, detach() { this.attached = false; }, async sendCommand(method, params) { if (method === 'Input.insertText') current.dom.window.document.querySelector('textarea').value = params.text; if (params.type === 'mouseReleased') clicks++; } },
    async loadURL() { current = fixture('<textarea></textarea><button id="send">Send</button>'); },
    executeJavaScriptInIsolatedWorld(_id, sources) { return Promise.resolve(current.dom.window.eval(sources[0].code)); },
  };
  const adapter = new WebsiteAdapter(wc, { id: 'chatgpt', url: 'http://127.0.0.1:4567/', selectors }, () => {}, (event, fields) => messages.push({ event, ...fields }));
  await assert.rejects(adapter.run({ job_id: 'ignored', purpose: 'candidate', prompt: 'Should not repeat', timeout_seconds: 2.3, stable_seconds: 0.01, min_wait_seconds: 0 }), error => error.code === 'submission_unconfirmed');
  assert.equal(clicks, 1);
  assert.equal(adapter.active, false);
  assert.equal(wc.debugger.isAttached(), false);
  assert.equal(messages.filter(m => m.event === 'adapter.dispatch').length, 1);
  assert.equal(messages.filter(m => m.event === 'adapter.submission_accepted').length, 0);
  assert.ok(messages.find(m => m.event === 'adapter.failed' && m.dispatched === true && m.accepted === false));
  current.dom.window.close();
});

test('cancel between trusted mouse press and release prevents additional dispatch', async () => {
  const controller = new AbortController();
  const events = [];
  const wc = { debugger: { async sendCommand(_method, params) { events.push(params.type); controller.abort(); } } };
  await assert.rejects(dispatchTrustedInput(wc, { method: 'button', x: 1, y: 1 }, Date.now() + 1000, controller.signal), error => error.code === 'cancelled');
  assert.deepEqual(events, ['mousePressed']);
});
