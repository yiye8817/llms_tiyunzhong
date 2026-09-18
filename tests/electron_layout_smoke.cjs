'use strict';

// Native integration fixture, not part of `npm test`.
//   node tests/electron_layout_smoke.cjs              # desktop when available
//   node tests/electron_layout_smoke.cjs --headless   # CI, when supported
// A root-only disposable-container run additionally requires the explicit
// --allow-root-no-sandbox flag. Production application settings are untouched.
// These localhost controls are deliberately synthetic. Their DOM does not claim
// to reproduce the current Qwen 3.8 website or its official selectors.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

if (!process.versions.electron) {
  const { spawnSync } = require('node:child_process');
  const cli = new Set(process.argv.slice(2));
  const allowed = new Set(['--headless', '--native', '--allow-root-no-sandbox']);
  if ([...cli].some(arg => !allowed.has(arg)) || (cli.has('--headless') && cli.has('--native'))) {
    console.error('Usage: node tests/electron_layout_smoke.cjs [--native | --headless] [--allow-root-no-sandbox]');
    process.exit(2);
  }
  const hasDisplay = Boolean(process.env.DISPLAY || process.env.WAYLAND_DISPLAY);
  const headless = cli.has('--headless') || (!cli.has('--native') && !hasDisplay);
  if (!headless && !hasDisplay) {
    console.error('UNSUPPORTED: --native needs DISPLAY or WAYLAND_DISPLAY. Run this fixture from your Linux desktop terminal.');
    process.exit(2);
  }
  const args = headless ? ['--ozone-platform=headless', '--disable-gpu', '--disable-dev-shm-usage'] : [];
  if (process.getuid?.() === 0) {
    if (!cli.has('--allow-root-no-sandbox')) {
      console.error('UNSUPPORTED: run as your normal desktop user. Disposable root containers may explicitly use --allow-root-no-sandbox for this fixture only.');
      process.exit(2);
    }
    args.push('--no-sandbox');
    console.error('Fixture only: Chromium sandbox disabled by the explicit root-container flag; no application configuration is changed.');
  } else {
    // Keep Chromium user-namespace sandboxing; never chown/chmod the SUID helper.
    args.push('--disable-setuid-sandbox');
  }
  let binary;
  try { binary = require('electron'); } catch {
    console.error('UNSUPPORTED: Electron dependency is missing. Install the project dependencies first.');
    process.exit(2);
  }
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'fusion-layout-fixture-'));
  const env = { ...process.env, FUSION_LAYOUT_FIXTURE_DIR: temporary, FUSION_LAYOUT_FIXTURE_MODE: headless ? 'headless' : 'native' };
  delete env.ELECTRON_RUN_AS_NODE;
  console.log(`Electron layout fixture mode: ${env.FUSION_LAYOUT_FIXTURE_MODE}; all pages and requests use a disposable localhost server.`);
  let result;
  try {
    result = spawnSync(binary, [...args, __filename], { stdio: 'inherit', timeout: 65000, killSignal: 'SIGKILL', env });
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
  if (result.error) console.error(`Electron fixture process error: ${result.error.message}`);
  if (result.signal) console.error(`UNSUPPORTED/CRASHED: Electron terminated with ${result.signal}; no native fixture pass was recorded. Retry on your Linux desktop, not by disabling its sandbox.`);
  process.exit(result.status ?? 2);
}

const http = require('node:http');
const { app, BrowserWindow, BaseWindow, WebContentsView, webContents } = require('electron');
const { WebsiteAdapter } = require('../electron/adapter.cjs');
const { ProviderLayout } = require('../electron/provider-layout.cjs');

const stateDir = process.env.FUSION_LAYOUT_FIXTURE_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'fusion-layout-fixture-'));
const native = process.env.FUSION_LAYOUT_FIXTURE_MODE !== 'headless';
app.setPath('userData', stateDir);
app.setPath('sessionData', path.join(stateDir, 'session'));

