'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { pageAction } = require('../electron/provider-dom.cjs');

// Structural fixtures: they validate provenance and one-gesture reservations,
// not compatibility with any particular deployed provider page revision.
const composer = '<form><textarea id="chat-input" data-x="10" data-y="300"></textarea><button id="send" aria-label="发送" data-x="300" data-y="300">发送</button></form>';
const selectors = { input: ['#chat-input'], send: ['#send'], assistant: ['[data-role="assistant"]'], stop: ['#stop'] };
const errorMarkup = '<div role="alert" id="error">服务器繁忙，请稍后再试<button id="retry" data-x="400" data-y="100">重试</button></div>';
const qwenBusyText = 'Oops! There was an issue connecting to Qwen3.8-Max. 目前服务访问量较大，请稍后再试。';
const qwenBusyIconMarkup = `<div role="alert" id="qwen-busy" data-x="90" data-y="100" data-width="356" data-height="88">${qwenBusyText}</div>
  <button id="qwen-busy-retry" data-x="90" data-y="210"><svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2-5"/></svg></button>`;
function fixture(history = '', url = 'https://chat.qwen.ai/c/current') {
  const dom = new JSDOM(`<main id="messages">${history}</main>${composer}`, { url, runScripts: 'outside-only' });
  const w = dom.window, doc = w.document;
  w.HTMLElement.prototype.getClientRects = function () { return [{ width: 80, height: 30 }]; };
  w.HTMLElement.prototype.getBoundingClientRect = function () { return { left: Number(this.dataset.x || 10), top: Number(this.dataset.y || 10), width: Number(this.dataset.width || 80), height: Number(this.dataset.height || 30) }; };
  doc.elementFromPoint = (x, y) => [...doc.querySelectorAll('button,textarea')].find(node => {
    const rect = node.getBoundingClientRect();
    return !node.closest('[hidden]') && x >= rect.left && x <= rect.left + rect.width && y >= rect.top && y <= rect.top + rect.height;
  }) || doc.body;
  Object.defineProperty(doc, 'visibilityState', { value: 'visible', configurable: true });
  doc.hasFocus = () => true;
  const act = (action, extra = {}) => w.eval(`(${pageAction.toString()})(${JSON.stringify(action)}, ${JSON.stringify({ id: 'job', provider_id: 'qwen', prompt: 'Current task', selectors, deadline: Date.now() + 60000, ...extra })})`);
  const prepare = () => { assert.equal(act('prepare').ready, true); assert.equal(act('claimSubmit').ready, true); };
  const accept = (answer = errorMarkup) => {
    doc.querySelector('textarea').value = '';
    doc.querySelector('#messages').insertAdjacentHTML('beforeend', `<article data-role="user">Current task</article><article data-role="assistant">${answer}</article>`);
  };
  return { dom, w, doc, act, prepare, accept, close: () => w.close() };
}
function withFixture(callback, history = '', url) {
  const f = fixture(history, url);
  try { callback(f); } finally { f.close(); }
}

test('current newly accepted failed turn has a single-use retry reservation and never DOM-clicks', () => withFixture(f => {
  f.prepare(); f.accept();
  let clicks = 0; f.doc.querySelector('#retry').onclick = () => clicks++;
  const inspected = f.act('recoveryInspect');
  assert.equal(inspected.contextValid, true); assert.equal(inspected.contextChanged, false);
  assert.equal(inspected.currentTurnAccepted, true);
  assert.equal(inspected.currentError, true); assert.equal(inspected.retryAvailable, true);
  const target = f.act('recoveryTarget');
  assert.equal(target.ready, true); assert.equal(target.target.id, 'retry');
  assert.equal(target.x, 440); assert.equal(target.y, 115);
  const claim = f.act('recoveryClaim', { nonce: target.nonce });
  assert.equal(claim.ready, true); assert.equal(claim.recoveryUsed, true);
  assert.equal(f.act('recoveryClaim', { nonce: target.nonce }).ready, false);
  assert.equal(f.act('recoveryTarget').ready, false); assert.equal(clicks, 0);
}));

