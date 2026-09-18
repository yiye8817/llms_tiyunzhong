'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { JSDOM } = require('jsdom');
const { WebsiteAdapter, pageAction } = require('../electron/adapter.cjs');

// Behavioral fixtures for session preservation and narrowly identified rating
// panels. They do not claim to reproduce a particular version of Qwen's site.
const selectors = {
  input: ['#chat-input'], send: ['button.send-button'],
  assistant: ['[data-role="assistant"]'], stop: ['#stop-response-button'],
  new_chat: ['a[href="/"]'],
};
const composer = '<form><textarea id="chat-input" data-x="200" data-y="300"></textarea><button type="button" class="send-button" data-x="500" data-y="300" aria-label="Send">Send</button></form>';
const model = '<button data-testid="model-selector" data-x="100" data-y="10">Qwen3.8-Max</button>';
const newChat = '<button id="new-chat" aria-label="New chat" data-x="100" data-y="70">+</button>';
const oldTurns = '<article data-role="user">Old task</article><article data-role="assistant">OLD ANSWER</article>';
const rating = '<div id="rating-panel" role="dialog" aria-label="Rate this response"><button id="rate-star" aria-label="5 stars" data-x="200" data-y="550">★★★★★</button><button id="rate-submit" data-x="400" data-y="550">Submit rating</button><button id="rate-close" aria-label="Close" data-x="650" data-y="550">×</button></div>';

function fixture(html, url = 'https://chat.qwen.ai/c/previous') {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only' });
  const w = dom.window;
  w.HTMLElement.prototype.getClientRects = function () { return [{ width: 100, height: 30 }]; };
  w.HTMLElement.prototype.getBoundingClientRect = function () {
    return { left: Number(this.dataset.x || 20), top: Number(this.dataset.y || 20), width: 100, height: 30 };
  };
  w.document.elementFromPoint = (x, y) => {
    const panel = w.document.querySelector('[role="dialog"]:not([hidden])');
    const scope = panel || w.document;
    return [...scope.querySelectorAll('button,[role="button"],a,textarea')].find(node => {
      const box = node.getBoundingClientRect();
      return !node.closest('[hidden]') && x >= box.left && x <= box.left + box.width && y >= box.top && y <= box.top + box.height;
    }) || panel || w.document.body;
  };
  Object.defineProperty(w.document, 'visibilityState', { value: 'visible', configurable: true });
  Object.defineProperty(w.document, 'hidden', { value: false, configurable: true });
  w.document.hasFocus = () => true;
  const act = (action, extra = {}) => w.eval(`(${pageAction.toString()})(${JSON.stringify(action)},${JSON.stringify({ id: 'qwen-session-test', provider_id: 'qwen', selectors, prompt: 'New task', deadline: Date.now() + 60000, ...extra })})`);
  return { dom, act };
}

test('Qwen session inspection reports selected model and existing turns without changing the page', () => {
  const { dom, act } = fixture(model + newChat + composer + oldTurns);
  try {
    const document = dom.window.document;
    const input = document.querySelector('textarea');
    input.value = 'KEEP THIS UNSENT DRAFT';
    let effects = 0;
    for (const control of document.querySelectorAll('button,textarea')) {
      control.focus = () => effects++;
      control.scrollIntoView = () => effects++;
      control.addEventListener('click', () => effects++);
      control.addEventListener('input', () => effects++);
    }
    const state = act('qwenSession');
    assert.equal(state.conversation, '/c/previous');
    assert.equal(state.answers, 1);
    assert.equal(state.userCount, 1);
    assert.equal(state.inputEmpty, false);
    assert.equal(state.inputReady, true);
    assert.equal(state.model.label, 'Qwen3.8-Max');
    assert.equal(state.newChat.id, 'new-chat');
    assert.equal(state.rating.length, 0);
    assert.equal(effects, 0);
    assert.equal(input.value, 'KEEP THIS UNSENT DRAFT');
    assert.equal(dom.window.__fusionJob, undefined);
  } finally { dom.window.close(); }
});

