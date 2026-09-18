'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { pageAction } = require('../electron/adapter.cjs');

function page(html, prompt = 'first\n\nsecond\nthird') {
  const dom = new JSDOM(html, { url: 'https://grok.com/', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  w.HTMLElement.prototype.getClientRects = function () { return this.isConnected ? [{ width: 100, height: 30 }] : []; };
  w.HTMLElement.prototype.getBoundingClientRect = () => ({ left: 20, top: 20, width: 100, height: 30 });
  w.document.hasFocus = () => true;
  w.document.elementFromPoint = () => w.document.querySelector('button');
  const args = {
    provider_id: 'grok',
    selectors: {
      input: ['div.ProseMirror[contenteditable="true"][role="textbox"]'],
      send: ['button[aria-label="提交"]'], assistant: [], stop: [],
    },
    prompt, id: 'grok-send', input_transport: 'cdp', require_interactive: true,
    deadline: Date.now() + 30000,
  };
  const act = action => w.eval(`(${pageAction.toString()})(${JSON.stringify(action)},${JSON.stringify(args)})`);
  return { dom, doc: w.document, act, input: w.document.querySelector('[contenteditable]') };
}

test('Grok accepts structured editor line breaks and recognizes Chinese Submit control', () => {
  const p = page('<div class="ProseMirror" contenteditable="true" role="textbox"></div><button aria-label="提交"></button>');
  try {
    assert.equal(p.act('prepare').ready, true);
    p.input.innerHTML = '<div>first</div><div><br></div><div>second<br>third</div>';
    p.input.focus();
    const result = p.act('claimSubmit');
    assert.equal(result.ready, true);
    assert.equal(result.method, 'button');
    assert.equal(result.inputLength, 19);
    assert.equal(result.sendTarget.selector, 'button[aria-label="提交"]');
  } finally { p.dom.window.close(); }
});

test('Grok recognizes current data-testid send controls', () => {
  const p = page('<div class="ProseMirror" contenteditable="true" role="textbox"></div><button data-testid="grokSendButton"></button>');
  try {
    p.act('prepare');
    p.input.innerHTML = '<div>first</div><div><br></div><div>second<br>third</div>';
    p.input.focus();
    assert.equal(p.act('claimSubmit').ready, true);
  } finally { p.dom.window.close(); }
});

test('Grok first conversation route changes remain bound to the accepted user turn', () => {
  const prompt = '请只回复：route-ok';
  const dom = new JSDOM('<div class="ProseMirror" contenteditable="true" role="textbox"></div><button aria-label="Send"></button><main id="messages"></main>', {
    url: 'https://grok.com/', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  const w = dom.window;
  w.HTMLElement.prototype.getClientRects = function () { return this.isConnected ? [{ width: 100, height: 30 }] : []; };
  w.HTMLElement.prototype.getBoundingClientRect = () => ({ left: 20, top: 20, width: 100, height: 30 });
  w.document.hasFocus = () => true;
  w.document.elementFromPoint = () => w.document.querySelector('button');
  const args = {
    provider_id: 'grok',
    selectors: { input: ['div.ProseMirror[contenteditable="true"][role="textbox"]'], send: ['button[aria-label="Send"]'], assistant: [], stop: [] },
    prompt, id: 'grok-route', input_transport: 'dom', require_interactive: true, deadline: Date.now() + 30000,
  };
  const act = action => w.eval(`(${pageAction.toString()})(${JSON.stringify(action)},${JSON.stringify(args)})`);
  try {
    assert.equal(act('prepare').ready, true);
    const input = w.document.querySelector('[contenteditable]');
    input.textContent = prompt;
    input.dispatchEvent(new w.InputEvent('input', { bubbles: true }));
    assert.equal(act('claimSubmit').ready, true);
    input.textContent = '';
    w.document.querySelector('#messages').innerHTML = '<article data-role="user">请只回复：route-ok</article>';
    assert.equal(act('recoveryInspect').contextChanged, false);
    const user = w.document.querySelector('[data-role="user"]');
    user.innerHTML = '<span>请只回复：</span><span>route-ok</span><button hidden>复制</button>';
    w.history.pushState({}, '', '/c/server-assigned');
    const firstRoute = act('recoveryInspect');
    assert.equal(firstRoute.currentTurnAccepted, true);
    assert.equal(firstRoute.contextChanged, false);
    const replacement = w.document.createElement('article');
    replacement.dataset.role = 'user';
    replacement.textContent = 'rendered user turn';
    user.replaceWith(replacement);
    const replacedNode = act('recoveryInspect');
    assert.equal(replacedNode.currentTurnAccepted, true);
    assert.equal(replacedNode.lastUserMatchesPrompt, true);
    assert.equal(replacedNode.contextChanged, false);
    w.history.pushState({}, '', '/c/router-refresh');
    assert.equal(act('recoveryInspect').contextChanged, false);
  } finally { dom.window.close(); }
});
