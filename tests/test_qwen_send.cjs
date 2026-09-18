'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { WebsiteAdapter, pageAction } = require('../electron/adapter.cjs');

// Deliberately keep the user's older selectors: provider fallbacks must work
// without requiring the user to delete or recreate the saved configuration.
const legacySelectors = {
  input: ['#chat-input', 'textarea', 'div[contenteditable="true"][role="textbox"]'],
  send: ['button#send-message-button', 'button[aria-label="Send Message"]', 'button[aria-label="Send"]', 'button[aria-label="发送"]'],
  assistant: ['[data-role="assistant"] .qwen-markdown', '.chat-assistant .qwen-markdown', '.qwen-markdown'],
  stop: ['#stop-response-button'],
};

function fixture(html, overrides = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:4567/', runScripts: 'outside-only' });
  const w = dom.window;
  w.HTMLElement.prototype.getClientRects = function () { return [{ width: 100, height: 30 }]; };
  w.HTMLElement.prototype.getBoundingClientRect = function () {
    return { left: Number(this.dataset.left || 20), top: 20, width: 100, height: 30 };
  };
  const args = {
    id: 'qwen-regression', provider_id: 'qwen', prompt: 'Explain the code\nprint(1)',
    deadline: Date.now() + 10000, selectors: legacySelectors, ...overrides,
  };
  const act = (action, changed = {}) => w.eval(`(${pageAction.toString()})(${JSON.stringify(action)},${JSON.stringify({ ...args, ...changed })})`);
  return { dom, args, act };
}

test('Qwen accepts complete CRLF input after textarea normalizes line endings', () => {
  const { dom, act } = fixture('<textarea id="chat-input"></textarea><button id="send-message-button">Send</button>', { prompt: 'line 1\r\n\r\nline 3' });
  try {
    assert.equal(act('prepare').ready, true);
    assert.equal(dom.window.document.querySelector('textarea').value, 'line 1\n\nline 3');
    const target = act('canSubmit');
    assert.equal(target.ready, true);
    assert.equal(target.method, 'button');
  } finally { dom.window.close(); }
});

test('Qwen recognizes a Send message label containing its Enter shortcut', () => {
  const { dom, act } = fixture('<form><textarea id="chat-input"></textarea><button aria-label="Send message (Enter)">↑</button></form>');
  try {
    act('prepare');
    const target = act('canSubmit');
    assert.equal(target.ready, true);
    assert.equal(target.method, 'button');
  } finally { dom.window.close(); }
});

test('Qwen recognizes a send control named through aria-labelledby', () => {
  const { dom, act } = fixture('<form><textarea id="chat-input"></textarea><span id="send-label">Send message</span><button aria-labelledby="send-label">↑</button></form>');
  try {
    act('prepare');
    const target = act('canSubmit');
    assert.equal(target.ready, true);
    assert.equal(target.method, 'button');
  } finally { dom.window.close(); }
});

test('Qwen skips a disabled duplicate when the same send selector has an enabled match', () => {
  const { dom, act } = fixture('<form><textarea id="chat-input"></textarea><button data-testid="send-button" disabled data-left="10">Send</button><button data-testid="send-button" data-left="210">Send</button></form>', {
    selectors: { ...legacySelectors, send: ['button[data-testid="send-button"]'] },
  });
  try {
    act('prepare');
    const target = act('canSubmit');
    assert.equal(target.ready, true);
    assert.equal(target.method, 'button');
    assert.equal(target.x, 260, 'Coordinates must identify the enabled control');
  } finally { dom.window.close(); }
});

for (const [name, control] of [
  ['chat-prompt-send-button wrapper', '<div class="chat-prompt-send-button"><button type="button"><svg></svg></button></div>'],
  ['send-button class', '<button class="send-button" type="button"><svg></svg></button>'],
  ['message-input-right-button-send class', '<div class="message-input-right-button-send"><svg></svg></div>'],
]) {
  test(`Qwen with saved legacy selectors recognizes ${name}`, () => {
    const { dom, act } = fixture(`<form><textarea id="chat-input"></textarea>${control}</form>`);
    try {
      act('prepare');
      const target = act('canSubmit');
      assert.equal(target.ready, true);
      assert.equal(target.method, 'button', 'Known Qwen send controls should be clicked instead of relying on Enter');
      assert.equal(target.sendSource, 'provider');
    } finally { dom.window.close(); }
  });
}