test('Qwen3.8-Max current busy card binds its adjacent unlabeled circular-arrow button', () => withFixture(f => {
  f.prepare(); f.accept(qwenBusyIconMarkup);
  let clicks = 0; f.doc.querySelector('#qwen-busy-retry').onclick = () => clicks++;
  const inspected = f.act('recoveryInspect');
  assert.equal(inspected.currentTurnAccepted, true); assert.equal(inspected.currentError, true);
  assert.equal(inspected.retryAvailable, true); assert.equal(inspected.target.id, 'qwen-busy-retry');
  assert.equal(inspected.target.selector, 'qwen-current-turn-busy-adjacent-icon');
  const target = f.act('recoveryTarget');
  assert.equal(target.ready, true); assert.equal(target.x, 130); assert.equal(target.y, 225);
  assert.equal(f.act('recoveryClaim', { nonce: target.nonce }).ready, true);
  assert.equal(clicks, 0, 'isolated DOM code reserves coordinates but never invokes click()');
}));

test('Qwen busy card can bind a sibling retry footer inside one verified current-turn wrapper', () => withFixture(f => {
  f.prepare(); f.doc.querySelector('textarea').value = '';
  f.doc.querySelector('#messages').insertAdjacentHTML('beforeend', `<section id="current-turn">
    <article data-role="user">Current task</article>
    <article data-role="assistant"><div role="alert" id="qwen-busy" data-x="90" data-y="100" data-width="356" data-height="88">${qwenBusyText}</div></article>
    <button id="qwen-sibling-retry" data-x="90" data-y="210"><svg><path/></svg></button></section>`);
  const state = f.act('recoveryInspect');
  assert.equal(state.currentError, true); assert.equal(state.retryAvailable, true);
  assert.equal(state.target.id, 'qwen-sibling-retry');
}));

test('Qwen busy card accepts an icon-only sibling with an exact accessible retry name', () => withFixture(f => {
  f.prepare(); f.doc.querySelector('textarea').value = '';
  f.doc.querySelector('#messages').insertAdjacentHTML('beforeend', `<section>
    <article data-role="user">Current task</article>
    <article data-role="assistant"><div role="alert" id="qwen-busy" data-x="90" data-y="100" data-width="356" data-height="88">${qwenBusyText}</div></article>
    <footer><button id="qwen-retry-icon" aria-label="重试" data-x="90" data-y="210"><svg><path d="M4 12a8 8 0 1 0 2-5"/></svg></button></footer>
  </section>`);
  const state = f.act('recoveryInspect');
  assert.equal(state.currentTurnAccepted, true);
  assert.equal(state.retryAvailable, true);
  assert.equal(state.target.selector, 'qwen-current-turn-busy-adjacent-icon');
}));

test('Qwen busy recovery ignores unlabeled page, sidebar and pre-error icons outside the error action', () => withFixture(f => {
  f.prepare();
  f.doc.body.insertAdjacentHTML('afterbegin', '<header><button id="page-refresh"><svg><path/></svg></button></header><aside><button id="sidebar-new"><svg><path/></svg></button></aside>');
  f.accept(`<button id="scroll-before" data-x="90" data-y="50"><svg><path/></svg></button>
    <div role="alert" id="qwen-busy" data-x="90" data-y="100" data-width="356" data-height="88">${qwenBusyText}</div>`);
  const state = f.act('recoveryInspect');
  assert.equal(state.currentError, true); assert.equal(state.retryAvailable, false);
  assert.equal(state.reason, 'recovery_target_missing');
}));

test('Qwen busy recovery treats multiple adjacent unlabeled icons as ambiguous', () => withFixture(f => {
  f.prepare(); f.accept(`${qwenBusyIconMarkup}<button id="other-icon" data-x="180" data-y="210"><svg><path/></svg></button>`);
  const state = f.act('recoveryInspect');
  assert.equal(state.currentError, true); assert.equal(state.retryAvailable, false);
  assert.equal(state.reason, 'recovery_target_ambiguous');
}));

for (const [description, attributes] of [
  ['oversized', 'data-x="90" data-y="210" data-width="180" data-height="120"'],
  ['far below', 'data-x="90" data-y="500"'],
  ['far beside', 'data-x="700" data-y="210"'],
]) {
  test(`Qwen busy recovery rejects a ${description} SVG control`, () => withFixture(f => {
    f.prepare(); f.accept(`<div role="alert" id="qwen-busy" data-x="90" data-y="100" data-width="356" data-height="88">${qwenBusyText}</div>
      <button id="unrelated-icon" ${attributes}><svg><path/></svg></button>`);
    const state = f.act('recoveryInspect');
    assert.equal(state.currentError, true); assert.equal(state.retryAvailable, false);
  }));
}

test('an ordinary Qwen icon action without the exact current busy error is never auto-retried', () => withFixture(f => {
  f.prepare(); f.accept('<p>Successful answer</p><button id="ordinary-regenerate" data-x="90" data-y="210"><svg><path/></svg></button>');
  const state = f.act('recoveryInspect');
  assert.equal(state.currentError, false); assert.equal(state.retryAvailable, false);
}));