test('Qwen new-chat target names the visible control but never clicks it inside DOM evaluation', () => {
  const { dom, act } = fixture(model + newChat + composer + oldTurns);
  try {
    let clicks = 0;
    dom.window.document.querySelector('#new-chat').addEventListener('click', () => clicks++);
    const target = act('qwenNewChatTarget');
    assert.equal(target.ready, true);
    assert.equal(target.method, 'button');
    assert.equal(target.target.id, 'new-chat');
    assert.equal(target.x, 150);
    assert.equal(target.y, 85);
    assert.equal(clicks, 0);
  } finally { dom.window.close(); }
});

test('Qwen reads the selected model text even when its control has a generic accessible label', () => {
  const { dom, act } = fixture(model.replace('data-testid="model-selector"', 'data-testid="model-selector" aria-label="Select model"') + newChat + composer);
  try {
    assert.equal(act('qwenSession').model.label, 'Qwen3.8-Max');
  } finally { dom.window.close(); }
});

for (const [name, dialog, expectedId] of [
  ['English rating dialog', rating, 'rate-close'],
  ['Chinese feedback panel with skip', '<section role="dialog" aria-labelledby="feedback-title"><h2 id="feedback-title">请为本次回答评分</h2><button id="skip-rating" data-x="650" data-y="550">跳过</button><button id="submit-feedback" data-x="400" data-y="550">提交反馈</button></section>', 'skip-rating'],
]) {
  test(`Qwen recognizes ${name} and only targets its explicit dismissal`, () => {
    const { dom, act } = fixture(model + newChat + composer + dialog);
    try {
      let clicks = 0;
      for (const node of dom.window.document.querySelectorAll('button')) node.addEventListener('click', () => clicks++);
      const state = act('qwenSession');
      assert.equal(state.rating.length, 1);
      assert.equal(state.rating[0].closeTarget.id, expectedId);
      const target = act('qwenDismissTarget');
      assert.equal(target.ready, true);
      assert.equal(target.method, 'button');
      assert.equal(target.target.id, expectedId);
      assert.equal(clicks, 0);
    } finally { dom.window.close(); }
  });
}

test('Qwen does not use stars or a feedback submission as a dismissal', () => {
  const { dom, act } = fixture(composer + rating.replace('<button id="rate-close" aria-label="Close" data-x="650" data-y="550">×</button>', ''));
  try {
    const state = act('qwenSession');
    assert.equal(state.rating.length, 1);
    assert.equal(state.rating[0].closeTarget, null);
    assert.equal(act('qwenDismissTarget').ready, false);
  } finally { dom.window.close(); }
});

test('Qwen keeps the rating dialog as its dismissal scope when a nested rating-stars widget also matches', () => {
  const nestedRating = rating.replace('id="rate-star"', 'id="rate-star" data-testid="rating-stars"');
  const { dom, act } = fixture(composer + nestedRating);
  try {
    const state = act('qwenSession');
    assert.equal(state.rating.length, 1);
    assert.match(state.rating[0].identity, /rating-panel/);
    assert.equal(state.rating[0].closeTarget.id, 'rate-close');
    const target = act('qwenDismissTarget');
    assert.equal(target.ready, true);
    assert.equal(target.target.id, 'rate-close');
  } finally { dom.window.close(); }
});

test('Qwen does not follow an ordinary Close link inside a rating panel', () => {
  const closeLink = '<a id="external-close" href="https://example.org/feedback" data-x="650" data-y="550">Close</a>';
  const { dom, act } = fixture(composer + rating.replace('<button id="rate-close" aria-label="Close" data-x="650" data-y="550">×</button>', closeLink));
  try {
    assert.equal(act('qwenSession').rating.length, 1);
    assert.equal(act('qwenSession').rating[0].closeTarget, null);
    assert.equal(act('qwenDismissTarget').ready, false);
  } finally { dom.window.close(); }
});