const ids = ['qwen', 'deepseek'];
const events = [];
const navigationCount = new Map(ids.map(id => [id, 0]));
const requests = new Map(ids.map(id => [id, []]));
const views = new Map();
const controllers = new Map();
let server, mainWindow, layout, base, finished = false, deadlineTimer;

function trace(event, fields = {}) {
  events.push({ event, ...fields });
  console.log(JSON.stringify({ time: new Date().toISOString(), component: 'layout-fixture', event, ...fields }));
}

function fixture(id) {
  return `<!doctype html><html><head><meta charset="utf-8"><title>Local ${id} focus fixture</title>
  <style>body{font:16px sans-serif;margin:20px}textarea{display:block;width:85%;height:120px}
  [role=button],button{display:inline-block;padding:12px;margin-top:12px;background:#246;color:white;cursor:pointer}
  [aria-disabled=true]{opacity:.5}pre{white-space:pre-wrap}</style></head><body>
  <h1>Local ${id} focus fixture</h1><div data-role="assistant"><p>STALE_REPLY</p></div>
  ${id === 'qwen' ? '<button type="button" id="local-new-chat" aria-label="新建对话">新建对话</button><button type="button" data-testid="model-selector">Qwen3.8-Max</button>' : ''}
  <form onsubmit="return false"><textarea id="prompt-textarea" aria-label="消息"></textarea>
  ${id === 'qwen'
    ? '<div id="local-send-control" role="button" tabindex="0" aria-label="发送" aria-disabled="true">发送</div>'
    : '<button id="local-send-control" type="button" aria-label="发送" disabled>发送</button>'}
  </form><script>
  (() => {
    const id = ${JSON.stringify(id)};
    const input = document.querySelector('textarea');
    const send = document.querySelector('#local-send-control');
    let controlledValue = '';
    window.fixture = { clicks: 0, accepted: 0, inputs: [], rejectedClicks: [], prompt: '', clickFocus: null, clickTrusted: null, httpStatus: null };
    const disable = value => { send.setAttribute('aria-disabled', String(value)); if ('disabled' in send) send.disabled = value; };
    document.querySelector('#local-new-chat')?.addEventListener('click', event => {
      if (!event.isTrusted || !document.hasFocus()) return;
      document.querySelectorAll('[data-role="assistant"],[data-role="user"],#stop').forEach(node => node.remove());
      controlledValue = ''; input.value = ''; disable(true);
      window.fixture = { clicks: 0, accepted: 0, inputs: [], rejectedClicks: [], prompt: '', clickFocus: null, clickTrusted: null, httpStatus: null };
      history.pushState({}, '', '/page/qwen?conversation=fresh-' + Date.now());
    });
    input.addEventListener('input', event => {
      const focused = document.hasFocus();
      window.fixture.inputs.push({ text: input.value, trusted: event.isTrusted, focused });
      if (id === 'qwen' && (!event.isTrusted || !focused)) return;
      controlledValue = input.value;
      disable(!controlledValue.trim());
    });
    send.addEventListener('click', async event => {
      window.fixture.clicks++;
      const focused = document.hasFocus();
      if (!event.isTrusted || !focused || !controlledValue.trim() || controlledValue !== input.value) {
        window.fixture.rejectedClicks.push({ focused, trusted: event.isTrusted, complete: controlledValue === input.value });
        return;
      }
      window.fixture.accepted++;
      window.fixture.prompt = controlledValue;
      window.fixture.clickFocus = focused;
      window.fixture.clickTrusted = event.isTrusted;
      const prompt = controlledValue;
      controlledValue = ''; input.value = ''; disable(true);
      const user = document.createElement('div'); user.dataset.role = 'user'; user.textContent = prompt; document.body.append(user);
      const stop = document.createElement('button'); stop.id = 'stop'; stop.textContent = '停止'; document.body.append(stop);
      const reply = document.createElement('div'); reply.dataset.role = 'assistant'; reply.textContent = 'STREAMING_REPLY'; document.body.append(reply);
      try {
        const response = await fetch('/api/' + id, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prompt }) });
        window.fixture.httpStatus = response.status;
        const value = await response.json();
        if (!response.ok) throw new Error('HTTP ' + response.status);
        await new Promise(resolve => setTimeout(resolve, 150));
        reply.innerHTML = value.html;
      } catch (error) { reply.textContent = 'FIXTURE_NETWORK_FAILED: ' + error.message; }
      finally { stop.remove(); }
    });
  })();
  </script></body></html>`;
}