test('a Qwen busy icon in an old turn cannot recover the newly accepted turn', () => withFixture(f => {
  f.prepare(); f.accept('<p>New successful answer</p>');
  const state = f.act('recoveryInspect');
  assert.equal(state.currentError, false); assert.equal(state.retryAvailable, false);
}, `<article data-role="user">Old task</article><article data-role="assistant">${qwenBusyIconMarkup}</article>`));

test('unaccepted original input may be watched manually without offering retry', () => withFixture(f => {
  f.prepare();
  const inspected = f.act('recoveryInspect');
  assert.equal(inspected.contextValid, true); assert.equal(inspected.contextChanged, false);
  assert.equal(inspected.currentTurnAccepted, false);
  assert.equal(inspected.reason, 'recovery_waiting_acceptance');
  assert.equal(inspected.currentError, false); assert.equal(f.act('recoveryTarget').ready, false);
}));

test('cleared input without a recognized new user is unknown rather than changed', () => withFixture(f => {
  f.prepare(); f.doc.querySelector('textarea').value = '';
  const inspected = f.act('recoveryInspect');
  assert.equal(inspected.contextValid, false); assert.equal(inspected.contextChanged, false);
  assert.equal(inspected.currentTurnAccepted, false);
  assert.equal(inspected.reason, 'recovery_acceptance_unknown');
}));

test('a new explicit error without a recognized user wrapper is blocked but never grants retry authority', () => withFixture(f => {
  f.prepare(); f.doc.querySelector('textarea').value = '';
  f.doc.querySelector('#messages').insertAdjacentHTML('beforeend', `<article data-role="assistant">${qwenBusyIconMarkup}</article>`);
  const inspected = f.act('recoveryInspect');
  assert.equal(inspected.currentTurnAccepted, false); assert.equal(inspected.currentError, true);
  assert.equal(inspected.contextValid, false); assert.equal(inspected.retryAvailable, false);
  assert.equal(inspected.reason, 'recovery_current_error_unbound');
  assert.equal(f.act('recoveryTarget').ready, false);
}));

test('an unmarked new assistant only blocks the exact Qwen busy signature, not quoted generic error prose', () => withFixture(f => {
  f.prepare(); f.doc.querySelector('textarea').value = '';
  f.doc.querySelector('#messages').insertAdjacentHTML('beforeend', `<article data-role="assistant">${qwenBusyText}</article>`);
  assert.equal(f.act('recoveryInspect').currentError, true);
  f.doc.querySelector('[data-role="assistant"]').textContent = 'Documentation example: Server error';
  const quoted = f.act('recoveryInspect');
  assert.equal(quoted.currentError, false); assert.equal(quoted.retryAvailable, false);
}));

for (const [label, mutate, reason] of [
  ['new user', f => f.doc.querySelector('#messages').insertAdjacentHTML('beforeend', '<article data-role="user">Different task</article>'), 'recovery_user_history_changed'],
  ['edited latest user', f => { f.doc.querySelector('[data-role="user"]').textContent = 'Changed task'; }, 'recovery_user_changed'],
  ['new input draft', f => { f.doc.querySelector('textarea').value = 'A different draft'; }, 'recovery_input_changed'],
  ['different conversation', f => f.w.history.pushState({}, '', '/c/different'), 'recovery_conversation_changed'],
]) {
  test(`manual recovery rejects a confirmed ${label} change`, () => withFixture(f => {
    f.prepare(); f.accept(); assert.equal(f.act('recoveryInspect').contextValid, true);
    mutate(f);
    const inspected = f.act('recoveryInspect');
    assert.equal(inspected.contextValid, false); assert.equal(inspected.contextChanged, true);
    assert.equal(inspected.reason, reason); assert.equal(f.act('recoveryTarget').ready, false);
  }));
}

test('homepage tolerates pre-acceptance route settling, then rejects changing again', () => withFixture(f => {
  f.prepare();
  // Qwen may briefly bounce between the launch route and multiple SPA routes
  // before painting the optimistic user bubble after Send.
  for (const route of ['/c/new', '/', '/c/accepted']) {
    f.w.history.pushState({}, '', route);
    f.doc.querySelector('textarea').value = '';
    assert.equal(f.act('recoveryInspect').contextChanged, false);
  }
  f.accept(); assert.equal(f.act('recoveryInspect').contextValid, true);
  f.w.history.pushState({}, '', '/c/another');
  assert.equal(f.act('recoveryInspect').contextChanged, true);
}, '', 'https://chat.qwen.ai/'));