for (const [name, dialog] of [
  ['unknown account confirmation', '<div role="dialog" aria-label="Delete account"><button data-x="650" data-y="550" aria-label="Close">×</button><button>Confirm</button></div>'],
  ['hidden rating panel', rating.replace('id="rating-panel"', 'id="rating-panel" hidden')],
  ['rating text inside an assistant answer', '<article data-role="assistant"><p>Please rate this response</p><button aria-label="Close" data-x="650" data-y="550">×</button></article>'],
]) {
  test(`Qwen never dismisses ${name}`, () => {
    const { dom, act } = fixture(composer + dialog);
    try {
      assert.equal(act('qwenSession').rating.length, 0);
      assert.equal(act('qwenDismissTarget').ready, false);
    } finally { dom.window.close(); }
  });
}

test('Qwen refuses a dismissal when the close control is disabled or obscured', () => {
  for (const unavailable of ['disabled', 'obscured']) {
    const { dom, act } = fixture(composer + rating);
    try {
      if (unavailable === 'disabled') dom.window.document.querySelector('#rate-close').disabled = true;
      else dom.window.document.elementFromPoint = () => dom.window.document.querySelector('#rate-star');
      assert.equal(act('qwenDismissTarget').ready, false, unavailable);
    } finally { dom.window.close(); }
  }
});

test('Qwen records a strongly evidenced human send in the prepared job context', () => {
  const { dom, act } = fixture(composer, 'https://chat.qwen.ai/c/manual');
  try {
    assert.equal(act('prepare', { fresh_session_confirmed: true, input_transport: 'cdp' }).ready, true);
    const input = dom.window.document.querySelector('textarea');
    input.value = 'New task';
    const user = dom.window.document.createElement('article');
    user.dataset.role = 'user'; user.textContent = 'New task';
    dom.window.document.body.append(user); input.value = '';
    const claim = act('confirmManualSubmit', { manual_evidence: 'new_user_turn' });
    assert.equal(claim.ready, true);
    assert.equal(claim.currentTurnAccepted, true);
    assert.equal(act('confirmManualSubmit', { manual_evidence: 'new_user_turn' }).ready, false, 'The human send can only be claimed once');
  } finally { dom.window.close(); }
});

test('Qwen requires hit-test evidence before giving a new-chat or rating-close target', () => {
  const { dom, act } = fixture(model + newChat + composer + rating);
  try {
    dom.window.document.elementFromPoint = undefined;
    assert.equal(act('qwenNewChatTarget').ready, false);
    assert.equal(act('qwenDismissTarget').ready, false);
  } finally { dom.window.close(); }
});

