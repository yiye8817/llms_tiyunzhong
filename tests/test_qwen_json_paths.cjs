'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { pageAction } = require('../electron/provider-dom.cjs');
const {
  extractJSONFromHTML, extractJSONFromText, extractJSONFromCandidates, structuredReplyState,
} = require('../electron/reply-extraction.cjs');

test('Qwen JSON extraction accepts the three bounded representations only when complete', () => {
  const value = { ok: true, items: [1, 2], url: 'https://example.test/a?x=1' };
  const source = JSON.stringify(value);
  const html = `<article><p>${source.replaceAll('&', '&amp;').replaceAll('"', '&quot;')}</p></article>`;
  assert.deepEqual(extractJSONFromHTML(html)?.value, value);
  assert.equal(extractJSONFromHTML(html)?.method, 'html_to_json');
  assert.deepEqual(extractJSONFromText(`\n\`\`\`json\n${source}\n\`\`\``,'copy_markdown_to_json')?.value, value);
  assert.deepEqual(extractJSONFromCandidates(['not json', source], 'dom_to_json')?.value, value);
  assert.equal(extractJSONFromText(`${source}\n说明`), null);
  assert.equal(extractJSONFromText('{"x":1,}'), null);
  assert.equal(structuredReplyState({ rawText: '[{"x":', jsonTextSafe: true, codeLanguages: [] }).incomplete, true);
  assert.equal(structuredReplyState({ rawText: '[{"x":1}]', jsonTextSafe: true, codeLanguages: [] }).incomplete, false);
});

test('Qwen DOM actions return latest answer candidates and a safe copy target', () => {
  const dom = new JSDOM('<article data-role="assistant"><p>{&quot;from&quot;:&quot;dom&quot;}</p>' +
    '<div class="toolbar"><button id="copy" aria-label="复制 Markdown">复制 Markdown</button></div></article>' +
    '<textarea id="chat-input"></textarea><button id="send">发送</button>', {
    url: 'https://chat.qwen.ai/', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const window = dom.window;
  window.HTMLElement.prototype.getClientRects = function () { return [{ width: 120, height: 24 }]; };
  window.HTMLElement.prototype.getBoundingClientRect = function () { return { left: 10, top: 10, width: 120, height: 24 }; };
  window.document.hasFocus = () => true;
  const call = action => window.eval(`(${pageAction.toString()})(${JSON.stringify(action)},${JSON.stringify({
    provider_id: 'qwen', prompt: 'return JSON', deadline: Date.now() + 10000,
    selectors: { input: ['#chat-input'], send: ['#send'], assistant: ['[data-role="assistant"]'], stop: [] },
  })})`);
  try {
    const domResult = call('qwenDOMJSON');
    assert.equal(domResult.ready, true);
    assert.ok(domResult.candidates.includes('{"from":"dom"}'));
    const copy = call('qwenCopyMarkdownTarget');
    assert.equal(copy.ready, true);
    assert.equal(copy.target.id, 'copy');
  } finally { dom.window.close(); }
});