test('a matching historical user prompt is not sufficient to retry its old error', () => withFixture(f => {
  f.prepare();
  const state = f.act('recoveryInspect');
  assert.equal(state.lastUserMatchesPrompt, true); assert.equal(state.currentError, false);
  assert.equal(state.retryAvailable, false);
}, `<article data-role="user">Current task</article><article data-role="assistant">${errorMarkup}</article>`));

test('historical retry remains excluded when a new answer succeeds', () => withFixture(f => {
  f.prepare(); f.accept('New successful answer');
  const state = f.act('recoveryInspect');
  assert.equal(state.currentError, false); assert.equal(state.retryAvailable, false);
}, `<article data-role="user">Old task</article><article data-role="assistant">${errorMarkup}</article>`));

test('re-rendered user history must preserve its exact text and count', () => withFixture(f => {
  f.prepare();
  f.doc.querySelector('#messages').innerHTML = '<article data-role="user">Old task</article><article data-role="assistant">Old answer</article>';
  f.accept(); assert.equal(f.act('recoveryInspect').retryAvailable, true);
  f.doc.querySelector('[data-role="user"]').textContent = 'Unrelated history';
  assert.equal(f.act('recoveryInspect').contextChanged, true);
}, '<article data-role="user">Old task</article><article data-role="assistant">Old answer</article>'));

for (const label of ['Send', '发送', 'Stop generating', '停止', 'Like', 'Dislike', 'Regenerate all answers', 'Retry payment']) {
  test(`does not treat ${label} as a current-error retry control`, () => withFixture(f => {
    f.prepare(); f.accept(errorMarkup.replace('>重试</button>', `>${label}</button>`));
    const state = f.act('recoveryInspect');
    assert.equal(state.currentError, true); assert.equal(state.retryAvailable, false);
  }));
}

for (const label of ['Retry', 'Try again', 'Regenerate response', '重新生成回答', '重新尝试']) {
  test(`recognizes the explicit ${label} error-recovery name`, () => withFixture(f => {
    f.prepare(); f.accept(errorMarkup.replace('>重试</button>', `>${label}</button>`));
    assert.equal(f.act('recoveryInspect').retryAvailable, true);
  }));
}

for (const [label, mutate] of [
  ['disabled', f => { f.doc.querySelector('#retry').disabled = true; }],
  ['obscured', f => { f.doc.elementFromPoint = () => f.doc.querySelector('#send'); }],
  ['missing hit test', f => { f.doc.elementFromPoint = undefined; }],
  ['hidden', f => { f.doc.querySelector('#retry').hidden = true; }],
  ['outside viewport', f => { f.doc.querySelector('#retry').dataset.x = '2000'; }],
  ['pointer-events none', f => { f.doc.querySelector('#retry').style.pointerEvents = 'none'; }],
  ['active generation', f => { f.doc.querySelector('[data-role="assistant"]').setAttribute('aria-busy', 'true'); }],
]) {
  test(`recovery never reserves a ${label} target`, () => withFixture(f => {
    f.prepare(); f.accept(); mutate(f);
    assert.equal(f.act('recoveryTarget').ready, false);
  }));
}

test('the retry target must remain the same DOM node until claim', () => withFixture(f => {
  f.prepare(); f.accept(); const target = f.act('recoveryTarget');
  const old = f.doc.querySelector('#retry'); old.replaceWith(old.cloneNode(true));
  const claim = f.act('recoveryClaim', { nonce: target.nonce });
  assert.equal(claim.ready, false); assert.equal(claim.reason, 'recovery_reservation_changed');
}));

test('a superseded or wrong nonce does not authorize any recovery click', () => withFixture(f => {
  f.prepare(); f.accept(); const first = f.act('recoveryTarget'), second = f.act('recoveryTarget');
  assert.notEqual(first.nonce, second.nonce);
  assert.equal(f.act('recoveryClaim', { nonce: first.nonce }).ready, false);
  assert.equal(f.act('recoveryClaim', { nonce: second.nonce }).ready, false);
}));

test('expired, cancelled, unrelated and unsubmitted jobs do not offer recovery', () => withFixture(f => {
  f.act('prepare');
  assert.equal(f.act('recoveryTarget').ready, false);
  f.act('claimSubmit'); f.accept();
  assert.equal(f.act('recoveryTarget', { id: 'another-job' }).ready, false);
  assert.equal(f.act('recoveryTarget', { deadline: 0 }).ready, false);
  f.act('abort'); assert.equal(f.act('recoveryTarget').ready, false);
}));