function simulatedSite({ alreadyLoaded = true, initialHistory = true, showRatingAfterNewChat = false, ignoreNewChat = false, ignoreDismiss = false, initialDraft = '', fullNavigation = false, routeOnly = false, changeModel = false, fullNavigationOnDismiss = false, manualSendOnPrompt = false } = {}) {
  const wc = new EventEmitter();
  const commands = [], clicks = [], logs = [], statuses = [];
  let current = null, loads = 0, turn = 0, chats = 0, pressed = null, manualSends = 0, manualSendScheduled = false;
  const open = history => {
    current?.dom.window.close();
    const newChatControl = fullNavigation ? '<a id="new-chat" href="/" data-x="100" data-y="70">Home</a>' : newChat;
    current = fixture(model + newChatControl + composer + (history ? oldTurns : ''));
    const w = current.dom.window, document = w.document;
    const input = document.querySelector('textarea');
    input.value = initialDraft;
    const installDismiss = () => document.querySelector('#rate-close')?.addEventListener('click', () => {
      if (fullNavigationOnDismiss) {
        let prevented = false;
        wc.emit('will-navigate', { preventDefault() { prevented = true; } }, 'https://chat.qwen.ai/');
        assert.equal(prevented, true, 'A rating dismissal must not navigate away or reset the selected model');
        return;
      }
      if (!ignoreDismiss) document.querySelector('#rating-panel').remove();
    });
    document.querySelector('#new-chat').addEventListener('click', event => {
      chats++;
      if (ignoreNewChat) return;
      if (fullNavigation) {
        event.preventDefault(); // The simulated WC below owns navigation instead of JSDOM.
        let prevented = false;
        wc.emit('will-navigate', { preventDefault() { prevented = true; } }, 'https://chat.qwen.ai/');
        assert.equal(prevented, true, 'A new-chat document reload must be stopped before resetting the selected model');
        return;
      }
      if (routeOnly) { w.history.pushState({}, '', `/c/new-${chats}`); return; }
      document.querySelectorAll('[data-role="assistant"],[data-role="user"]').forEach(node => node.remove());
      input.value = '';
      w.history.pushState({}, '', `/c/new-${chats}`);
      if (changeModel) document.querySelector('[data-testid="model-selector"]').textContent = 'Qwen Default';
      if (showRatingAfterNewChat) {
        document.body.insertAdjacentHTML('beforeend', rating);
        installDismiss();
      }
    });
    document.querySelector('.send-button').addEventListener('click', () => {
      assert.equal(document.querySelector('#rating-panel'), null, 'The rating panel must be confirmed absent before send');
      turn++;
      const user = document.createElement('article');
      user.dataset.role = 'user'; user.textContent = input.value;
      const answer = document.createElement('article');
      answer.dataset.role = 'assistant'; answer.textContent = `NEW ANSWER ${turn}`;
      document.body.append(user, answer);
      input.value = '';
    });
  };
  if (alreadyLoaded) open(initialHistory);
  Object.assign(wc, {
    isDestroyed: () => false,
    getURL: () => current?.dom.window.location.href || 'about:blank',
    stop() {},
    focus() { assert.fail('Session maintenance must not move OS window focus'); },
    async loadURL(url) {
      assert.equal(url, 'https://chat.qwen.ai/');
      loads++;
      open(false);
    },
    executeJavaScriptInIsolatedWorld(_id, sources) {
      return Promise.resolve(current.dom.window.eval(sources[0].code));
    },
    debugger: {
      attached: false,
      isAttached() { return this.attached; },
      attach() { assert.equal(this.attached, false); this.attached = true; },
      detach() { assert.equal(this.attached, true); this.attached = false; },
      async sendCommand(method, params) {
        commands.push({ method, ...params });
        assert.equal(this.attached, true);
        const w = current.dom.window;
        if (method === 'Input.insertText') {
          const input = w.document.activeElement;
          assert.equal(input.id, 'chat-input');
          assert.equal(input.value, '', 'No old draft may be overwritten');
          input.value = params.text;
          input.dispatchEvent(new w.InputEvent('input', { bubbles: true, inputType: 'insertText', data: params.text }));
        } else if (method === 'Input.dispatchMouseEvent' && params.type === 'mousePressed') {
          pressed = { x: params.x, y: params.y };
        } else if (method === 'Input.dispatchMouseEvent' && params.type === 'mouseReleased') {
          assert.deepEqual(pressed, { x: params.x, y: params.y });
          pressed = null;
          const hit = w.document.elementFromPoint(params.x, params.y);
          assert.ok(hit?.matches('button,a,[role="button"]'), 'Every trusted gesture must hit its identified control');
          clicks.push(hit.id || hit.className);
          hit.click();
        }
      },
    },
  });
  const status = (state, message) => {
    statuses.push({ state, message });
    if (state === 'manual_retry_required' && manualSendOnPrompt && !manualSendScheduled) {
      manualSendScheduled = true;
      setTimeout(() => {
        current.dom.window.document.querySelector('#rating-panel')?.remove();
        current.dom.window.document.querySelector('.send-button').click();
        manualSends++;
      }, 250);
    }
  };
  const adapter = new WebsiteAdapter(wc, { id: 'qwen', url: 'https://chat.qwen.ai/', selectors }, status, (event, fields) => logs.push({ event, ...fields }));
  return {
    wc, adapter, logs, statuses, clicks, commands,
    get loads() { return loads; },
    get manualSends() { return manualSends; },
    get document() { return current.dom.window.document; },
    close() { current?.dom.window.close(); },
  };
}