test('Qwen never submits or falls back to Enter when every send control is disabled', () => {
  const { dom, act } = fixture('<form><textarea id="chat-input"></textarea><button class="send-button" disabled>↑</button></form>');
  try {
    let clicks = 0;
    dom.window.document.querySelector('button').addEventListener('click', () => clicks++);
    act('prepare');
    assert.equal(act('canSubmit').reason, 'send_disabled');
    assert.equal(act('claimSubmit').ready, false);
    assert.equal(clicks, 0);
    assert.equal(dom.window.__fusionJob.submitted, false);
  } finally { dom.window.close(); }
});

test('Qwen never reserves or clicks an obscured send control', () => {
  const { dom, act } = fixture('<form><textarea id="chat-input"></textarea><button class="send-button">↑</button></form><div id="overlay"></div>');
  try {
    dom.window.document.elementFromPoint = () => dom.window.document.querySelector('#overlay');
    act('prepare');
    assert.equal(act('canSubmit').reason, 'send_obscured');
    assert.equal(act('claimSubmit').ready, false);
    assert.equal(dom.window.__fusionJob.submitted, false);
  } finally { dom.window.close(); }
});

test('Qwen rejects a genuinely truncated prompt despite an enabled send button', () => {
  const { dom, act } = fixture('<textarea id="chat-input"></textarea><button id="send-message-button">Send</button>');
  try {
    act('prepare');
    dom.window.document.querySelector('textarea').value = 'Explain the code';
    assert.equal(act('canSubmit').reason, 'input_changed');
    assert.equal(act('claimSubmit').ready, false);
    assert.equal(dom.window.__fusionJob.submitted, false);
  } finally { dom.window.close(); }
});

test('Qwen does not infer sending from an unknown form submit control', () => {
  const { dom, act } = fixture('<form><textarea id="chat-input"></textarea><button type="submit">Delete account</button></form>');
  try {
    act('prepare');
    assert.equal(act('canSubmit').reason, 'unrecognized_form_submit');
    assert.equal(act('claimSubmit').ready, false);
    assert.equal(dom.window.__fusionJob.submitted, false);
  } finally { dom.window.close(); }
});

test('Qwen CDP preparation focuses the empty editor without synthetic text or input events', () => {
  const { dom, act } = fixture('<textarea id="chat-input"></textarea><button id="send-message-button" disabled>Send</button>', { input_transport: 'cdp' });
  try {
    const input = dom.window.document.querySelector('textarea');
    let events = 0;
    input.addEventListener('input', () => events++);
    const prepared = act('prepare');
    assert.equal(prepared.ready, true);
    assert.equal(prepared.inputMethod, 'cdp_insert_text');
    assert.equal(input.value, '');
    assert.equal(events, 0);
    assert.equal(dom.window.document.activeElement, input);
    assert.equal(act('canSubmit').ready, false, 'Preparation alone is not accepted input');
  } finally { dom.window.close(); }
});

test('Qwen identifies its composer ahead of generic textareas and retains that editor after DOM changes', () => {
  const { dom, args, act } = fixture('<textarea id="search-box"></textarea><textarea class="message-input-textarea"></textarea><button class="send-button">↑</button>');
  try {
    const document = dom.window.document;
    const input = document.querySelector('.message-input-textarea');
    assert.equal(act('prepare').ready, true);
    assert.equal(input.value, args.prompt);
    assert.equal(document.querySelector('#search-box').value, '');
    // A new higher-priority selector must not redirect an already prepared job.
    document.body.insertAdjacentHTML('afterbegin', '<textarea id="chat-input"></textarea>');
    assert.equal(act('canSubmit').ready, true);
    input.value = 'truncated';
    document.querySelector('#chat-input').value = args.prompt;
    assert.equal(act('canSubmit').reason, 'input_changed', 'Only the originally prepared composer can validate this prompt');
    assert.equal(act('claimSubmit').ready, false);
    assert.equal(dom.window.__fusionJob.submitted, false);
  } finally { dom.window.close(); }
});