test('inspection works unfocused but interactive reservations require actual focus', () => withFixture(f => {
  f.prepare(); f.accept(); f.doc.hasFocus = () => false;
  assert.equal(f.act('recoveryInspect', { require_interactive: true }).retryAvailable, true);
  assert.equal(f.act('recoveryTarget', { require_interactive: true }).ready, false);
  assert.equal(f.act('recoveryTarget', { require_interactive: false }).ready, true);
}));

test('explicit error text without a retry button remains a manual recovery case', () => withFixture(f => {
  f.prepare(); f.accept('<div role="alert">Network error</div>');
  const state = f.act('recoveryInspect');
  assert.equal(state.currentError, true); assert.equal(state.retryAvailable, false);
}));

test('a retry control without error evidence does not trigger regeneration', () => withFixture(f => {
  f.prepare(); f.accept('<p>Successful answer</p><button data-x="400" data-y="100">Regenerate response</button>');
  assert.equal(f.act('recoveryInspect').currentError, false);
}));

test('two recovery controls in one error are ambiguous and require manual choice', () => withFixture(f => {
  f.prepare(); f.accept(errorMarkup.replace('</div>', '<button data-x="500" data-y="100">Try again</button></div>'));
  const state = f.act('recoveryInspect');
  assert.equal(state.reason, 'recovery_target_ambiguous'); assert.equal(state.retryAvailable, false);
}));

test('a local one-turn wrapper can associate an adjacent explicit error with this user', () => withFixture(f => {
  f.prepare(); f.doc.querySelector('textarea').value = '';
  f.doc.querySelector('#messages').insertAdjacentHTML('beforeend', `<section><article data-role="user">Current task</article>${errorMarkup}</section>`);
  assert.equal(f.act('recoveryInspect').retryAvailable, true);
}));

test('a global or quoted alert does not provide a current-turn retry target', () => withFixture(f => {
  f.prepare(); f.accept('Successful answer');
  f.doc.body.insertAdjacentHTML('beforeend', errorMarkup);
  assert.equal(f.act('recoveryInspect').currentError, false);
  f.doc.querySelector('[data-role="assistant"]').innerHTML = `<pre>${errorMarkup}</pre>`;
  assert.equal(f.act('recoveryInspect').currentError, false);
}));

test('a failed claim due to an unavailable target also consumes its nonce', () => withFixture(f => {
  f.prepare(); f.accept(); const target = f.act('recoveryTarget');
  f.doc.querySelector('#retry').disabled = true;
  assert.equal(f.act('recoveryClaim', { nonce: target.nonce }).ready, false);
  f.doc.querySelector('#retry').disabled = false;
  assert.equal(f.act('recoveryClaim', { nonce: target.nonce }).ready, false);
}));

test('recovery keeps the original prepared prompt bound to its job', () => withFixture(f => {
  f.prepare(); f.accept();
  const state = f.act('recoveryInspect', { prompt: 'Different task' });
  assert.equal(state.contextChanged, true); assert.equal(state.reason, 'recovery_prompt_changed');
}));

for (const message of ['An error has occurred.', 'Service unavailable', 'Server error', '服务器开小差了，请重试']) {
  test(`explicit current-turn failure recognizes ${message}`, () => withFixture(f => {
    f.prepare(); f.accept(errorMarkup.replace('服务器繁忙，请稍后再试', message));
    assert.equal(f.act('recoveryInspect').currentError, true);
    assert.equal(f.act('recoveryInspect').retryAvailable, true);
  }));
}

test('copy and edit controls inside the current user turn do not contaminate prompt matching', () => withFixture(f => {
  f.prepare(); f.accept();
  f.doc.querySelector('[data-role="user"]').insertAdjacentHTML('beforeend', '<button>复制</button><span role="button">编辑</span><span aria-hidden="true">装饰</span>');
  const inspected = f.act('recoveryInspect');
  assert.equal(inspected.currentTurnAccepted, true); assert.equal(inspected.lastUserMatchesPrompt, true);
  assert.equal(inspected.retryAvailable, true);
}));

test('a changed historical copy control label does not count as edited user task content', () => withFixture(f => {
  f.prepare(); f.accept();
  f.doc.querySelector('#old-copy').textContent = 'Copied';
  const inspected = f.act('recoveryInspect');
  assert.equal(inspected.contextChanged, false); assert.equal(inspected.currentTurnAccepted, true);
}, '<article data-role="user">Earlier task<button id="old-copy">Copy</button></article><article data-role="assistant">Earlier answer</article>'));