const job = id => ({ job_id: id, purpose: 'candidate', prompt: `TASK ${id}`, timeout_seconds: 45, stable_seconds: 0.01, min_wait_seconds: 0 });

async function settle(t, promise, maximumMs = 60000) {
  let result;
  promise.then(value => { result = { value }; }, error => { result = { error }; });
  for (let elapsed = 0; !result && elapsed <= maximumMs; elapsed += 100) {
    await new Promise(resolve => setImmediate(resolve));
    t.mock.timers.tick(100);
  }
  assert.ok(result, 'The operation must finish within its bounded deadline');
  if (result.error) throw result.error;
  return result.value;
}

test('two Qwen jobs preserve the selected model and reuse the live page with one new chat per old session', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const site = simulatedSite();
  try {
    assert.equal(await settle(t, site.adapter.run(job('first'))), 'NEW ANSWER 1');
    assert.equal(await settle(t, site.adapter.run(job('second'))), 'NEW ANSWER 2');
    assert.equal(site.loads, 0);
    assert.equal(site.document.querySelector('[data-testid="model-selector"]').textContent, 'Qwen3.8-Max');
    assert.deepEqual(site.clicks, ['new-chat', 'send-button', 'new-chat', 'send-button']);
    assert.equal(site.commands.filter(row => row.method === 'Input.insertText').length, 2);
    assert.equal(site.wc.debugger.isAttached(), false);
    assert.equal(site.adapter.active, false);
  } finally { site.close(); t.mock.timers.reset(); }
});

test('a never-loaded Qwen page navigates once and subsequent calls reuse it', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const site = simulatedSite({ alreadyLoaded: false, initialHistory: false });
  try {
    assert.equal(await settle(t, site.adapter.run(job('cold'))), 'NEW ANSWER 1');
    assert.equal(await settle(t, site.adapter.run(job('warm'))), 'NEW ANSWER 2');
    assert.equal(site.loads, 1);
    assert.deepEqual(site.clicks, ['send-button', 'new-chat', 'send-button']);
  } finally { site.close(); t.mock.timers.reset(); }
});

test('a rating dialog shown after creating a Qwen chat is dismissed and confirmed before one send', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const site = simulatedSite({ showRatingAfterNewChat: true });
  try {
    assert.equal(await settle(t, site.adapter.run(job('rating'))), 'NEW ANSWER 1');
    assert.deepEqual(site.clicks, ['new-chat', 'rate-close', 'send-button']);
    assert.equal(site.document.querySelector('#rating-panel'), null);
    assert.equal(site.loads, 0);
    assert.equal(site.commands.filter(row => row.method === 'Input.insertText').length, 1);
  } finally { site.close(); t.mock.timers.reset(); }
});

test('an ignored Qwen new-chat click fails without reloading or submitting into the previous chat', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const site = simulatedSite({ ignoreNewChat: true });
  try {
    await assert.rejects(settle(t, site.adapter.run(job('ignored-chat'))), error => error.code === 'qwen_new_chat_unconfirmed');
    assert.deepEqual(site.clicks, ['new-chat']);
    assert.equal(site.loads, 0);
    assert.equal(site.commands.some(row => row.method === 'Input.insertText'), false);
    assert.equal(site.document.querySelector('[data-role="assistant"]').textContent, 'OLD ANSWER');
    assert.equal(site.wc.debugger.isAttached(), false);
  } finally { site.close(); t.mock.timers.reset(); }
});

test('an ignored Qwen rating dismissal prompts for manual send and never repeats the automatic click', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const site = simulatedSite({ showRatingAfterNewChat: true, ignoreDismiss: true });
  try {
    await assert.rejects(settle(t, site.adapter.run({ ...job('ignored-dismiss'), qwen_manual_retry_wait_seconds: 2 })), error => error.code === 'qwen_manual_send_timeout');
    assert.deepEqual(site.clicks, ['new-chat', 'rate-close']);
    assert.equal(site.loads, 0);
    assert.equal(site.commands.filter(row => row.method === 'Input.insertText').length, 1);
    assert.ok(site.document.querySelector('#rating-panel'));
    assert.ok(site.statuses.some(row => row.state === 'manual_retry_required' && /2 秒/.test(row.message)));
    assert.ok(site.logs.some(row => row.event === 'adapter.qwen_manual_send_wait_started'));
    assert.equal(site.wc.debugger.isAttached(), false);
  } finally { site.close(); t.mock.timers.reset(); }
});