async function serve(request, response) {
  const url = new URL(request.url, 'http://127.0.0.1');
  if (request.method === 'GET' && url.pathname === '/shell') {
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
    return response.end('<!doctype html><meta charset="utf-8"><title>Fusion layout fixture</title><h1>Local layout test — do not interact until it finishes</h1>');
  }
  const page = /^\/page\/(qwen|deepseek)$/.exec(url.pathname);
  if (request.method === 'GET' && page) {
    navigationCount.set(page[1], navigationCount.get(page[1]) + 1);
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
    return response.end(fixture(page[1]));
  }
  const model = /^\/api\/(qwen|deepseek)$/.exec(url.pathname);
  if (request.method === 'POST' && model) {
    const chunks = []; let bytes = 0;
    for await (const chunk of request) {
      bytes += chunk.length;
      if (bytes > 32768) { response.writeHead(413); return response.end(); }
      chunks.push(chunk);
    }
    let value;
    try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { response.writeHead(400); return response.end(); }
    requests.get(model[1]).push(value.prompt);
    trace('fixture.http_request', { provider_id: model[1], status: 200, payload: { prompt: value.prompt } });
    response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
    return response.end(JSON.stringify({ html: '<h2>' + model[1] + ' complete</h2><p>Verified local response.</p><pre><code class="language-python">print("ok")\n</code></pre>' }));
  }
  response.writeHead(404); response.end();
}

async function waitFor(predicate, description, timeout = 3000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  assert.fail(`Timed out waiting for ${description}`);
}

async function pageState(id) { return views.get(id).webContents.executeJavaScript('window.fixture'); }

function panes(mode, left = 500) {
  const [width, height] = mainWindow.getContentSize();
  const gap = 8, y = Math.min(60, height - 1);
  const first = Math.max(1, Math.floor((width - gap) * left / 1200));
  return mode === 'tabs'
    ? [{ provider_id: 'deepseek', bounds: { x: 0, y, width, height: height - y } }]
    : [{ provider_id: 'qwen', bounds: { x: 0, y, width: first, height: height - y } },
      { provider_id: 'deepseek', bounds: { x: first + gap, y, width: Math.max(1, width - first - gap), height: height - y } }];
}

async function verifyPreserved(identities, counts) {
  for (const id of ids) {
    const wc = views.get(id).webContents;
    assert.equal(wc.id, identities.get(id), 'Layout switches must reuse the same webContents');
    assert.equal(await wc.executeJavaScript('window.layoutMarker'), `preserved-${id}`, 'Layout switches must preserve page memory');
    assert.equal(navigationCount.get(id), counts.get(id), 'Layout switches must not reload or navigate model pages');
    assert.equal((await pageState(id)).accepted, 1, 'Layout switches must not resend the prompt');
  }
}