test('Qwen native input enables its button, sends exactly once in background and returns complete Markdown', async () => {
  let current, loads = 0, clicks = 0, focuses = 0, attachments = 0, detachments = 0;
  const commands = [], actions = [], logs = [];
  const prompt = 'PRIVATE QWEN TASK\r\nprint(1)';
  const wc = {
    isDestroyed: () => false,
    stop() {},
    focus() { focuses++; },
    getOwnerBrowserWindow() { return { focus() { focuses++; }, show() { focuses++; } }; },
    debugger: {
      attached: false,
      isAttached() { return this.attached; },
      attach() { assert.equal(this.attached, false); this.attached = true; attachments++; },
      detach() { assert.equal(this.attached, true); this.attached = false; detachments++; },
      async sendCommand(method, params) {
        assert.equal(this.attached, true);
        commands.push({ method, ...params });
        const w = current.dom.window;
        if (method === 'Input.insertText') {
          const input = w.document.activeElement;
          assert.equal(input.id, 'chat-input');
          assert.equal(input.value, '', 'No earlier synthetic assignment or duplicate insertion');
          assert.equal(params.text, prompt.replace(/\r\n?/g, '\n'));
          input.value = params.text;
          input.dispatchEvent(new w.InputEvent('input', { bubbles: true, inputType: 'insertText', data: params.text }));
        } else if (method === 'Input.dispatchMouseEvent' && params.type === 'mouseReleased') {
          const button = w.document.querySelector('.send-button');
          assert.equal(button.disabled, false, 'The editor must accept the input before sending');
          button.click();
        }
      },
    },
    async loadURL() {
      loads++;
      current = fixture('<form><textarea id="chat-input"></textarea><button class="send-button" type="button" disabled><svg></svg></button></form>');
      const { document, InputEvent } = current.dom.window;
      assert.equal(typeof InputEvent, 'function');
      const input = document.querySelector('textarea');
      const button = document.querySelector('button');
      input.addEventListener('input', () => { button.disabled = !input.value.trim(); });
      button.addEventListener('click', () => {
        clicks++;
        input.value = '';
        document.body.insertAdjacentHTML('beforeend', '<article data-role="assistant"><div class="qwen-markdown"><h2>PRIVATE QWEN RESULT</h2><p>Complete answer.</p></div><div class="qwen-markdown"><pre><code class="language-python">print(42)\n</code></pre></div><div class="qwen-markdown"><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table></div></article>');
      });
    },
    executeJavaScriptInIsolatedWorld(_id, sources) {
      const code = sources[0].code;
      const action = code.match(/\)\("(inspect|prepare|canSubmit|claimSubmit)"/);
      if (action) actions.push(action[1]);
      if (action?.[1] === 'prepare') assert.equal(this.debugger.attached, true, 'Attach the trusted input channel before preparing Qwen');
      return Promise.resolve(current.dom.window.eval(code));
    },
  };
  try {
    const adapter = new WebsiteAdapter(wc, { id: 'qwen', url: 'http://127.0.0.1:4567/', selectors: legacySelectors }, () => {}, (event, fields) => logs.push({ event, ...fields }));
    const markdown = await adapter.run({ job_id: 'qwen-native-input', purpose: 'candidate', prompt, timeout_seconds: 15, stable_seconds: 0.01, min_wait_seconds: 0 });
    assert.equal(loads, 1);
    assert.equal(clicks, 1);
    assert.equal(focuses, 0, 'Do not bring the model window to the foreground');
    assert.equal(attachments, 1);
    assert.equal(detachments, 1);
    assert.equal(adapter.active, false);
    assert.deepEqual(commands.map(command => [command.method, command.type || '']), [
      ['Emulation.setFocusEmulationEnabled', ''], ['Input.insertText', ''], ['Input.dispatchMouseEvent', 'mousePressed'], ['Input.dispatchMouseEvent', 'mouseReleased'], ['Emulation.setFocusEmulationEnabled', ''],
    ]);
    assert.equal(actions.filter(action => action === 'prepare').length, 1);
    assert.equal(actions.filter(action => action === 'claimSubmit').length, 1);
    assert.match(markdown, /## PRIVATE QWEN RESULT/);
    assert.match(markdown, /```python\nprint\(42\)/);
    assert.match(markdown, /\| A \| B \|/);
    assert.doesNotMatch(markdown, /OLD PRIVATE REPLY/);
    assert.ok(logs.some(entry => entry.event === 'adapter.submission_accepted'));
    assert.ok(logs.some(entry => entry.event === 'adapter.send_state'));
    assert.equal(logs.find(entry => entry.event === 'adapter.start').payload.prompt, prompt);
    assert.match(logs.find(entry => entry.event === 'adapter.complete').payload.markdown, /PRIVATE QWEN RESULT/);
    assert.doesNotMatch(JSON.stringify(logs.map(({payload, ...metadata}) => metadata)), /PRIVATE QWEN|OLD PRIVATE|print\(1\)|<article|127\.0\.0\.1/);
  } finally { current?.dom.window.close(); }
});

for (const interruption of ['rejected', 'cancelled']) {
  test(`Qwen ${interruption} native insertion is never repeated or submitted and releases its debugger`, async () => {
    const controller = new AbortController();
    const commands = [], actions = [], logs = [];
    let current, attachments = 0, detachments = 0, clicks = 0;
    const prompt = 'PRIVATE DRAFT LEFT AFTER INTERRUPTION';
    const wc = {
      isDestroyed: () => false,
      stop() {},
      focus() { assert.fail('Interrupted background input must not focus its window'); },
      debugger: {
        attached: false,
        isAttached() { return this.attached; },
        attach() { this.attached = true; attachments++; },
        detach() { this.attached = false; detachments++; },
        sendCommand(method, params) {
          commands.push(method);
          if (method === 'Emulation.setFocusEmulationEnabled') return Promise.resolve({});
          assert.equal(method, 'Input.insertText', 'No mouse or Enter event may follow an unsuccessful insertion');
          const w = current.dom.window;
          const input = w.document.activeElement;
          assert.equal(input.value, '', 'The task must never repeat an insertion whose outcome is ambiguous');
          input.value = params.text;
          input.dispatchEvent(new w.InputEvent('input', { bubbles: true, inputType: 'insertText', data: params.text }));
          // The renderer can apply the edit before the command response is lost.
          // Keep this deliberately ambiguous: retrying would duplicate the draft.
          if (interruption === 'rejected') return Promise.reject(new Error('Synthetic CDP response lost after insertion'));
          queueMicrotask(() => controller.abort());
          return new Promise(() => {});
        },
      },
      async loadURL() {
        current = fixture('<textarea id="chat-input"></textarea><button class="send-button" disabled>↑</button>');
        const document = current.dom.window.document;
        const input = document.querySelector('textarea');
        const button = document.querySelector('button');
        input.addEventListener('input', () => { button.disabled = false; });
        button.addEventListener('click', () => { clicks++; });
      },
      executeJavaScriptInIsolatedWorld(_id, sources) {
        const code = sources[0].code;
        const action = code.match(/\)\("(inspect|prepare|canSubmit|claimSubmit)"/);
        if (action) actions.push(action[1]);
        return Promise.resolve(current.dom.window.eval(code));
      },
    };
    try {
      const adapter = new WebsiteAdapter(wc, { id: 'qwen', url: 'http://127.0.0.1:4567/', selectors: legacySelectors }, () => {}, (event, fields) => logs.push({ event, ...fields }));
      await assert.rejects(
        adapter.run({ job_id: `qwen-${interruption}`, purpose: 'candidate', prompt, timeout_seconds: 5, stable_seconds: 0.01, min_wait_seconds: 0 }, controller.signal),
        error => error.code === (interruption === 'cancelled' ? 'cancelled' : 'input_dispatch_failed'),
      );
      assert.deepEqual(commands, ['Emulation.setFocusEmulationEnabled', 'Input.insertText', 'Emulation.setFocusEmulationEnabled']);
      assert.equal(clicks, 0);
      assert.equal(actions.filter(action => action === 'prepare').length, 1);
      assert.equal(actions.includes('claimSubmit'), false);
      assert.equal(attachments, 1);
      assert.equal(detachments, 1);
      assert.equal(wc.debugger.isAttached(), false);
      assert.equal(adapter.active, false);
      assert.equal(current.dom.window.document.querySelector('textarea').value, prompt);
      assert.equal(current.dom.window.__fusionJob.cancelled, true);
      assert.equal(current.dom.window.__fusionJob.submitted, false);
      const failed = logs.find(entry => entry.event === 'adapter.failed');
      assert.equal(failed.input_dispatched, true);
      assert.equal(failed.dispatched, false);
      assert.equal(failed.accepted, false);
      assert.doesNotMatch(JSON.stringify(logs.map(({payload, ...metadata}) => metadata)), /PRIVATE DRAFT|<textarea|127\.0\.0\.1/);
    } finally { current?.dom.window.close(); }
  });
}

test('standalone send detection is read-only with an existing draft and an expired job', () => {
  const { dom, act } = fixture('<textarea id="chat-input">KEEP MY DRAFT</textarea><button class="send-button" disabled>发送</button><div id="overlay"></div>');
  try {
    const document = dom.window.document;
    document.elementFromPoint = () => document.querySelector('#overlay');
    const draft = document.querySelector('textarea');
    let effects = 0;
    for (const node of [draft, document.querySelector('button')]) {
      node.focus = () => effects++;
      node.scrollIntoView = () => effects++;
      node.addEventListener('click', () => effects++);
      node.addEventListener('input', () => effects++);
    }
    const report = act('diagnoseSend', { deadline: 0 });
    assert.equal(effects, 0);
    assert.equal(draft.value, 'KEEP MY DRAFT');
    assert.equal(dom.window.__fusionJob, undefined);
    assert.equal(report.input.valueLength, 13);
    assert.equal(report.sendTarget.selector, 'button.send-button');
    assert.equal(report.sendTarget.disabled, true);
    assert.equal(report.sendTarget.obscured, true);
    assert.equal(report.sendTarget.hitTarget.id, 'overlay');
    assert.equal(report.sendTarget.bounds.width, 100);
    assert.equal(typeof report.page.hasFocus, 'boolean');
    assert.ok(report.selectorChecks.some(item => item.selector === 'button.send-button' && item.matches === 1));
  } finally { dom.window.close(); }
});

test('send detection includes hidden selector matches and unknown buttons without guessing SVG meaning', () => {
  const { dom, act } = fixture('<form><textarea id="chat-input"></textarea><button id="send-message-button" hidden>Send</button><button id="unknown"><svg><path d="M1 2"/></svg></button></form>');
  try {
    const report = act('diagnoseSend');
    assert.equal(report.sendTarget, null);
    const known = report.selectorChecks.flatMap(item => item.candidates).find(item => item.id === 'send-message-button');
    assert.equal(known.visible, false);
    assert.ok(report.unmatchedControls.some(item => item.id === 'unknown'));
    assert.equal(dom.window.__fusionJob, undefined);
  } finally { dom.window.close(); }
});

test('send detection works before a composer exists and caps button details', () => {
  const { dom, act } = fixture(Array.from({ length: 40 }, (_, index) => `<button id="control-${index}" class="send-button">Other</button>`).join(''));
  try {
    const report = act('diagnoseSend');
    assert.equal(report.input, null);
    assert.equal(report.sendTarget, null);
    assert.ok(report.selectorChecks.flatMap(item => item.candidates).length <= 12);
    assert.equal(report.unmatchedControls.length, 12);
  } finally { dom.window.close(); }
});
