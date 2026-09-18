'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { ProviderLayout, normalizeBounds, clampBounds } = require('../electron/provider-layout.cjs');

let focused = null;
class FakeView {
  constructor(id) {
    this.id = id; this.visible = false; this.bounds = null; this.parent = null;
    this.webContents = {
      id, state: { loggedIn: true, draft: `${id} preserved` }, closeCount: 0, destroyed: false,
      isDestroyed() { return this.destroyed; },
      close() { this.closeCount++; this.destroyed = true; },
      focus() { focused = this; },
      isFocused() { return focused === this; },
    };
  }
  setBounds(bounds) { this.bounds = { ...bounds }; }
  getBounds() { return { ...this.bounds }; }
  setVisible(visible) { this.visible = visible; }
  getVisible() { return this.visible; }
}
class FakeWindow extends EventEmitter {
  constructor(options = {}) {
    super(); this.visible = options.show !== false; this.minimized = false; this.destroyed = false;
    this.size = [options.width || 1200, options.height || 850]; this.focusCount = 0; this.inactiveShows = 0; this.activeShows = 0; this.destroyCount = 0;
    this.contentView = {
      children: [],
      addChildView: view => {
        assert.ok(!this.destroyed, 'must never attach to destroyed host');
        assert.ok(!view.parent || view.parent === this, 'must detach from old owner before attaching');
        this.contentView.children = this.contentView.children.filter(item => item !== view);
        this.contentView.children.push(view); view.parent = this;
      },
      removeChildView: view => {
        this.contentView.children = this.contentView.children.filter(item => item !== view);
        if (view.parent === this) view.parent = null;
      },
    };
  }
  getContentSize() { return this.size; }
  isDestroyed() { return this.destroyed; }
  isVisible() { return this.visible; }
  isMinimized() { return this.minimized; }
  setMenuBarVisibility() {}
  setTitle(title) { this.title = title; }
  showInactive() { this.visible = true; this.inactiveShows++; }
  show() { this.visible = true; this.activeShows++; }
  hide() { this.visible = false; }
  focus() { this.focusCount++; }
  restore() { this.minimized = false; this.emit('restore'); }
  minimize() { this.minimized = true; this.emit('minimize'); }
  resize(width, height) { this.size = [width, height]; this.emit('resize'); }
  close() { let prevented = false; this.emit('close', { preventDefault: () => { prevented = true; } }); if (!prevented) this.destroy(); return prevented; }
  destroy() { this.destroyed = true; this.visible = false; this.destroyCount++; this.emit('closed'); }
}
function setup() {
  const main = new FakeWindow(); const windows = []; const logs = []; const statuses = []; const interrupted = [];
  let busy = false, maintenance = false;
  const recoverable = new Set();
  const layout = new ProviderLayout({ mainWindow: main,
    createWindow: options => { const win = new FakeWindow(options); windows.push(win); return win; },
    isBusy: () => busy, getFocusedWebContents: () => focused,
    isMaintenance: () => maintenance, canRecover: id => recoverable.has(id),
    onStatus: status => statuses.push(status), onInputInterrupted: (job, reason) => interrupted.push({ job, reason }),
    log: (event, fields) => logs.push({ event, fields }),
  });
  const qwen = new FakeView('qwen'); const deepseek = new FakeView('deepseek');
  layout.add('qwen', qwen, 'Qwen'); layout.add('deepseek', deepseek, 'DeepSeek');
  const left = { x: 500, y: 100, width: 320, height: 700 }; const right = { x: 830, y: 100, width: 360, height: 700 };
  const tabs = (active = 'qwen', hidden = false) => layout.setLayout({ mode: 'tabs', panes: [{ provider_id: active, bounds: { x: 500, y: 100, width: 690, height: 700 } }], active_provider: active, hidden });
  const split = () => layout.setLayout({ mode: 'split', panes: [{ provider_id: 'qwen', bounds: left }, { provider_id: 'deepseek', bounds: right }], active_provider: 'qwen', hidden: false });
  const acquire = (id, options = {}) => layout.acquireInput({ job: { provider_id: id, job_id: `${id}-job`, request_id: 'request-1' }, deadline: Date.now() + 5000, ...options });
  return { main, windows, layout, qwen, deepseek, left, right, logs, statuses, interrupted, tabs, split, acquire, busy: value => { busy = value; },
    recoverable, maintenance: value => { maintenance = value; } };
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('layout validation rejects unknown providers, duplicate panes, malformed mode and dimensions before mutation', () => {
  const h = setup(); h.tabs(); const before = JSON.stringify(h.layout.state);
  for (const payload of [null, { mode: 'popups', panes: [] }, { mode: 'tabs', panes: [], hidden: 1 },
    { mode: 'tabs', panes: [{ provider_id: 'unknown', bounds: h.left }] },
    { mode: 'split', panes: [{ provider_id: 'qwen', bounds: h.left }, { provider_id: 'qwen', bounds: h.right }] },
    { mode: 'tabs', panes: [{ provider_id: 'qwen', bounds: { ...h.left, width: NaN } }] },
    { mode: 'tabs', panes: [{ provider_id: 'qwen', bounds: h.left }, { provider_id: 'deepseek', bounds: h.right }] },
    { mode: 'tabs', panes: [], active_provider: 'unknown' }, { mode: 'tabs', panes: [], reopen_windows: 'true' }]) {
    assert.throws(() => h.layout.setLayout(payload), { code: 'invalid_layout' });
    assert.equal(JSON.stringify(h.layout.state), before);
  }
  assert.throws(() => normalizeBounds({ ...h.left, x: -1 }));
  assert.deepEqual(clampBounds({ x: 3000, y: 5000, width: 100, height: 100 }, [100, 90]), { x: 99, y: 89, width: 1, height: 1 });
  h.layout.shutdown();
});

test('hidden providers retain real viewports and legacy bounds/select APIs work', () => {
  const h = setup();
  assert.equal(h.qwen.visible, false); assert.equal(h.deepseek.visible, false);
  assert.ok(h.qwen.bounds.width > 0 && h.qwen.bounds.height > 0);
  h.layout.setBounds(h.left); assert.equal(h.qwen.visible, true);
  h.layout.showProvider('deepseek'); assert.equal(h.deepseek.visible, true); assert.equal(h.qwen.visible, false);
  h.layout.setBounds(null); assert.equal(h.deepseek.visible, false); assert.deepEqual(h.qwen.bounds, h.left);
  h.layout.shutdown();
});

test('tabs, split and independent windows reparent the same live views without replacing webContents', () => {
  const h = setup(); const identities = [h.qwen.webContents, h.deepseek.webContents];
  h.tabs(); assert.equal(h.qwen.visible, true); assert.equal(h.deepseek.visible, false);
  h.split(); assert.equal(h.qwen.visible, true); assert.equal(h.deepseek.visible, true);
  assert.deepEqual(h.qwen.bounds, h.left); assert.deepEqual(h.deepseek.bounds, h.right);
  h.layout.setLayout({ mode: 'windows', panes: [], hidden: false });
  assert.equal(h.main.contentView.children.length, 0);
  assert.equal(h.windows.length, 2);
  assert.equal(h.qwen.parent, h.windows[0]); assert.equal(h.deepseek.parent, h.windows[1]);
  assert.ok(h.windows.every(win => win.inactiveShows === 1 && win.focusCount === 0 && win.activeShows === 0));
  h.windows[0].resize(750, 650); assert.deepEqual(h.qwen.bounds, { x: 0, y: 0, width: 750, height: 650 });
  h.tabs('deepseek'); assert.equal(h.main.contentView.children.length, 2); assert.ok(h.windows.every(win => !win.visible));
  assert.equal(h.qwen.webContents, identities[0]); assert.equal(h.deepseek.webContents, identities[1]);
  assert.equal(h.qwen.webContents.state.draft, 'qwen preserved'); assert.equal(h.qwen.webContents.closeCount, 0);
  h.layout.shutdown();
});

test('mode changes during jobs or import are rejected but same-mode geometry updates remain allowed', () => {
  const h = setup(); h.tabs(); h.busy(true);
  assert.throws(() => h.split(), { code: 'layout_busy' });
  assert.throws(() => h.tabs('deepseek'), { code: 'layout_busy' });
  assert.throws(() => h.layout.showProvider('deepseek'), { code: 'layout_busy' });
  assert.equal(h.layout.state.mode, 'tabs'); assert.equal(h.windows.length, 0);
  h.layout.setLayout({ mode: 'tabs', panes: [{ provider_id: 'qwen', bounds: h.left }], hidden: false });
  assert.deepEqual(h.qwen.bounds, h.left);
  h.layout.shutdown();
});

test('closing an auxiliary window hides it and routine resize never reopens it; explicit reopen preserves page', () => {
  const h = setup(); h.layout.setLayout({ mode: 'windows', panes: [] });
  const first = h.windows[0]; assert.equal(first.close(), true); assert.equal(first.destroyed, false);
  assert.equal(h.qwen.visible, false); assert.equal(h.qwen.webContents.closeCount, 0);
  h.layout.setLayout({ mode: 'windows', panes: [] }); assert.equal(first.visible, false);
  h.windows[1].resize(600, 600); assert.equal(first.visible, false);
  assert.deepEqual(h.statuses.at(-1), { type: 'layout-window-closed', provider_id: 'qwen' });
  h.layout.setLayout({ mode: 'windows', panes: [], reopen_windows: true });
  assert.equal(first.visible, true); assert.equal(first.focusCount, 0); assert.equal(h.windows.length, 2);
  first.close(); h.layout.showProvider('qwen'); assert.equal(first.visible, true); assert.equal(first.focusCount, 1);
  h.layout.shutdown(); h.layout.shutdown();
  assert.equal(h.qwen.webContents.closeCount, 1); assert.equal(h.deepseek.webContents.closeCount, 1);
  assert.equal(first.destroyCount, 1); assert.ok(!h.main.destroyed);
});

test('input leases serialize hidden-tab input and restore the user selected tab and prior focus', async () => {
  const h = setup(); h.tabs(); h.qwen.webContents.focus();
  const releaseDeepseek = await h.acquire('deepseek');
  assert.equal(h.deepseek.visible, true); assert.equal(h.qwen.visible, false);
  assert.equal(h.layout.state.active_provider, 'qwen'); assert.equal(focused, h.deepseek.webContents);
  let secondStarted = false;
  const next = h.acquire('qwen').then(release => { secondStarted = true; return release; });
  await tick(); assert.equal(secondStarted, false);
  await releaseDeepseek(); const releaseQwen = await next;
  assert.equal(secondStarted, true); assert.equal(h.qwen.visible, true);
  await releaseQwen(); await releaseQwen();
  assert.equal(h.layout.lease, null); assert.equal(h.qwen.visible, true); assert.equal(h.deepseek.visible, false);
  assert.equal(focused, h.qwen.webContents); assert.equal(h.main.focusCount, 0);
  assert.equal(h.logs.filter(row => row.event === 'input_lease.acquired').length, 2);
  assert.equal(h.logs.filter(row => row.event === 'input_lease.released').length, 2);
  assert.deepEqual(h.statuses.filter(row => row.type === 'input-visibility').map(row => row.active), [true, false, true, false]);
  h.layout.shutdown();
});

test('split lease retains the adjacent pane and pins target geometry until release', async () => {
  const h = setup(); h.split(); const release = await h.acquire('qwen');
  assert.equal(h.deepseek.visible, true);
  const updatedLeft = { ...h.left, width: 250 }; const updatedRight = { ...h.right, x: 760, width: 430 };
  h.layout.setLayout({ mode: 'split', panes: [{ provider_id: 'qwen', bounds: updatedLeft }, { provider_id: 'deepseek', bounds: updatedRight }] });
  assert.deepEqual(h.qwen.bounds, h.left); assert.equal(h.deepseek.visible, false, 'overlapping newly resized pane cannot intercept input');
  await release(); assert.deepEqual(h.qwen.bounds, updatedLeft); assert.deepEqual(h.deepseek.bounds, updatedRight); assert.equal(h.deepseek.visible, true);
  h.layout.shutdown();
});

test('settings hide immediately interrupts active input and queued input waits until the page area returns', async () => {
  const h = setup(); h.tabs(); const release = await h.acquire('qwen');
  h.tabs('qwen', true);
  assert.equal(h.qwen.visible, false); assert.equal(h.deepseek.visible, false);
  assert.equal(h.interrupted[0].reason, 'input_view_hidden');
  let acquired = false; const pending = h.acquire('deepseek').then(result => { acquired = true; return result; });
  await release(); await tick(); assert.equal(acquired, false); assert.equal(h.qwen.visible, false);
  h.tabs(); const finish = await pending; assert.equal(h.deepseek.visible, true);
  await finish(); h.layout.shutdown();
});

test('cancelled and expired input waits never acquire or dispatch a later input', async () => {
  const h = setup(); h.tabs(); const release = await h.acquire('qwen');
  const controller = new AbortController(); const cancelled = h.acquire('deepseek', { signal: controller.signal });
  const cancelledCheck = assert.rejects(cancelled, { code: 'cancelled' }); controller.abort(); await cancelledCheck;
  await assert.rejects(h.acquire('deepseek', { deadline: Date.now() + 15 }), { code: 'input_lease_timeout' });
  await release(); assert.equal(h.layout.queue.length, 0); assert.equal(h.logs.filter(row => row.event === 'input_lease.acquired').length, 1);
  await assert.rejects(h.acquire('deepseek', { signal: controller.signal }), { code: 'cancelled' });
  h.layout.shutdown();
});

test('queued settings-hidden work is rejected when provider is removed or app shuts down', async () => {
  const h = setup();
  const removed = h.acquire('deepseek'); const removedCheck = assert.rejects(removed, { code: 'provider_unavailable' });
  h.layout.remove('deepseek'); await removedCheck;
  assert.equal(h.deepseek.webContents.closeCount, 1);
  const pending = h.acquire('qwen'); const pendingCheck = assert.rejects(pending, { code: 'cancelled' });
  h.layout.shutdown(); await pendingCheck;
  assert.equal(h.qwen.webContents.closeCount, 1); assert.equal(h.layout.queue.length, 0);
});

test('a closed or minimized model window waits without blocking another visible provider', async () => {
  const h = setup(); h.layout.setLayout({ mode: 'windows', panes: [] });
  h.windows[0].close(); let firstStarted = false;
  const hidden = h.acquire('qwen').then(release => { firstStarted = true; return release; });
  const releaseVisible = await h.acquire('deepseek'); assert.equal(firstStarted, false);
  await releaseVisible(); h.layout.setLayout({ mode: 'windows', panes: [], reopen_windows: true });
  await (await hidden)();
  h.windows[0].minimize(); let resumed = false;
  const minimized = h.acquire('qwen').then(release => { resumed = true; return release; });
  await tick(); assert.equal(resumed, false); h.windows[0].restore(); await (await minimized)();
  assert.ok(h.windows.every(win => win.focusCount === 0));
  h.layout.shutdown();
});

test('closing a model window during its lease interrupts without reopening it on release', async () => {
  const h = setup(); h.layout.setLayout({ mode: 'windows', panes: [] });
  const release = await h.acquire('qwen'); h.windows[0].close();
  assert.equal(h.interrupted[0].reason, 'input_view_hidden'); assert.equal(h.qwen.visible, false);
  await release(); assert.equal(h.windows[0].visible, false); assert.equal(h.qwen.visible, false);
  assert.equal(h.qwen.webContents.closeCount, 0); h.layout.shutdown();
});

test('explicit reopen restores minimized windows and resumes queued input while normal layout never restores them', async () => {
  const h = setup(); h.layout.setLayout({ mode: 'windows', panes: [] });
  h.windows[0].minimize(); h.busy(true);
  let acquired = false; const pending = h.acquire('qwen').then(release => { acquired = true; return release; });
  h.layout.setLayout({ mode: 'windows', panes: [] });
  await tick(); assert.equal(h.windows[0].isMinimized(), true); assert.equal(acquired, false);
  h.layout.setLayout({ mode: 'windows', panes: [], reopen_windows: true });
  const release = await pending;
  assert.equal(h.windows[0].isMinimized(), false); assert.equal(acquired, true);
  await release();
  assert.ok(h.windows.every(window => window.focusCount === 0 && window.activeShows === 0));
  h.layout.shutdown();
});

test('main-window minimization queues tab leases while independent windows remain usable', async () => {
  const h = setup(); h.tabs(); h.main.minimize();
  let started = false; const pending = h.acquire('qwen').then(release => { started = true; return release; });
  await tick(); assert.equal(started, false); h.main.restore(); h.layout.resume(); await (await pending)();
  h.layout.setLayout({ mode: 'windows', panes: [] }); h.main.minimize();
  await (await h.acquire('qwen'))();
  h.layout.shutdown();
});

test('focus failure releases the lease, restores selected view and allows a subsequent provider', async () => {
  const h = setup(); h.tabs();
  h.deepseek.webContents.focus = () => { throw new Error('renderer unavailable'); };
  await assert.rejects(h.acquire('deepseek'), { code: 'input_view_unavailable' });
  assert.equal(h.layout.lease, null); assert.equal(h.qwen.visible, true);
  await (await h.acquire('qwen'))(); h.layout.shutdown();
});

test('failure while restoring previous focus does not block the next queued input', async () => {
  const h = setup(); h.tabs();
  const previous = { isDestroyed: () => false, isFocused: () => false, focus: () => { throw new Error('stale prior focus'); } };
  focused = previous;
  const release = await h.acquire('qwen'); const next = h.acquire('deepseek');
  await release(); const finish = await next;
  assert.equal(h.layout.lease.item.id, 'deepseek');
  assert.ok(h.logs.some(row => row.event === 'input_lease.restore_failed'));
  await finish(); h.layout.shutdown();
});

test('removing the actively leased provider interrupts and drains another queued provider', async () => {
  const h = setup(); h.tabs(); const release = await h.acquire('qwen'); const pending = h.acquire('deepseek');
  h.layout.remove('qwen'); const finish = await pending;
  assert.equal(h.interrupted[0].reason, 'provider_unavailable'); assert.equal(h.qwen.webContents.closeCount, 1);
  await release(); assert.equal(h.layout.lease.item.id, 'deepseek');
  await finish(); h.layout.shutdown();
});

test('provider removal from a window detaches and closes only the removed view', () => {
  const h = setup(); h.layout.setLayout({ mode: 'windows', panes: [] });
  h.layout.remove('deepseek'); h.layout.remove('deepseek');
  assert.equal(h.deepseek.parent, null); assert.equal(h.deepseek.webContents.closeCount, 1);
  assert.equal(h.windows[1].destroyCount, 1); assert.equal(h.qwen.visible, true); assert.equal(h.qwen.webContents.closeCount, 0);
  h.layout.setTitle('qwen', 'Qwen 3.8'); assert.equal(h.windows[0].title, 'Qwen 3.8 · MultiLLM Fusion');
  h.layout.shutdown();
});

test('explicit recovery access reveals the existing background tab while ordinary busy switches stay locked', () => {
  const h = setup(); h.tabs('deepseek'); h.busy(true); h.recoverable.add('qwen');
  const original = h.qwen.webContents;
  assert.throws(() => h.layout.showProvider('qwen'), {code: 'layout_busy'});
  const result = h.layout.showRecoveryProvider('qwen');
  assert.equal(result.active_provider, 'qwen'); assert.equal(result.panes[0].provider_id, 'qwen');
  assert.equal(h.qwen.visible, true); assert.equal(h.deepseek.visible, false);
  assert.equal(h.qwen.webContents, original); assert.equal(original.state.draft, 'qwen preserved'); assert.equal(original.closeCount, 0);
  assert.equal(h.statuses.at(-1).type, 'recovery-provider-opened');
  assert.throws(() => h.layout.showProvider('deepseek'), {code: 'layout_busy'});
  h.layout.setLayout({...result, panes: [{provider_id: 'qwen', bounds: h.left}]});
  assert.equal(h.layout.state.active_provider, 'qwen'); h.layout.shutdown();
});

test('recovery access stays denied during input leases, browser maintenance and hidden settings', async () => {
  const h = setup(); h.tabs(); h.busy(true); h.recoverable.add('deepseek');
  const unchanged = JSON.stringify(h.layout.state);
  const release = await h.acquire('qwen');
  assert.throws(() => h.layout.showRecoveryProvider('deepseek'), {code: 'layout_busy'});
  assert.equal(JSON.stringify(h.layout.state), unchanged); await release();
  h.maintenance(true);
  assert.throws(() => h.layout.showRecoveryProvider('deepseek'), {code: 'recovery_unavailable'});
  assert.equal(JSON.stringify(h.layout.state), unchanged); h.maintenance(false);
  h.layout.setLayout({...h.layout.state, hidden: true});
  assert.throws(() => h.layout.showRecoveryProvider('deepseek'), {code: 'layout_hidden'});
  h.layout.shutdown();
});

test('recovery access cannot reveal a provider without a currently authorized manual wait', () => {
  const h = setup(); h.tabs(); h.busy(true);
  assert.throws(() => h.layout.showRecoveryProvider('deepseek'), {code: 'recovery_unavailable'});
  h.recoverable.add('qwen');
  assert.throws(() => h.layout.showRecoveryProvider('deepseek'), {code: 'recovery_unavailable'});
  h.recoverable.delete('qwen');
  assert.throws(() => h.layout.showRecoveryProvider('qwen'), {code: 'recovery_unavailable'});
  h.layout.shutdown();
});

test('recovery access in split mode retains pane sizes and reveals an unseen provider in the right pane', () => {
  const h = setup(), third = new FakeView('chatgpt'); h.layout.add('chatgpt', third, 'ChatGPT');
  h.split(); h.busy(true); h.recoverable.add('deepseek'); h.recoverable.add('chatgpt');
  h.layout.showRecoveryProvider('deepseek'); assert.deepEqual(h.layout.state.panes.map(p => p.provider_id), ['qwen', 'deepseek']);
  const result = h.layout.showRecoveryProvider('chatgpt');
  assert.deepEqual(result.panes.map(p => p.provider_id), ['qwen', 'chatgpt']);
  assert.deepEqual(third.bounds, h.right); assert.deepEqual(h.qwen.bounds, h.left);
  assert.equal(h.deepseek.visible, false); assert.equal(third.visible, true); assert.equal(third.webContents.closeCount, 0);
  h.layout.shutdown();
});

test('explicit recovery access restores only its hidden independent window and keeps other jobs intact', () => {
  const h = setup(); h.layout.setLayout({mode:'windows', panes:[], hidden:false});
  const [qwenWindow, deepseekWindow] = h.windows;
  qwenWindow.close(); qwenWindow.minimize(); deepseekWindow.close();
  h.busy(true); h.recoverable.add('qwen');
  const result = h.layout.showRecoveryProvider('qwen');
  assert.equal(result.mode, 'windows'); assert.equal(qwenWindow.visible, true); assert.equal(qwenWindow.minimized, false);
  assert.equal(qwenWindow.focusCount, 1); assert.equal(deepseekWindow.visible, false);
  assert.equal(h.qwen.webContents.closeCount, 0); assert.equal(h.interrupted.length, 0);
  h.layout.shutdown();
});