async function runPair(round, expectedFocus) {
  const begin = events.length;
  const foregroundViolations = [];
  const prompts = new Map(ids.map(id => [id, `本地 ${round} / ${id}\n请保留 Markdown 与代码。`]));
  const adapters = ids.map(id => {
    const controller = new AbortController(); controllers.set(id, controller);
    const provider = { id, url: `${base}/page/${id}`, selectors: {
      input: ['#prompt-textarea'],
      // Qwen's deliberately stale configured selector exercises its semantic
      // send-button fallback. This is not an asserted live Qwen selector.
      send: id === 'qwen' ? ['#obsolete-local-send-selector'] : ['#local-send-control'],
      assistant: ['[data-role="assistant"]'], stop: ['#stop'], new_chat: id === 'qwen' ? ['#local-new-chat'] : [],
    } };
    return new WebsiteAdapter(views.get(id).webContents, provider, () => {}, (event, fields) => {
      trace(event, fields);
      if (round === 'windows' && event === 'adapter.dispatch' && native) {
        // Adapter log sinks are intentionally exception-isolated. Record the
        // observation here and assert outside the callback so failure is real.
        if (!mainWindow.isFocused()) foregroundViolations.push(id);
      }
    }, { acquireInput: args => layout.acquireInput(args) });
  });
  const result = await Promise.all(adapters.map((adapter, index) => {
    const id = ids[index];
    return adapter.run({ job_id: `fixture-${round}-${id}`, request_id: `fixture-${round}`, provider_id: id,
      prompt: prompts.get(id), purpose: 'candidate', timeout_seconds: 18, stable_seconds: 0.1, min_wait_seconds: 0.1 }, controllers.get(id).signal);
  }));
  for (let index = 0; index < ids.length; index++) {
    const id = ids[index];
    const value = await pageState(id);
    assert.equal(value.clicks, 1, `${id}: one click, with no Enter fallback or repeated gesture`);
    assert.equal(value.accepted, 1, `${id}: the page must actually accept the request`);
    assert.deepEqual(value.rejectedClicks, [], `${id}: no rejected focus/untrusted/incomplete click`);
    assert.equal(value.prompt, prompts.get(id));
    assert.equal(value.clickTrusted, true); assert.equal(value.clickFocus, true);
    assert.equal(value.httpStatus, 200);
    if (id === 'qwen') {
      assert.equal(value.inputs.length, 1, 'Qwen gets one native input operation');
      assert.equal(value.inputs[0].trusted, true); assert.equal(value.inputs[0].focused, true);
    }
    assert.equal(requests.get(id).filter(prompt => prompt === prompts.get(id)).length, 1, 'The localhost model API receives exactly one request per job');
    assert.match(result[index], new RegExp('^## ' + id + ' complete', 'm'));
    assert.match(result[index], /```python\nprint\("ok"\)\n```/);
    assert.doesNotMatch(result[index], /STALE_REPLY|STREAMING_REPLY|FIXTURE_NETWORK_FAILED/);
    assert.equal(adapters[index].active, false);
    const own = events.slice(begin).filter(event => event.provider_id === id);
    assert.equal(own.filter(event => event.event === 'adapter.submission_accepted').length, 1);
    assert.equal(own.filter(event => event.event === 'adapter.focus_emulation' && event.acknowledged).length, 1);
    assert.equal(own.filter(event => event.event === 'adapter.focus_released' && event.acknowledged).length, 1);
    assert.ok(own.findIndex(event => event.event === 'adapter.dispatch') < own.findIndex(event => event.event === 'adapter.focus_released'));
    assert.ok(own.findIndex(event => event.event === 'adapter.focus_released') < own.findIndex(event => event.event === 'adapter.submission_accepted'), 'Release input after the complete gesture so slow acceptance does not block other models');
    assert.ok(own.findIndex(event => event.event === 'adapter.focus_released') < own.findIndex(event => event.event === 'adapter.complete'), 'Focus emulation ends before waiting for reply completion');
    assert.ok(own.some(event => event.event === 'adapter.network_response' && event.status === 200), 'Real CDP observes the localhost HTTP response');
    assert.equal(views.get(id).webContents.debugger.isAttached(), false, 'CDP detaches after completion');
  }
  assert.deepEqual(foregroundViolations, [], 'Background model sending must not activate its operating-system window');
  const own = events.slice(begin);
  assert.equal(own.filter(event => event.event === 'input_lease.acquired').length, 2);
  assert.equal(own.filter(event => event.event === 'input_lease.released').length, 2);
  let leases = 0;
  for (const event of own) {
    if (event.event === 'input_lease.acquired') { leases++; assert.equal(leases, 1, 'Only one native view may own input at a time'); }
    if (event.event === 'input_lease.released') { leases--; assert.equal(leases, 0); }
  }
  assert.equal(layout.lease, null);
  assert.equal(layout.queue.length, 0);
  if (native) await waitFor(() => webContents.getFocusedWebContents()?.id === expectedFocus.id, 'restoration of the previously focused webContents');
  const qwen = views.get('qwen').webContents;
  const inspectComposer = '({ clicks: window.fixture.clicks, value: document.querySelector("textarea").value, active: document.activeElement?.id, focus: document.hasFocus() })';
  const beforeDiagnostic = await qwen.executeJavaScript(inspectComposer);
  const diagnostic = await adapters[0].diagnoseSend();
  assert.equal(diagnostic.sendTarget?.label, '发送', 'Read-only diagnosis identifies the semantically labelled local send control');
  assert.ok(diagnostic.selectorChecks.some(check => check.selector === '#obsolete-local-send-selector' && check.matches === 0));
  assert.deepEqual(await qwen.executeJavaScript(inspectComposer), beforeDiagnostic, 'Send diagnosis must not fill, focus, or click');
  trace('fixture.round_complete', { round, sends: 2, focused_webcontents: webContents.getFocusedWebContents()?.id });
}