test('Qwen monitors a human send after the rating panel blocks automation and returns the answer', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const site = simulatedSite({ showRatingAfterNewChat: true, ignoreDismiss: true, manualSendOnPrompt: true });
  try {
    assert.equal(await settle(t, site.adapter.run(job('manual-rating-send'))), 'NEW ANSWER 1');
    assert.deepEqual(site.clicks, ['new-chat', 'rate-close']);
    assert.equal(site.manualSends, 1);
    assert.equal(site.commands.filter(row => row.method === 'Input.insertText').length, 1);
    assert.equal(site.document.querySelector('#rating-panel'), null);
    assert.ok(site.statuses.some(row => row.state === 'manual_retry_required' && /20 秒/.test(row.message)));
    assert.ok(site.logs.some(row => row.event === 'adapter.qwen_manual_send_detected'));
    assert.ok(site.logs.some(row => row.event === 'adapter.complete'));
    assert.equal(site.wc.debugger.isAttached(), false);
  } finally { site.close(); t.mock.timers.reset(); }
});

test('a Qwen draft in an otherwise empty session is preserved without clearing or sending it', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const site = simulatedSite({ initialHistory: false, initialDraft: 'MY UNSENT DRAFT' });
  try {
    await assert.rejects(settle(t, site.adapter.run(job('draft'))), error => error.code === 'input_not_empty');
    assert.equal(site.document.querySelector('textarea').value, 'MY UNSENT DRAFT');
    assert.equal(site.loads, 0);
    assert.equal(site.clicks.length, 0);
    assert.equal(site.commands.some(row => row.method === 'Input.insertText'), false);
  } finally { site.close(); t.mock.timers.reset(); }
});

test('Qwen falls back to manual send when a rating-close control attempts full navigation', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
  const site = simulatedSite({ showRatingAfterNewChat: true, fullNavigationOnDismiss: true });
  try {
    await assert.rejects(settle(t, site.adapter.run({ ...job('dismiss-navigation'), qwen_manual_retry_wait_seconds: 2 })), error => error.code === 'qwen_manual_send_timeout');
    assert.deepEqual(site.clicks, ['new-chat', 'rate-close']);
    assert.equal(site.loads, 0);
    assert.equal(site.commands.filter(row => row.method === 'Input.insertText').length, 1);
    assert.ok(site.statuses.some(row => row.state === 'manual_retry_required'));
    assert.equal(site.wc.listenerCount('will-navigate'), 0);
    assert.equal(site.wc.debugger.isAttached(), false);
    assert.equal(site.adapter.active, false);
  } finally { site.close(); t.mock.timers.reset(); }
});

for (const [name, option, errorCode] of [
  ['a full document reload', 'fullNavigation', 'qwen_new_chat_requires_reload'],
  ['a changed route with old turns still present', 'routeOnly', 'qwen_new_chat_unconfirmed'],
  ['a reset model selection', 'changeModel', 'qwen_model_changed'],
]) {
  test(`Qwen stops before sending when its new-chat control causes ${name}`, async t => {
    t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() });
    const site = simulatedSite({ [option]: true });
    try {
      await assert.rejects(settle(t, site.adapter.run(job(option))), error => error.code === errorCode);
      assert.deepEqual(site.clicks, ['new-chat']);
      assert.equal(site.loads, 0);
      assert.equal(site.commands.some(row => row.method === 'Input.insertText'), false);
      assert.equal(site.wc.listenerCount('will-navigate'), 0, 'Temporary navigation protection must be removed after the failed job');
      assert.equal(site.wc.debugger.isAttached(), false);
      assert.equal(site.adapter.active, false);
    } finally { site.close(); t.mock.timers.reset(); }
  });
}
