'use strict';

// Run with: node tests/electron_smoke.cjs
// Only a localhost fixture is loaded. The root-only --no-sandbox below is a
// container test workaround; it never changes the production application.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

if (!process.versions.electron) {
  const { spawnSync } = require('node:child_process');
  const args = ['--ozone-platform=headless', '--disable-gpu', '--disable-dev-shm-usage'];
  if (process.getuid?.() === 0) args.push('--no-sandbox');
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fusion-electron-smoke-'));
  const env = { ...process.env, FUSION_SMOKE_DATA_DIR: tempDir };
  delete env.ELECTRON_RUN_AS_NODE;
  const result = spawnSync(require('electron'), [...args, __filename], {
    stdio: 'inherit', timeout: 40000, env,
  });
  fs.rmSync(tempDir, { recursive: true, force: true });
  if (result.error) console.error(result.error.message);
  if (result.signal) console.error(`Electron smoke process terminated with ${result.signal}; this environment may not support Chromium headless.`);
  process.exit(result.status ?? 1);
}

const { app, BrowserWindow } = require('electron');
const http = require('node:http');
const { WebsiteAdapter, pageAction } = require('../electron/adapter.cjs');
const stateDir = process.env.FUSION_SMOKE_DATA_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'fusion-electron-smoke-'));
app.setPath('userData', stateDir);
app.setPath('sessionData', path.join(stateDir, 'session'));

const selectors = {
  input: ['#prompt'], send: ['#send'], assistant: ['[data-role="assistant"]'],
  stop: ['#stop'], new_chat: [],
};
const output = '<h2>真实 DOM 测试</h2><p>保留<strong>重要内容</strong>和<a href="/source">来源</a>。</p>' +
  '<pre><span>复制代码</span><button>复制</button><code class="language-python">print(&quot;hello&quot;)\n</code></pre>' +
  '<table><thead><tr><th>模型</th><th>结果</th></tr></thead><tbody><tr><td>A</td><td>完成</td></tr></tbody></table>';

function fixture(mode) {
  return `<!doctype html><meta charset="utf-8"><title>Local adapter fixture</title>
    <div data-role="assistant"><p>旧回复，不应被提取</p></div>
    <div data-role="user">用户文本，不应被提取</div>
    <textarea id="prompt"></textarea><button id="send">发送</button>
    <script>
      window.fixture = { count: 0, input: '', inputs: [] };
      document.querySelector('#prompt').addEventListener('input', event => window.fixture.inputs.push(event.target.value));
      document.querySelector('#send').addEventListener('click', () => {
        window.fixture.count++;
        window.fixture.input = document.querySelector('#prompt').value;
        document.querySelector('#prompt').value = '';
        if (${JSON.stringify(mode)} === 'stall') return;
        const stop = document.createElement('button'); stop.id = 'stop'; stop.textContent = '停止'; document.body.append(stop);
        const answer = document.createElement('div'); answer.dataset.role = 'assistant'; answer.textContent = '生成中'; document.body.append(answer);
        setTimeout(() => { answer.innerHTML = ${JSON.stringify(output)}; stop.remove(); }, 950);
      });
    </script>`;
}

let server;
let window;

async function smoke() {
  await app.whenReady();
  server = http.createServer((request, response) => {
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
    response.end(fixture(request.url === '/stall' ? 'stall' : 'complete'));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  window = new BrowserWindow({ show: false, width: 900, height: 700, webPreferences: {
    nodeIntegration: false, contextIsolation: true, sandbox: true, backgroundThrottling: false,
    partition: `fusion-smoke-${process.pid}`,
  } });
  window.webContents.session.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: !details.url.startsWith(base + '/') });
  });

  const states = [];
  const adapter = new WebsiteAdapter(window.webContents, { id: 'fixture', url: `${base}/complete`, selectors }, state => states.push(state));
  const prompt = 'Android 内存分析\n请保留代码、表格和 Markdown。';
  const markdown = await adapter.run({ job_id: 'smoke-complete', prompt, purpose: 'candidate', timeout_seconds: 12, stable_seconds: 0.1, min_wait_seconds: 0.1 });
  const pageState = await window.webContents.executeJavaScript('window.fixture');
  assert.equal(pageState.count, 1, 'One job must click send exactly once');
  assert.equal(pageState.input, prompt, 'Multiline Unicode prompt must survive input insertion');
  assert.deepEqual(pageState.inputs, [prompt], 'The input event must carry the full prompt');
  assert.match(markdown, /^## 真实 DOM 测试/m);
  assert.match(markdown, /```python\nprint\("hello"\)\n```/);
  assert.match(markdown, /\| 模型 \| 结果 \|/);
  assert.match(markdown, new RegExp('\\[来源\\]\\(' + base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '/source\\)'));
  assert.doesNotMatch(markdown, /旧回复|用户文本|生成中|复制代码/);
  assert.ok(states.includes('generating'));
  assert.equal(states.at(-1), 'ready');
  assert.equal(adapter.active, false);
  assert.equal(await window.webContents.executeJavaScript('typeof require'), 'undefined', 'Website fixture must have no Node require');
  assert.equal(await window.webContents.executeJavaScript('typeof window.fusion'), 'undefined', 'Website fixture must have no local application bridge');

  const duplicate = `(${pageAction.toString()})('submit',${JSON.stringify({ id: 'smoke-complete', deadline: Date.now() + 10000, prompt, selectors })})`;
  await assert.rejects(window.webContents.executeJavaScriptInIsolatedWorld(1733, [{ code: duplicate }]), /already submitted/);
  assert.equal(await window.webContents.executeJavaScript('window.fixture.count'), 1);

  const stalled = new WebsiteAdapter(window.webContents, { id: 'fixture', url: `${base}/stall`, selectors });
  await assert.rejects(stalled.run({ job_id: 'smoke-stall', prompt: '只发送一次', purpose: 'candidate', timeout_seconds: 2.5, stable_seconds: 0.1, min_wait_seconds: 0.1 }), error => error.code === 'timeout');
  assert.equal(await window.webContents.executeJavaScript('window.fixture.count'), 1, 'A timed-out job must not resend');
  assert.equal(stalled.active, false);
  console.log('PASS Electron fixture: isolated browser, send once, Markdown code/table/link extraction, stale-output exclusion, timeout without retry.');
}

smoke().then(() => finish(0), error => { console.error(error); finish(1); });

function finish(code) {
  if (window && !window.isDestroyed()) window.destroy();
  if (server) server.close();
  // Flush Electron handles first; only the disposable directory is removed.
  app.once('will-quit', () => { try { fs.rmSync(stateDir, { recursive: true, force: true }); } catch {} });
  app.exit(code);
}
