'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { pageAction, markdownFromHTML } = require('../electron/adapter.cjs');
const config = require('../config.example.json');

// Structural fixtures exercise safe provider fallbacks. They are intentionally
// not represented as snapshots of either live website.
const staleSelectors = { input: ['.old-composer'], send: ['.old-send'], assistant: ['.removed-answer'], stop: [], new_chat: [] };

const markup = {
  glm: {
    url: 'https://chat.z.ai/',
    composer: '<form id="chat-input-container"><textarea id="chat-input"></textarea><button class="sendMessageButton" type="button" aria-label="Send Message">发送</button></form>',
    user: prompt => `<div class="user-message">${prompt}</div>`,
    answer: '<article data-message-role="assistant" class="assistant-message"><h2>GLM 结果</h2><p>完整回答。</p><pre><code class="language-python">print(42)\n</code></pre><table><tr><th>项</th><th>值</th></tr><tr><td>状态</td><td>完成</td></tr></table></article>',
    stop: '<button data-testid="stop-button" aria-label="停止生成"></button>',
    error: '<div data-testid="generation-error">服务器繁忙，请稍后再试。<button data-testid="retry-button"><svg></svg></button></div>',
    heading: '## GLM 结果',
  },
  kimi: {
    url: 'https://www.kimi.com/',
    composer: '<form><textarea data-testid="chat-input"></textarea><button class="send-button" type="button" aria-label="发送消息">发送</button></form>',
    user: prompt => `<div class="chat-content-item-user">${prompt}</div>`,
    answer: '<article class="chat-content-item-assistant"><h2>Kimi 结果</h2><p>完整回答。</p><pre><code class="language-python">print(42)\n</code></pre><table><tr><th>项</th><th>值</th></tr><tr><td>状态</td><td>完成</td></tr></table></article>',
    stop: '<button data-testid="stop-button" aria-label="停止生成"></button>',
    error: '<div class="kimi-message-error">Something went wrong. <button data-testid="regenerate-button"><svg></svg></button></div>',
    heading: '## Kimi 结果',
  },
};

