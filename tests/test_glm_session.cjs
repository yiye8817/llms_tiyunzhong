'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { JSDOM } = require('jsdom');
const { WebsiteAdapter, pageAction } = require('../electron/adapter.cjs');

// Local structural fixtures, not snapshots of a live Z.ai account.
const selectors = { input: ['#chat-input'], send: ['#send'], assistant: ['[data-role="assistant"]'], stop: [], new_chat: [] };
const model = '<button id="model-selector" aria-label="Select model" data-x="100" data-y="10">GLM-5.3-Flash</button>';
const newChat = '<button id="new-chat" aria-label="New chat" data-x="100" data-y="70">+</button>';
const composer = '<form><textarea id="chat-input" data-x="200" data-y="300"></textarea><button type="button" id="send" aria-label="Send Message" data-x="500" data-y="300">Send</button></form>';
const history = '<article data-role="user">OLD USER</article><article data-role="assistant">OLD ANSWER</article>';
function fixture(html, url = 'https://chat.z.ai/c/previous') {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  w.HTMLElement.prototype.getClientRects = function () { return this.isConnected ? [this.getBoundingClientRect()] : []; };
  w.HTMLElement.prototype.getBoundingClientRect = function () {
    return { left: Number(this.dataset.x || 20), top: Number(this.dataset.y || 20), width: 100, height: 30 };
  };
  w.document.elementFromPoint = (x, y) => [...w.document.querySelectorAll('button,a,textarea')].find(node => {
    const b = node.getBoundingClientRect();
    return !node.closest('[hidden]') && x >= b.left && x <= b.left + b.width && y >= b.top && y <= b.top + b.height;
  }) || w.document.body;
  w.document.hasFocus = () => true;
  const act = (action, extra = {}) => w.eval(`(${pageAction.toString()})(${JSON.stringify(action)},${JSON.stringify({
    id: 'glm-session-test', provider_id: 'glm', selectors, prompt: 'NEW TASK', deadline: Date.now() + 60000, ...extra,
  })})`);
  return { dom, act };
}
test('GLM session inspection reads selected flash model without editing or clicking', () => {
  const f = fixture(model + newChat + composer + history);
  try {
    const input = f.dom.window.document.querySelector('textarea'); input.value = 'KEEP DRAFT';
    let effects = 0;
    for (const node of f.dom.window.document.querySelectorAll('button,textarea')) {
      node.focus = () => effects++; node.scrollIntoView = () => effects++;
      node.addEventListener('click', () => effects++); node.addEventListener('input', () => effects++);
    }
    const s = f.act('glmSession');
    assert.equal(s.model.key, 'glm-5.3-flash'); assert.equal(s.model.label, 'GLM-5.3-Flash');
    assert.equal(s.answers, 1); assert.equal(s.userCount, 1); assert.equal(s.inputEmpty, false);
    assert.equal(s.newChat.id, 'new-chat'); assert.equal(s.rating.length, 0);
    assert.equal(effects, 0); assert.equal(input.value, 'KEEP DRAFT');
    assert.equal(f.dom.window.__fusionJob, undefined);
  } finally { f.dom.window.close(); }
});
test('GLM new-chat target uses semantic control with old/stale configured selectors', () => {
  const f = fixture(model + newChat + composer + history);
  try {
    let clicks = 0; f.dom.window.document.querySelector('#new-chat').addEventListener('click', () => clicks++);
    const t = f.act('glmNewChatTarget');
    assert.equal(t.ready, true); assert.equal(t.target.id, 'new-chat'); assert.equal(clicks, 0);
    f.dom.window.document.elementFromPoint = undefined;
    assert.equal(f.act('glmNewChatTarget').ready, false);
  } finally { f.dom.window.close(); }
});
test('GLM ignores menu options and model names inside old assistant answers', () => {
  const f = fixture(newChat + composer + '<div role="menu"><button>GLM-5.3-Flash</button></div>' +
    '<article data-role="assistant"><button id="model-selector">GLM-5.3-Flash</button></article>');
  try { assert.equal(f.act('glmSession').model, null); } finally { f.dom.window.close(); }
});
test('GLM native select reads only its selected option', () => {
  const f = fixture('<select id="model-selector"><option>GLM-5.3</option><option selected>GLM-5.3-Flash</option></select>' + newChat + composer);
  try { assert.equal(f.act('glmSession').model.key, 'glm-5.3-flash'); } finally { f.dom.window.close(); }
});
test('GLM never runs Qwen rating-dismiss actions', () => {
  const f = fixture(model + composer + '<div role="dialog" aria-label="Rate this response"><button aria-label="Close">Close</button></div>');
  try {
    assert.equal(f.act('glmSession').rating.length, 0);
    assert.equal(f.act('qwenDismissTarget').reason, 'unsupported_provider');
    assert.equal(f.act('qwenNewChatTarget').reason, 'unsupported_provider');
  } finally { f.dom.window.close(); }
});
function site(options = {}) {
  const wc = new EventEmitter(), commands = [], clicks = [];
  let f, loads = 0, turns = 0, chats = 0;
  const open = old => {
    f?.dom.window.close(); f = fixture(model + newChat + composer + (old ? history : ''));
    const w = f.dom.window, doc = w.document, input = doc.querySelector('textarea');
    input.value = options.draft || '';
    if (options.noNewChat) doc.querySelector('#new-chat').remove();
    doc.querySelector('#new-chat')?.addEventListener('click', () => {
      chats++;
      if (options.fullNavigation) {
        let prevented = false;
        wc.emit('will-navigate', { preventDefault() { prevented = true; } }, 'https://chat.z.ai/');
        assert.equal(prevented, true); return;
      }
      if (options.ignoreNewChat) return;
      w.history.pushState({}, '', `/c/new-${chats}`);
      if (options.routeOnly) return;
      doc.querySelectorAll('[data-role="assistant"],[data-role="user"]').forEach(node => node.remove());
      if (options.changeModel) doc.querySelector('#model-selector').textContent = 'GLM-5.3';
    });
    doc.querySelector('#send').addEventListener('click', () => {
      turns++;
      const user = doc.createElement('article'); user.dataset.role = 'user'; user.textContent = input.value;
      const answer = doc.createElement('article'); answer.dataset.role = 'assistant'; answer.textContent = `NEW ANSWER ${turns}`;
      doc.body.append(user, answer); input.value = '';
    });
  };
  if (!options.cold) open(options.history !== false);
  Object.assign(wc, {
    getURL: () => f?.dom.window.location.href || 'about:blank', isDestroyed: () => false, stop() {},
    async loadURL(url) { assert.equal(url, 'https://chat.z.ai/'); loads++; open(false); },
    async executeJavaScriptInIsolatedWorld(_world, sources) { return f.dom.window.eval(sources[0].code); },
    debugger: {
      attached: false, isAttached() { return this.attached; }, attach() { this.attached = true; }, detach() { this.attached = false; },
      async sendCommand(method, params) {
        commands.push({ method, ...params });
        const w = f.dom.window;
        if (method === 'Input.insertText') {
          const input = w.document.activeElement; assert.equal(input.id, 'chat-input'); assert.equal(input.value, '');
          input.value = params.text; input.dispatchEvent(new w.InputEvent('input', { bubbles: true, inputType: 'insertText', data: params.text }));
        } else if (method === 'Input.dispatchMouseEvent' && params.type === 'mouseReleased') {
          const control = w.document.elementFromPoint(params.x, params.y); clicks.push(control.id); control.click();
        }
      },
    },
  });
  const adapter = new WebsiteAdapter(wc, { id: 'glm', url: 'https://chat.z.ai/', selectors });
  return { wc, adapter, commands, clicks, get loads() { return loads; }, get document() { return f.dom.window.document; }, close() { f?.dom.window.close(); } };
}
const job = id => ({ job_id: id, purpose: 'candidate', prompt: `TASK ${id}`, timeout_seconds: 35, stable_seconds: 0.01, min_wait_seconds: 0 });
async function settle(t, promise) {
  let result; promise.then(value => { result = { value }; }, error => { result = { error }; });
  for (let elapsed = 0; !result && elapsed <= 60000; elapsed += 100) { await new Promise(resolve => setImmediate(resolve)); t.mock.timers.tick(100); }
  assert.ok(result); if (result.error) throw result.error; return result.value;
}
test('GLM two complete DOM/CDP jobs keep glm-5.3-flash without refreshing', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() }); const s = site();
  try {
    assert.equal(await settle(t, s.adapter.run(job('one'))), 'NEW ANSWER 1');
    assert.equal(await settle(t, s.adapter.run(job('two'))), 'NEW ANSWER 2');
    assert.equal(s.loads, 0); assert.equal(s.document.querySelector('#model-selector').textContent, 'GLM-5.3-Flash');
    assert.deepEqual(s.clicks, ['new-chat', 'send', 'new-chat', 'send']);
    assert.equal(s.commands.filter(c => c.method === 'Input.insertText').length, 2);
  } finally { s.close(); t.mock.timers.reset(); }
});
test('GLM cold page loads once and then uses SPA new-chat', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() }); const s = site({ cold: true });
  try {
    await settle(t, s.adapter.run(job('cold'))); await settle(t, s.adapter.run(job('warm')));
    assert.equal(s.loads, 1); assert.deepEqual(s.clicks, ['send', 'new-chat', 'send']);
  } finally { s.close(); t.mock.timers.reset(); }
});
for (const [name, options, code] of [
  ['draft', { draft: 'KEEP DRAFT' }, 'input_not_empty'],
  ['missing new-chat', { noNewChat: true }, 'glm_new_chat_required'],
  ['ignored new-chat', { ignoreNewChat: true }, 'glm_new_chat_unconfirmed'],
  ['only route changes', { routeOnly: true }, 'glm_new_chat_unconfirmed'],
  ['full navigation', { fullNavigation: true }, 'glm_new_chat_requires_reload'],
  ['model reset', { changeModel: true }, 'glm_model_changed'],
]) test(`GLM DOM/CDP ${name} stops before input without a reload fallback`, async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.now() }); const s = site(options);
  try {
    await assert.rejects(settle(t, s.adapter.run(job(name))), error => error.code === code);
    assert.equal(s.loads, 0); assert.equal(s.commands.some(c => c.method === 'Input.insertText'), false);
    assert.equal(s.wc.listenerCount('will-navigate'), 0); assert.equal(s.wc.listenerCount('will-redirect'), 0);
    assert.equal(s.adapter.active, false); assert.equal(s.wc.debugger.isAttached(), false);
    if (options.draft) assert.equal(s.document.querySelector('textarea').value, options.draft);
  } finally { s.close(); t.mock.timers.reset(); }
});
