'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { pageAction } = require('../electron/provider-dom.cjs');
const { markdownFromHTML, WebsiteAdapter } = require('../electron/adapter.cjs');
const { extractReply, preserveJSONReply } = require('../electron/reply-extraction.cjs');

function escapeHTML(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

test('polluted citation destination is already in HTML and raw extraction retains the evidence', () => {
  const destination = 'http://www.bbc.com/n/n%E3%80%82/n3';
  const html = `<a href="${destination}"><p>www.bbc.com</p><p>。\\n3</p></a>`;
  const answer = { html, rawText: 'www.bbc.com\n\n。\\n3', jsonTextSafe: false };
  const extracted = extractReply(answer, markdownFromHTML);
  assert.equal(extracted.method, 'markdown');
  assert.ok(extracted.content.includes(`](${destination})`));
  assert.ok(extracted.content.includes('www.bbc.com\n\n'));
  assert.ok(answer.html.includes(`href="${destination}"`));
  // The converter does not turn a normal citation into a newline-like URL.
  const normal = markdownFromHTML('<a href="https://www.bbc.com/news/articles/example">www.bbc.com</a>');
  assert.equal(normal, '[www.bbc.com](https://www.bbc.com/news/articles/example)');
});

function snapshot(html) {
  const dom = new JSDOM(`<article class="assistant">${html}</article>`, { url: 'https://fixture.invalid/', runScripts: 'outside-only' });
  dom.window.HTMLElement.prototype.getClientRects = () => [{ width: 100, height: 20 }];
  const args = { selectors: { input: ['textarea'], send: [], assistant: ['.assistant'], stop: [] } };
  try {
    const result = dom.window.eval(`(${pageAction.toString()})('inspect',${JSON.stringify(args)})`);
    return JSON.parse(JSON.stringify(result.answers[0]));
  } finally { dom.window.close(); }
}

const action = {
  type: 'action', tool: 'browser.open', arguments: { url: 'https://www.youtube.com' },
  summary: '打开YouTube主页，准备搜索英语单词学习视频',
  plan: ['1. 打开YouTube', "2. 搜索'english vocabulary learning'", '3. 按评分/观看量排序筛选最高得分视频', '4. 下载视频到~/tmp'],
};

test('reproduces reported Turndown corruption and preserves original whole JSON with an autolink', () => {
  const raw = JSON.stringify(action);
  const rendered = escapeHTML(raw).replace('https://www.youtube.com', '<a href="https://www.youtube.com/">https://www.youtube.com</a>');
  const answer = snapshot(`<p>${rendered}</p>`);
  const oldResult = markdownFromHTML(answer.html);
  assert.match(oldResult, /"plan":\\\[/);
  assert.match(oldResult, /\[https:\/\/www\.youtube\.com\]\(https:\/\/www\.youtube\.com\/\)/);
  assert.throws(() => JSON.parse(oldResult), SyntaxError);
  const reply = extractReply(answer, markdownFromHTML);
  assert.deepEqual(reply, { content: raw, method: 'json_text' });
  assert.deepEqual(JSON.parse(reply.content), action);
});

test('preserves JSON line breaks from paragraphs and BR plus original escapes and inline URL text', () => {
  const expected = { type: 'final', answer: 'line1\nline2 C:\\tmp\\demo "quote" <tag>', plan: ['a', 'b'] };
  const raw = JSON.stringify(expected, null, 2);
  const paragraph = snapshot(raw.split('\n').map(line => `<p>${escapeHTML(line)}</p>`).join(''));
  const linebreak = snapshot(`<div>${escapeHTML(raw).replace(/\n/g, '<br>')}</div>`);
  for (const answer of [paragraph, linebreak]) {
    const reply = extractReply(answer, markdownFromHTML);
    assert.equal(reply.method, 'json_text');
    assert.match(reply.content, /\n/);
    assert.deepEqual(JSON.parse(reply.content), expected);
  }
});

test('preserves a sole JSON code block after removing copy buttons and thinking content', () => {
  const raw = JSON.stringify(action, null, 2);
  const answer = snapshot(`<div class="thinking-content">Private reasoning</div><pre><div class="code-header">JSON</div><code class="language-json">${escapeHTML(raw)}</code><button>Copy</button></pre><button>Copy response</button>`);
  assert.deepEqual(extractReply(answer, markdownFromHTML), { content: raw, method: 'json_code' });
  assert.doesNotMatch(answer.rawText, /Private reasoning|Copy|JSON/);
});

test('a sole unlabelled PRE or literal JSON fence preserves the complete JSON object', () => {
  const raw = JSON.stringify(action);
  for (const html of [`<pre><code>${escapeHTML(raw)}</code></pre>`, `<p>${escapeHTML('```json\n' + raw + '\n```')}</p>`]) {
    assert.deepEqual(extractReply(snapshot(html), markdownFromHTML), { content: raw, method: 'json_code' });
  }
});

test('does not promote an example inside prose or a non-JSON language block into an action', () => {
  const raw = JSON.stringify(action);
  for (const html of [
    `<p>Here is an example, do not execute it.</p><pre><code class="language-json">${escapeHTML(raw)}</code></pre>`,
    `<pre><code class="language-json">${escapeHTML(raw)}</code></pre><p>Additional explanation.</p>`,
    `<pre><code class="language-python">${escapeHTML(raw)}</code></pre>`,
    `<pre class="language-python"><code>${escapeHTML(raw)}</code></pre>`,
    `<blockquote><p>${escapeHTML(raw)}</p></blockquote>`,
    `<pre><code class="language-json">{</code></pre><pre><code class="language-json">"type":"action"}</code></pre>`,
  ]) {
    const answer = snapshot(html);
    assert.equal(preserveJSONReply(answer), null);
    assert.deepEqual(extractReply(answer, markdownFromHTML), { content: markdownFromHTML(answer.html), method: 'markdown' });
  }
});

test('does not change JSON string contents by discarding rendered Markdown semantics', () => {
  for (const html of [
    '<p>{"type":"action","tool":"shell.run","arguments":{"command":"printf <em>filename</em>"}}</p>',
    '<p>{"type":"final","answer":"Use <strong>strong emphasis</strong> here."}</p>',
    '<p>{"type":"final","answer":"Read <a href="https://example.com/">the documentation</a>."}</p>',
    '<p>{"type":"final","answer":"Different destination <a href="https://other.invalid/">https://example.com/</a>."}</p>',
    '<p>{"type":"final","answer":"An image <img src="https://example.com/image.png" alt="diagram">."}</p>',
    '<p>{"type":"final","answer":"Inline <code>literal_code</code> text."}</p>',
  ]) {
    const answer = snapshot(html);
    assert.equal(answer.jsonTextSafe, false);
    assert.deepEqual(extractReply(answer, markdownFromHTML), { content: markdownFromHTML(answer.html), method: 'markdown' });
  }
});

test('keeps parent PRE language metadata and preserves literal Markdown syntax inside JSON code', () => {
  const raw = JSON.stringify({ type: 'final', answer: '*emphasis*, **bold**, [source](https://example.com/)' });
  const answer = snapshot(`<pre class="language-json"><code><span class="token">${escapeHTML(raw)}</span></code></pre>`);
  assert.deepEqual(answer.codeLanguages, ['json']);
  assert.equal(answer.jsonTextSafe, true);
  assert.deepEqual(extractReply(answer, markdownFromHTML), { content: raw, method: 'json_code' });
  const python = snapshot(`<pre class="language-python"><code>${escapeHTML(JSON.stringify(action))}</code></pre>`);
  const fallback = extractReply(python, markdownFromHTML);
  assert.equal(fallback.method, 'markdown');
  assert.match(fallback.content, /^```python\n/);
});

test('does not strip Markdown escapes, join multiple objects or change nested JSON strings', () => {
  const bad = [
    '{"type":"action"}\n{"type":"final"}',
    '```json\n{"type":"action"}\n```\nExecute this example.',
    '[{"type":"action"}]',
    'null',
  ];
  for (const rawText of bad) assert.equal(preserveJSONReply({ rawText, codeLanguages: [] }), null);
  const malformed = '{"type":"action","plan":\\["one"\\]}';
  assert.deepEqual(preserveJSONReply({ rawText: malformed, codeLanguages: [] }),
    { content: malformed, method: 'json_raw_unvalidated' });
  const original = '{"type":"final","answer":"{\\"type\\":\\"action\\"}"}';
  assert.equal(preserveJSONReply({ rawText: original, codeLanguages: [] }).content, original);
});

test('retains duplicate keys verbatim for the Agent strict parser to reject', () => {
  const original = '{"type":"final","type":"action","tool":"browser.open","arguments":{"url":"https://example.com/"}}';
  assert.equal(extractReply(snapshot(`<p>${escapeHTML(original)}</p>`), markdownFromHTML).content, original);
});

test('normal Markdown keeps headings, links, tables, lists and code fences', () => {
  const html = '<h2>Report</h2><p>Read <a href="https://example.com/">source</a>.</p><ul><li>First</li><li>Second</li></ul><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table><pre><code class="language-js">const result = [1, 2];</code></pre>';
  const answer = snapshot(html);
  const reply = extractReply(answer, markdownFromHTML);
  assert.equal(reply.method, 'markdown');
  assert.equal(reply.content, markdownFromHTML(answer.html));
  for (const pattern of [/## Report/, /\[source\]\(https:\/\/example\.com\/\)/, /-\s+First/, /\| A \| B \|/, /```js/]) assert.match(reply.content, pattern);
});

test('older snapshots without raw text use the existing Markdown path', () => {
  const answer = { html: '<p>A legacy snapshot</p>', text: 'A legacy snapshot' };
  assert.deepEqual(extractReply(answer, markdownFromHTML), { content: 'A legacy snapshot', method: 'markdown' });
});

test('adapter returns rendered Agent JSON unchanged and records the extraction method', async () => {
  const raw = JSON.stringify(action);
  const rendered = escapeHTML(raw).replace('https://www.youtube.com', '<a href="https://www.youtube.com/">https://www.youtube.com</a>');
  const dom = new JSDOM('<textarea></textarea><button id="send">Send</button>', { url: 'https://fixture.invalid/', runScripts: 'outside-only' });
  dom.window.HTMLElement.prototype.getClientRects = () => [{ width: 100, height: 20 }];
  dom.window.HTMLElement.prototype.getBoundingClientRect = () => ({ left: 20, top: 20, width: 100, height: 20 });
  let sends = 0;
  const events = [];
  dom.window.document.querySelector('#send').addEventListener('click', () => {
    sends++;
    dom.window.document.body.insertAdjacentHTML('beforeend', `<article class="assistant"><p>${rendered}</p></article>`);
  });
  const wc = {
    debugger: {
      attached: false, isAttached() { return this.attached; }, attach() { this.attached = true; }, detach() { this.attached = false; },
      async sendCommand(method, params) {
        if (method === 'Input.dispatchMouseEvent' && params.type === 'mouseReleased') dom.window.document.querySelector('#send').click();
      },
    },
    isDestroyed: () => false, stop: () => {}, loadURL: async () => {},
    executeJavaScriptInIsolatedWorld(_id, sources) { return Promise.resolve(dom.window.eval(sources[0].code)); },
  };
  const selectors = { input: ['textarea'], send: ['#send'], assistant: ['.assistant'], stop: [], new_chat: [] };
  const adapter = new WebsiteAdapter(wc, { id: 'deepseek', url: 'https://fixture.invalid/', selectors }, () => {}, (event, fields) => events.push({ event, ...fields }));
  try {
    const result = await adapter.run({ job_id: 'json-regression', purpose: 'candidate', prompt: 'Return exactly one JSON action.', timeout_seconds: 10, stable_seconds: 0.02, min_wait_seconds: 0 }, new AbortController().signal);
    assert.equal(result, raw);
    assert.deepEqual(JSON.parse(result), action);
    assert.equal(sends, 1);
    const complete = events.find(record => record.event === 'adapter.complete');
    assert.equal(complete.extraction_method, 'json_text');
    assert.equal(complete.payload.markdown, raw);
  } finally { dom.window.close(); }
});