function fixture(provider, body = markup[provider].composer, prompt = '第一行\n第二行') {
  const dom = new JSDOM(body, { url: markup[provider].url, runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const { document } = window;
  window.HTMLElement.prototype.getClientRects = function () { return this.isConnected ? [this.getBoundingClientRect()] : []; };
  window.HTMLElement.prototype.getBoundingClientRect = function () { return { left: 20, top: 20, width: 120, height: 32 }; };
  document.hasFocus = () => true;
  document.elementFromPoint = () => document.querySelector('[data-testid="retry-button"],[data-testid="regenerate-button"]') ||
    document.querySelector('[data-testid="send-button"],.send-button,.sendMessageButton');
  const args = { id: `${provider}-fixture`, provider_id: provider, selectors: staleSelectors, prompt,
    deadline: Date.now() + 30000, require_interactive: true };
  const act = (action, changed = {}) => window.eval(`(${pageAction.toString()})(${JSON.stringify(action)},${JSON.stringify({ ...args, ...changed })})`);
  return { dom, window, document, args, act, close: () => window.close() };
}

test('default catalog exposes disabled GLM and Kimi without changing the enabled pair', () => {
  assert.equal(config.schema_version, 3);
  assert.deepEqual(config.providers.filter(provider => provider.enabled).map(provider => provider.id), ['chatgpt', 'deepseek']);
  for (const id of ['glm', 'kimi']) {
    const provider = config.providers.find(item => item.id === id);
    assert.ok(provider);
    assert.equal(provider.enabled, false);
    for (const key of ['input', 'send', 'assistant', 'stop', 'new_chat']) assert.ok(provider.selectors[key].length, `${id}.${key}`);
  }
  assert.equal(config.providers.find(item => item.id === 'glm').url, 'https://chat.z.ai/');
  assert.equal(config.providers.find(item => item.id === 'kimi').url, 'https://www.kimi.com/');
});

for (const provider of ['glm', 'kimi']) {
  test(`${provider} production CDP preparation focuses without a synthetic edit and accepts one native insertion`, () => {
    const page = fixture(provider);
    try {
      const input = page.document.querySelector('textarea');
      let inputEvents = 0;
      input.addEventListener('input', () => inputEvents++);
      const prepared = page.act('prepare', { input_transport: 'cdp' });
      assert.equal(prepared.ready, true);
      assert.equal(prepared.inputMethod, 'cdp_insert_text');
      assert.equal(prepared.inputFocused, true);
      assert.equal(input.value, '');
      assert.equal(inputEvents, 0);

      // This one assignment/event represents Chromium Input.insertText in the
      // isolated fixture; pageAction itself must not perform it twice.
      input.value = page.args.prompt;
      input.dispatchEvent(new page.window.InputEvent('input', { bubbles: true, inputType: 'insertText', data: page.args.prompt }));
      assert.equal(inputEvents, 1);
      assert.equal(page.act('canSubmit').ready, true);
      assert.equal(page.act('claimSubmit').ready, true);
      assert.throws(() => page.act('claimSubmit'), /already submitted/);
    } finally { page.close(); }
  });

  test(`${provider} refuses a blind Enter fallback when no Send control is identified`, () => {
    const page = fixture(provider, '<textarea data-testid="chat-input"></textarea>');
    try {
      assert.equal(page.act('prepare', { input_transport: 'cdp' }).ready, true);
      const input = page.document.querySelector('textarea');
      input.value = page.args.prompt;
      input.dispatchEvent(new page.window.InputEvent('input', { bubbles: true, inputType: 'insertText', data: page.args.prompt }));
      const target = page.act('canSubmit');
      assert.equal(target.ready, false);
      assert.equal(target.reason, 'send_missing');
    } finally { page.close(); }
  });

  test(`${provider} fallback prepares exactly one composer, reserves Send, and captures structured Markdown`, () => {
    const page = fixture(provider, `<textarea id="unrelated-search"></textarea>${markup[provider].composer}<div class="old-answer">OLD ANSWER</div>`);
    try {
      const prepared = page.act('prepare');
      assert.equal(prepared.ready, true);
      assert.equal(prepared.inputSource, 'provider');
      const input = page.document.querySelector('form textarea');
      assert.equal(input.value, page.args.prompt);
      assert.equal(page.document.querySelector('#unrelated-search').value, '');
      const target = page.act('canSubmit');
      assert.equal(target.ready, true);
      assert.equal(target.method, 'button');
      assert.equal(target.sendSource, 'provider');
      assert.equal(page.act('claimSubmit').ready, true);
      assert.throws(() => page.act('claimSubmit'), /already submitted/);

      // Represent the one trusted native click's page result without invoking a
      // model service. The extraction path must ignore the user's own turn.
      input.value = '';
      page.document.body.insertAdjacentHTML('beforeend', markup[provider].user(page.args.prompt));
      page.document.body.insertAdjacentHTML('beforeend', markup[provider].answer);
      const snapshot = page.act('inspect');
      assert.equal(snapshot.userCount, 1);
      assert.equal(snapshot.lastUserMatchesPrompt, true);
      assert.equal(snapshot.answers.length, 1);
      const markdown = markdownFromHTML(snapshot.answers[0].html);
      assert.ok(markdown.startsWith(markup[provider].heading));
      assert.match(markdown, /```python\nprint\(42\)\n```/);
      assert.match(markdown, /\| 项 \| 值 \|/);
      assert.doesNotMatch(markdown, /第一行|第二行|OLD ANSWER/);
    } finally { page.close(); }
  });

  test(`${provider} stop and current-turn retry candidates stay bound to recognized controls`, () => {
    const page = fixture(provider);
    try {
      page.act('prepare');
      assert.equal(page.act('claimSubmit').ready, true);
      const input = page.document.querySelector('textarea');
      input.value = '';
      const turn = page.document.createElement('section');
      turn.className = 'current-turn';
      turn.innerHTML = markup[provider].user(page.args.prompt) + markup[provider].error;
      page.document.body.append(turn);
      page.document.body.insertAdjacentHTML('beforeend', markup[provider].stop);
      assert.equal(page.act('inspect').stopping, true);
      page.document.querySelector('[data-testid="stop-button"]').remove();

      const report = page.act('recoveryInspect');
      assert.equal(report.contextValid, true);
      assert.equal(report.currentTurnAccepted, true);
      assert.equal(report.currentError, true);
      assert.equal(report.retryAvailable, true);
      const target = page.act('recoveryTarget');
      assert.equal(target.ready, true);
      assert.equal(target.method, 'button');
      assert.match(target.target.selector, /retry-button|regenerate-button/);
      assert.equal(page.act('recoveryClaim', { nonce: target.nonce }).ready, true);
      assert.equal(page.act('recoveryInspect').recoveryUsed, true);
    } finally { page.close(); }
  });
}
