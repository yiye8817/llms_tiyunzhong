'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { pageAction } = require('../electron/adapter.cjs');
const selectors = require('../config.example.json').providers.find(row => row.id === 'chatgpt').selectors;

// Structural regression fixtures, not a snapshot of the user's signed-in page.
function page(html, prompt = 'first\nsecond', overrides = {}) {
  const dom = new JSDOM(html, { url: 'https://chatgpt.invalid/', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window, doc = w.document;
  w.HTMLElement.prototype.getClientRects = function () { return this.isConnected ? [this.getBoundingClientRect()] : []; };
  w.HTMLElement.prototype.getBoundingClientRect = () => ({ left: 20, top: 20, width: 100, height: 30 });
  doc.hasFocus = () => true;
  doc.elementFromPoint = () => doc.querySelector('#composer-submit-button,button[data-testid="send-button"],button');
  const args = { provider_id: 'chatgpt', selectors, prompt, id: 'chatgpt-send', input_transport: 'cdp', require_interactive: true, deadline: Date.now() + 30000, ...overrides };
  const act = (action, changed = {}) => w.eval(`(${pageAction.toString()})(${JSON.stringify(action)},${JSON.stringify({ ...args, ...changed })})`);
  const input = doc.querySelector('.ProseMirror,#prompt-textarea');
  const fill = content => { if ('value' in input) input.value = content; else input.innerHTML = content; };
  return { dom, w, doc, input, act, fill, close: () => w.close() };
}

test('ChatGPT prepares the visible ProseMirror editor instead of a hidden legacy textarea', () => {
  const p = page('<textarea data-id="root" hidden></textarea><form><div id="prompt-textarea" class="ProseMirror" contenteditable="true"><p><br class="ProseMirror-trailingBreak"></p></div><button id="composer-submit-button" aria-label="Send prompt"></button></form>', 'question', { selectors: { ...selectors, input: ['textarea', "textarea[data-id='root']"] } });
  try {
    const result = p.act('prepare');
    assert.equal(result.ready, true);
    assert.equal(result.inputMethod, 'cdp_insert_text');
    assert.equal(result.inputFocused, true);
    assert.equal(p.doc.activeElement, p.input);
    assert.equal(p.doc.querySelector('textarea').value, '');
    assert.equal(p.input.textContent, '', 'Prepare itself must not synthesize a DOM edit');
  } finally { p.close(); }
});

test('ChatGPT compares ProseMirror paragraph and soft breaks literally without rendered paragraph spacing', () => {
  const p = page('<form><div id="prompt-textarea" class="ProseMirror" contenteditable="true"></div><button id="composer-submit-button" aria-label="Send prompt"></button></form>', 'first\n\nsecond\nthird\n  indentation');
  try {
    p.act('prepare');
    p.fill('<p>first</p><p><br class="ProseMirror-trailingBreak"></p><p>second<br>third</p><p>  indentation</p>');
    Object.defineProperty(p.input, 'innerText', { value: 'first\n\n\n\nsecond\nthird\n\n  indentation' });
    assert.equal(p.act('canSubmit').ready, true);
    assert.equal(p.act('claimSubmit').ready, true);
    assert.throws(() => p.act('claimSubmit'), /already submitted/);
  } finally { p.close(); }
});

test('ChatGPT does not collapse missing newlines or indentation to force input equality', () => {
  const p = page('<form><div id="prompt-textarea" class="ProseMirror" contenteditable="true"></div><button id="composer-submit-button" aria-label="Send prompt"></button></form>', 'first\n\n  second');
  try {
    p.act('prepare');
    for (const wrong of ['<p>first</p><p>  second</p>', '<p>first</p><p></p><p>second</p>']) {
      p.fill(wrong);
      assert.equal(p.act('claimSubmit').reason, 'input_changed');
      assert.equal(p.w.__fusionJob.submitted, false);
    }
  } finally { p.close(); }
});

test('ChatGPT never treats voice, dictation or stop variants of the composer control as Send', () => {
  for (const label of ['Start voice mode', 'Dictate', 'Stop generating', '开启语音', '停止生成']) {
    const p = page('<form><textarea id="prompt-textarea"></textarea><button id="composer-submit-button" aria-label="Send prompt"></button></form>', 'question');
    try {
      p.act('prepare'); p.fill('question'); p.doc.querySelector('button').setAttribute('aria-label', label);
      assert.equal(p.act('claimSubmit').reason, 'send_action_conflict', label);
      assert.equal(p.w.__fusionJob.submitted, false);
      assert.equal(p.act('diagnoseSend').sendActionConflict, true);
    } finally { p.close(); }
  }
});

test('ChatGPT reevaluates a control changing from voice to enabled Send without a speculative click', () => {
  const p = page('<form><textarea id="prompt-textarea"></textarea><button id="composer-submit-button" aria-label="Start voice mode"></button></form>', 'question');
  try {
    p.act('prepare'); p.fill('question');
    assert.equal(p.act('canSubmit').reason, 'send_action_conflict');
    const send = p.doc.querySelector('button');
    send.setAttribute('aria-label', 'Send prompt'); send.disabled = true;
    assert.equal(p.act('canSubmit').reason, 'send_disabled');
    send.disabled = false;
    assert.equal(p.act('claimSubmit').ready, true);
  } finally { p.close(); }
});

test('ChatGPT scopes Send to its composer and refuses an unrelated form control or blind Enter', () => {
  const p = page('<button id="composer-submit-button" aria-label="Send prompt"></button><form><textarea id="prompt-textarea"></textarea><button type="button" aria-label="Send prompt" data-testid="send-button"></button></form>', 'question');
  try {
    p.act('prepare'); p.fill('question');
    p.doc.elementFromPoint = () => p.doc.querySelector('form button');
    assert.equal(p.act('claimSubmit').sendTarget.id, '');
    assert.equal(p.act('diagnoseSend').sendTarget.selector, 'button[data-testid=\'send-button\']');
  } finally { p.close(); }
  const empty = page('<textarea id="prompt-textarea"></textarea>', 'question');
  try {
    empty.act('prepare'); empty.fill('question');
    assert.equal(empty.act('claimSubmit').reason, 'send_missing');
    assert.equal(empty.w.__fusionJob.submitted, false);
  } finally { empty.close(); }
});

test('ChatGPT rechecks focus, actual click target, and editor replacement before one reservation', () => {
  const p = page('<form><textarea id="prompt-textarea"></textarea><button id="composer-submit-button" aria-label="Send prompt"></button><div id="overlay"></div></form>', 'question');
  try {
    p.act('prepare'); p.fill('question');
    p.doc.hasFocus = () => false;
    assert.equal(p.act('claimSubmit').reason, 'page_not_interactive');
    p.doc.hasFocus = () => true;
    p.doc.elementFromPoint = () => p.doc.querySelector('#overlay');
    assert.equal(p.act('claimSubmit').reason, 'send_obscured');
    p.doc.elementFromPoint = undefined;
    assert.equal(p.act('claimSubmit').reason, 'send_hit_test_unavailable');
    p.doc.elementFromPoint = () => p.doc.querySelector('button');
    const replacement = p.input.cloneNode(); replacement.value = ''; p.input.replaceWith(replacement);
    assert.equal(p.act('claimSubmit').reason, 'input_changed');
    replacement.value = 'question';
    assert.equal(p.act('claimSubmit').ready, true);
  } finally { p.close(); }
});