async function smoke() {
  await app.whenReady();
  server = http.createServer((request, response) => { serve(request, response).catch(error => { trace('fixture.server_error', { error: error.message }); response.destroy(); }); });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${server.address().port}`;
  const webPreferences = { nodeIntegration: false, contextIsolation: true, sandbox: true,
    backgroundThrottling: false, partition: `fusion-layout-fixture-${process.pid}-${Date.now()}` };
  mainWindow = new BrowserWindow({ width: 1200, height: 720, useContentSize: true, show: false, webPreferences });
  mainWindow.webContents.session.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !details.url.startsWith(base + '/') }));
  layout = new ProviderLayout({ mainWindow, createWindow: options => new BaseWindow(options),
    isBusy: () => false, log: trace, getFocusedWebContents: () => webContents.getFocusedWebContents(),
    onInputInterrupted: job => controllers.get(job.provider_id)?.abort(),
    onStatus: fields => trace('fixture.layout_status', fields) });
  mainWindow.on('resize', () => layout.resume());
  await mainWindow.loadURL(`${base}/shell`);
  for (const id of ids) {
    const view = new WebContentsView({ webPreferences }); views.set(id, view);
    layout.add(id, view, `Local ${id}`);
    view.webContents.on('render-process-gone', (_event, details) => { throw new Error(`Fixture renderer terminated: ${JSON.stringify(details)}`); });
  }
  layout.setLayout({ mode: 'tabs', panes: panes('tabs'), active_provider: 'deepseek', hidden: false });
  mainWindow.show(); mainWindow.focus(); views.get('deepseek').webContents.focus();
  if (native) await waitFor(() => mainWindow.isFocused(), 'initial native window focus');
  assert.equal(views.get('qwen').getVisible(), false, 'Qwen must initially be the background tab');
  assert.equal(views.get('deepseek').getVisible(), true);
  await runPair('tabs', views.get('deepseek').webContents);
  assert.equal(layout.state.active_provider, 'deepseek', 'Input leases must preserve the user-selected tab');
  assert.equal(views.get('qwen').getVisible(), false);
  assert.equal(views.get('deepseek').getVisible(), true);

  const identities = new Map(ids.map(id => [id, views.get(id).webContents.id]));
  const counts = new Map(navigationCount);
  for (const id of ids) await views.get(id).webContents.executeJavaScript(`window.layoutMarker=${JSON.stringify('preserved-' + id)}`);
  layout.setLayout({ mode: 'split', panes: panes('split', 500), active_provider: 'deepseek', hidden: false });
  await verifyPreserved(identities, counts);
  for (const id of ids) assert.equal(views.get(id).getVisible(), true);
  const initial = ids.map(id => views.get(id).getBounds());
  assert.ok(initial[0].x + initial[0].width <= initial[1].x, 'Both split panes have non-overlapping native bounds');
  layout.setLayout({ mode: 'split', panes: panes('split', 720), active_provider: 'deepseek', hidden: false });
  const resized = ids.map(id => views.get(id).getBounds());
  assert.ok(resized[0].width > initial[0].width); assert.ok(resized[1].width < initial[1].width);
  await verifyPreserved(identities, counts);
  trace('fixture.split_complete', { payload: { initial, resized } });

  layout.setLayout({ mode: 'windows', panes: panes('split', 720), active_provider: 'deepseek', hidden: false });
  await verifyPreserved(identities, counts);
  for (const id of ids) {
    const item = layout.entries.get(id);
    assert.notEqual(item.host, mainWindow); assert.equal(item.host, item.window);
    assert.equal(item.view, views.get(id));
    item.window.setContentSize(620, 520);
    await waitFor(() => { const actual = item.view.getBounds(); const size = item.window.getContentSize(); return actual.width === size[0] && actual.height === size[1]; }, 'detached window resize');
  }
  const qwenWindow = layout.entries.get('qwen').window;
  qwenWindow.close();
  await waitFor(() => !qwenWindow.isVisible(), 'close hides the detached provider window');
  assert.equal(qwenWindow.isDestroyed(), false); assert.equal(views.get('qwen').webContents.isDestroyed(), false);
  assert.equal(layout.entries.get('qwen').host, qwenWindow);
  await verifyPreserved(identities, counts);
  layout.setLayout({ mode: 'windows', panes: panes('split', 720), active_provider: 'deepseek', hidden: false, reopen_windows: true });
  assert.equal(layout.entries.get('qwen').window, qwenWindow, 'Reopening reuses the detached native window');
  await waitFor(() => qwenWindow.isVisible(), 'detached window reopen');
  await verifyPreserved(identities, counts);
  mainWindow.show(); mainWindow.focus(); mainWindow.webContents.focus();
  if (native) await waitFor(() => mainWindow.isFocused(), 'main window foreground before background-window send');
  await runPair('windows', mainWindow.webContents);
  if (native) assert.equal(mainWindow.isFocused(), true, 'Background sending must preserve the foreground window');
  for (const id of ids) {
    assert.equal(views.get(id).webContents.id, identities.get(id));
    assert.equal(requests.get(id).length, 2, 'Exactly one request per provider in each of the two rounds');
    assert.equal(await views.get(id).webContents.executeJavaScript('typeof require'), 'undefined');
  }
  const afterWindows = new Map(navigationCount);
  layout.setLayout({ mode: 'tabs', panes: panes('tabs'), active_provider: 'deepseek', hidden: false });
  for (const id of ids) {
    assert.equal(layout.entries.get(id).host, mainWindow);
    assert.equal(views.get(id).webContents.id, identities.get(id));
    assert.equal(navigationCount.get(id), afterWindows.get(id));
  }
  trace('fixture.pass', { mode: native ? 'native' : 'headless', rounds: 2, providers: ids,
    checks: ['trusted_focus_send', 'single_submission', 'http_status', 'markdown', 'lease_restoration', 'split_resize', 'window_reparent', 'window_resize', 'close_preserves_state'] });
  console.log('PASS local Electron layout fixture. This does not validate live Qwen/DeepSeek selectors, login, or remote model availability.');
}

async function finish(code) {
  if (finished) return; finished = true;
  clearTimeout(deadlineTimer);
  for (const controller of controllers.values()) controller.abort();
  try { layout?.shutdown(); } catch (error) { console.error(error.message); code ||= 1; }
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.destroy();
  if (server) { server.closeAllConnections?.(); server.close(); }
  app.exit(code);
}

process.on('uncaughtException', error => { console.error(error); void finish(1); });
process.on('unhandledRejection', error => { console.error(error); void finish(1); });
deadlineTimer = setTimeout(() => { console.error('FAIL: native fixture exceeded its 55-second deadline; no success was recorded.'); void finish(1); }, 55000);
smoke().then(() => finish(0), error => { console.error(error); return finish(1); });
