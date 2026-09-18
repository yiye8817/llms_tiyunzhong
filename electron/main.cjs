'use strict';

const { app, BrowserWindow, BaseWindow, WebContentsView, webContents, session, ipcMain, dialog, clipboard } = require('electron');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { pathToFileURL } = require('node:url');
const { spawn } = require('node:child_process');
const WebSocket = require('ws');
const { WebsiteAdapter } = require('./adapter.cjs');
const { BrowserLoginController, LoginError, runPythonHelper } = require('./browser-login.cjs');
const { createLogger, diskSecrets, lineSink } = require('./diagnostics.cjs');
const { ProviderLayout } = require('./provider-layout.cjs');

const ROOT = path.resolve(__dirname, '..');
const DATA = path.resolve(process.env.FUSION_DATA_DIR || path.join(os.homedir(), '.local/share/multillm-fusion'));
const PORT = Number(process.env.FUSION_PORT || 8765);
if (!Number.isInteger(PORT) || PORT < 1024 || PORT > 65535) throw new Error('FUSION_PORT must be an integer from 1024 to 65535.');
const BASE = `http://127.0.0.1:${PORT}`;
const UI_PATH = path.join(ROOT, 'ui/index.html');
const UI_URL = pathToFileURL(UI_PATH).href;
const PROVIDER_ID = /^[a-z][a-z0-9_-]{0,39}$/;
const JOB_ID = /^[A-Za-z0-9_-]{1,100}$/;
const providers = new Map();
const runningJobs = new Map();
const seenJobs = new Set();
const cancelledJobs = new Set();
let win, socket, child, reconnectTimer, pingTimer, token, config, providerLayout;
let closing = false, shutdownComplete = false, backendExited = false, reconnectAttempt = 0;
let appliedConfigSerial = 0;
let configuration = Promise.resolve();
let browserLogin, pendingConfigRequests = 0, pendingConfigurations = 0;
const startupSecrets = diskSecrets();
const log = createLogger({ component: 'electron', getSecrets: () => [...startupSecrets, token, config?.fusion?.api_key] });
process.env.FUSION_LOG_DIR = log.directory;

fs.mkdirSync(DATA, { recursive: true, mode: 0o700 });
app.setPath('userData', path.join(DATA, 'chromium'));

function notify(payload) {
  if (win && !win.isDestroyed() && !win.webContents.isDestroyed()) win.webContents.send('fusion:status', payload);
}
function send(packet, expectedSocket = socket) {
  if (expectedSocket && expectedSocket === socket && expectedSocket.readyState === WebSocket.OPEN) expectedSocket.send(JSON.stringify(packet));
}
function providerStatus(id, state, message) {
  const provider = providers.get(id);
  if (provider?.status?.state !== state) log('provider_state', { provider_id: id, state });
  if (provider) provider.status = { state, message };
  notify({ type: 'provider', provider_id: id, state, message });
  send({ type: 'status', provider_id: id, state, message });
}
function bridgeStatus(state, message) { log('bridge_state', { state }); notify({ type: 'bridge', state, message }); }

function initializeFiles() {
  const configFile = path.join(DATA, 'config.json');
  try { fs.copyFileSync(path.join(ROOT, 'config.example.json'), configFile, fs.constants.COPYFILE_EXCL); } catch (e) { if (e.code !== 'EEXIST') throw e; }
  fs.chmodSync(configFile, 0o600);
  const tokenFile = path.join(DATA, 'api-key.txt');
  if (!process.env.FUSION_TOKEN) {
    try { fs.writeFileSync(tokenFile, crypto.randomBytes(32).toString('hex') + '\n', { flag: 'wx', mode: 0o600 }); } catch (e) { if (e.code !== 'EEXIST') throw e; }
    fs.chmodSync(tokenFile, 0o600);
  }
  token = (process.env.FUSION_TOKEN || fs.readFileSync(tokenFile, 'utf8')).trim();
  if (token.length < 16 || /[\r\n]/.test(token)) throw new Error('API token must be at least 16 characters, without newlines.');
}

function requestLocal(method, route, body, timeout = 15000, authenticated = true, progressId = undefined) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const headers = { Accept: 'application/json' };
    if (authenticated) headers.Authorization = `Bearer ${token}`;
    if (progressId) headers['X-Fusion-Progress-ID'] = progressId;
    if (payload) { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = payload.length; }
    const req = http.request({ hostname: '127.0.0.1', port: PORT, method, path: route, headers }, res => {
      const chunks = []; let size = 0;
      res.on('data', chunk => {
        size += chunk.length;
        if (size > 32 * 1024 * 1024) { req.destroy(new Error('Backend response exceeds 32 MiB.')); return; }
        chunks.push(chunk);
      });
      res.on('error', reject);
      res.on('end', () => {
        let value;
        try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { return reject(new Error(`Backend returned non-JSON HTTP ${res.statusCode}.`)); }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          const detail = value.error?.message || value.detail || value.message || `HTTP ${res.statusCode}`;
          return reject(new Error(typeof detail === 'string' ? detail : JSON.stringify(detail)));
        }
        resolve(value);
      });
    });
    req.setTimeout(timeout, () => req.destroy(new Error('本地接口请求超时，请检查后端日志。')));
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

async function startBackend() {
  let existing = false;
  try { await requestLocal('GET', '/health', undefined, 1200, false); existing = true; } catch {}
  if (existing) {
    config = await requestLocal('GET', '/internal/config');
    log('backend_reused');
    return;
  }
  const python = process.env.FUSION_PYTHON || path.join(ROOT, '.venv/bin/python');
  if (!fs.existsSync(python) && python.includes('/')) throw new Error(`找不到 Python 环境：${python}\n请先运行 ./run.sh 安装依赖。`);
  child = spawn(python, ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', String(PORT), '--no-access-log'], {
    cwd: ROOT, env: { ...process.env, FUSION_DATA_DIR: DATA, FUSION_TOKEN: token }, stdio: ['ignore', 'ignore', 'pipe'],
  });
  // Python writes its structured events to backend.log itself. Mirror them to the
  // terminal once; capture non-structured startup errors in electron.log.
  const stderr = lineSink(line => {
    try { const record = JSON.parse(line); if (record.component === 'backend' && record.event) { log.forward(line); return; } } catch {}
    if (line.trim()) log('backend_stderr', { detail: line }, 'warn');
  });
  child.stderr.on('data', stderr.write);
  child.stderr.on('end', stderr.end);
  log('backend_spawn', { pid: child.pid, port: PORT });
  let startupError;
  child.on('error', error => { startupError = error; backendExited = true; log('backend_spawn_failed', { code: error.code || 'unknown' }); });
  child.on('exit', (code, signal) => {
    backendExited = true; log('backend_exit', { code, signal });
    if (!closing) bridgeStatus('error', 'Python 后端已退出，请在终端运行 ./run.sh --backend 检查错误后重启。');
  });
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline && !backendExited) {
    try {
      await requestLocal('GET', '/health', undefined, 800, false);
      config = await requestLocal('GET', '/internal/config');
      log('backend_ready');
      return;
    } catch { await new Promise(resolve => setTimeout(resolve, 250)); }
  }
  if (startupError) throw new Error(`Python 启动失败 (${startupError.code || 'unknown'})。请运行 ./run.sh。`);
  throw new Error(`Python 后端未就绪。请确认端口 ${PORT} 未占用，并在终端运行 ./run.sh --backend 查看详细错误。`);
}

function safeWebURL(value) {
  try { const url = new URL(value); return !url.username && !url.password && (url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))); } catch { return false; }
}

function initializeBrowserLogin() {
  browserLogin = new BrowserLoginController({
    getProvider: id => providers.get(id),
    getConnection: () => socket,
    isConnectionCurrent: connection => !!connection && connection === socket && connection.readyState === WebSocket.OPEN && !closing,
    canStart: () => !closing && !runningJobs.size && !pendingConfigRequests && !pendingConfigurations && ![...providers.values()].some(item => item.adapter.active),
    acquire: () => requestLocal('POST', '/internal/browser-maintenance', { active: true }),
    release: lease_id => requestLocal('POST', '/internal/browser-maintenance', { active: false, lease_id }),
    runHelper: (payload, signal) => runPythonHelper({ python: process.env.FUSION_PYTHON || path.join(ROOT, '.venv/bin/python'), root: ROOT, payload, signal }),
    chooseFile: async () => {
      const result = await dialog.showOpenDialog(win, { title: '导入模型登录文件', properties: ['openFile'], filters: [{ name: '模型登录 JSON', extensions: ['json'] }] });
      return result.canceled ? null : result.filePaths[0];
    },
    onBusy: active => notify({ type: 'browser-maintenance', active }),
    // Backend disconnect releases any matching lease. Do not leave a failed release
    // blocking API clients indefinitely or pretend it succeeded in the renderer.
    onReleaseFailure: connection => { if (connection === socket) socket.terminate(); },
  });
}

function layoutProviders() {
  providerLayout?.render();
}

function canOpenRecoveryProvider(id) {
  if (closing || browserLogin?.busy || pendingConfigurations || pendingConfigRequests || !providers.get(id)?.adapter.active) return false;
  return [...runningJobs.values()].some(job => job.provider_id === id && job.manualRecovery === true && !job.controller.signal.aborted);
}

async function createProvider(provider) {
  if (!PROVIDER_ID.test(provider.id) || !safeWebURL(provider.url)) throw new Error('无效的模型 ID 或 URL。');
  const ses = session.fromPartition(`persist:fusion-${provider.id}`);
  await ses.setProxy(provider.proxy ? { mode: 'fixed_servers', proxyRules: provider.proxy } : { mode: 'system' });
  await ses.closeAllConnections();
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);
  ses.on('will-download', event => event.preventDefault());
  const view = new WebContentsView({ webPreferences: {
    session: ses, nodeIntegration: false, nodeIntegrationInSubFrames: false,
    contextIsolation: true, sandbox: true, webSecurity: true, allowRunningInsecureContent: false,
    backgroundThrottling: false, spellcheck: false, navigateOnDragDrop: false,
  } });
  view.setVisible(false);
  const wc = view.webContents;
  // Remote windows cannot inherit the local preload. Keep sign-in navigation in its tab.
  wc.setWindowOpenHandler(({ url }) => {
    if (safeWebURL(url) && !browserLogin?.busy && !providers.get(provider.id)?.adapter.active) void wc.loadURL(url).catch(() => providerStatus(provider.id, 'error', '登录页面打开失败。'));
    return { action: 'deny' };
  });
  wc.on('will-navigate', (event, url) => { if (!safeWebURL(url)) event.preventDefault(); });
  wc.on('will-redirect', (event, url) => { if (!safeWebURL(url)) event.preventDefault(); });
  wc.on('will-attach-webview', event => event.preventDefault());
  wc.on('did-finish-load', () => {
    log('provider_loaded', { provider_id: provider.id });
    if (!browserLogin?.busy && !providers.get(provider.id)?.adapter.active) providerStatus(provider.id, 'ready', '网页已打开，请确认已登录。');
  });
  wc.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
    if (isMainFrame && code !== -3) { log('provider_load_failed', { provider_id: provider.id, code }); providerStatus(provider.id, 'error', `网页加载失败 (${code})，请检查代理与网络并刷新。`); }
  });
  wc.on('render-process-gone', (_event, details) => {
    log('provider_renderer_gone', { provider_id: provider.id, reason: details.reason });
    for (const job of runningJobs.values()) if (job.provider_id === provider.id) job.controller.abort();
    providerStatus(provider.id, 'error', '网页进程已退出，请刷新该标签。');
  });
  wc.on('unresponsive', () => log('provider_unresponsive', { provider_id: provider.id }));
  wc.on('responsive', () => log('provider_responsive', { provider_id: provider.id }));
  const item = { view, provider, status: { state: 'loading', message: '正在打开网页…' }, pendingLoad: null };
  item.adapter = new WebsiteAdapter(wc, provider, (state, message) => {
    for (const job of runningJobs.values()) if (job.provider_id === provider.id) job.manualRecovery = ['manual_retry_required', 'verification_required'].includes(state);
    providerStatus(provider.id, state, message);
  },
    (event, fields) => log(event, { ...fields, provider_id: provider.id }),
    { acquireInput: request => providerLayout.acquireInput(request),
      // The Qwen copy-button fallback reads only the current system clipboard
      // after a trusted page gesture; no clipboard contents are logged here.
      readClipboard: () => clipboard.readText() });
  providers.set(provider.id, item);
  providerLayout.add(provider.id, view, provider.name);
  layoutProviders();
  // Keep the initial navigation promise so a request cannot start a second
  // navigation while this page is still loading. The result is normalized to
  // avoid an unhandled rejection; adapter.run will retry a failed navigation.
  item.pendingLoad = wc.loadURL(provider.url).then(
    () => ({ ok: true }),
    error => ({ ok: false, error }),
  );
  return item;
}

async function waitForProviderLoad(item, timeoutMs = 45000) {
  const pending = item?.pendingLoad;
  if (!pending) return;
  item.pendingLoad = null;
  let timer;
  const timeout = new Promise(resolve => {
    timer = setTimeout(() => resolve({ ok: false, timedOut: true }), timeoutMs);
  });
  const result = await Promise.race([pending, timeout]);
  clearTimeout(timer);
  if (result?.timedOut) {
    try { item.view.webContents.stop(); } catch {}
    log('provider_load_wait_timeout', { provider_id: item.provider.id, timeout_ms: timeoutMs });
  } else if (result && result.ok === false) {
    log('provider_load_wait_failed', { provider_id: item.provider.id, error: result.error?.message || 'navigation_failed' });
  }
}

async function applyConfig(next) {
  if (browserLogin?.busy) throw new LoginError('BUSY');
  if (!next || !Array.isArray(next.providers)) throw new Error('Backend configuration is invalid.');
  if (runningJobs.size) throw new Error('Cannot reconfigure while website jobs are running.');
  const enabled = next.providers.filter(provider => provider.enabled);
  const ids = new Set(enabled.map(provider => provider.id));
  for (const [id, item] of providers) {
    if (!ids.has(id)) { providerLayout.remove(id); providers.delete(id); }
  }
  for (const provider of enabled) {
    if (!PROVIDER_ID.test(provider.id) || !safeWebURL(provider.url)) throw new Error('Invalid provider configuration.');
    const existing = providers.get(provider.id);
    if (!existing) await createProvider(provider);
    else {
      const networkChanged = existing.provider.url !== provider.url || existing.provider.proxy !== provider.proxy;
      if (networkChanged) {
        const ses = existing.view.webContents.session;
        await ses.setProxy(provider.proxy ? { mode: 'fixed_servers', proxyRules: provider.proxy } : { mode: 'system' });
        await ses.closeAllConnections();
      }
      existing.provider = provider;
      existing.adapter.provider = provider;
      providerLayout.setTitle(provider.id, provider.name);
      if (networkChanged) {
        existing.pendingLoad = existing.view.webContents.loadURL(provider.url).then(
          () => ({ ok: true }),
          error => ({ ok: false, error }),
        );
      }
    }
  }
  config = next;
  log('config_applied', { providers: enabled.map(item => item.id), fusion_mode: next.fusion?.mode, fusion_provider: next.fusion?.provider });
  appliedConfigSerial++;
  layoutProviders();
}

function validJob(job) {
  return JOB_ID.test(job.job_id || '') && PROVIDER_ID.test(job.provider_id || '') && typeof job.prompt === 'string' && job.prompt.length > 0 && job.prompt.length <= 4000000 && Buffer.byteLength(job.prompt, 'utf8') <= 8000000 &&
    (job.request_source === undefined || ['fusion_chat','api'].includes(job.request_source)) &&
    ['candidate', 'fusion'].includes(job.purpose) && Number.isFinite(job.timeout_seconds) && job.timeout_seconds >= 1 && job.timeout_seconds <= 1800 &&
    (job.submission_timeout_seconds === undefined || (Number.isFinite(job.submission_timeout_seconds) && job.submission_timeout_seconds >= 15 && job.submission_timeout_seconds <= 600)) &&
    (job.recovery_timeout_seconds === undefined || (Number.isFinite(job.recovery_timeout_seconds) && job.recovery_timeout_seconds >= 0 && job.recovery_timeout_seconds <= 600)) &&
    (job.input_chunk_chars === undefined || (Number.isInteger(job.input_chunk_chars) && job.input_chunk_chars >= 256 && job.input_chunk_chars <= 16384)) &&
    (job.input_chunk_delay_ms === undefined || (Number.isInteger(job.input_chunk_delay_ms) && job.input_chunk_delay_ms >= 0 && job.input_chunk_delay_ms <= 1000)) &&
    (job.submit_settle_seconds === undefined || (Number.isFinite(job.submit_settle_seconds) && job.submit_settle_seconds >= 0 && job.submit_settle_seconds <= 10)) &&
    (job.access_interval_seconds === undefined || (Number.isFinite(job.access_interval_seconds) && job.access_interval_seconds >= 0 && job.access_interval_seconds <= 3600)) &&
    (job.total_timeout_seconds === undefined || (Number.isFinite(job.total_timeout_seconds) && job.total_timeout_seconds > 0 && job.total_timeout_seconds <= 1200)) &&
    Number.isFinite(job.stable_seconds) && job.stable_seconds >= 1 && job.stable_seconds <= 120 && Number.isFinite(job.min_wait_seconds) && job.min_wait_seconds >= 0 && job.min_wait_seconds <= 300;
}

async function generate(job, connectedSocket) {
  await configuration;
  if (connectedSocket !== socket || connectedSocket.readyState !== WebSocket.OPEN) return;
  if (cancelledJobs.delete(job.job_id)) return;
  if (!validJob(job)) { send({ type: 'error', job_id: job.job_id, provider_id: job.provider_id, error: { code: 'invalid_job', message: '无效的网页生成任务。' } }, connectedSocket); return; }
  if (browserLogin?.busy) { send({ type: 'error', job_id: job.job_id, provider_id: job.provider_id, error: { code: 'browser_maintenance', message: '正在导入浏览器登录，请稍后重试。' } }, connectedSocket); return; }
  if (seenJobs.has(job.job_id)) { log('duplicate_job_ignored', { job_id: job.job_id }); return; }
  seenJobs.add(job.job_id);
  if (seenJobs.size > 10000) { log('job_limit'); connectedSocket.close(1012, 'Restart bridge after 10000 jobs'); return; }
  const item = providers.get(job.provider_id);
  if (!item || item.adapter.active) { send({ type: 'error', job_id: job.job_id, provider_id: job.provider_id, error: { code: 'provider_unavailable', message: '模型未启用或已有任务。' } }, connectedSocket); return; }
  const controller = new AbortController();
  runningJobs.set(job.job_id, { controller, provider_id: job.provider_id, manualRecovery: false });
  const started = Date.now();
  log('job_start', { job_id: job.job_id, request_id: job.request_id, provider_id: job.provider_id, purpose: job.purpose, request_source: job.request_source || 'unknown', retry_stages: job.qwen_retry_stages !== false, recovery_seconds: job.recovery_timeout_seconds, total_timeout_seconds: job.total_timeout_seconds, prompt_chars: job.prompt.length, timeout_seconds: job.timeout_seconds });
  try {
    // applyConfig can finish before the hidden provider view's first load.
    // Wait for that navigation before adapter.run performs its deliberate
    // task navigation; otherwise two loadURL calls can cancel each other and
    // surface as a misleading candidate_generation_failed/502.
    await waitForProviderLoad(item);
    const markdown = await item.adapter.run(job, controller.signal, progress => {
      send({ type: 'progress', job_id: job.job_id, provider_id: job.provider_id, ...progress }, connectedSocket);
    });
    send({ type: 'result', job_id: job.job_id, provider_id: job.provider_id, markdown }, connectedSocket);
    log('job_complete', { job_id: job.job_id, request_id: job.request_id, provider_id: job.provider_id, markdown_chars: markdown.length, elapsed_ms: Date.now() - started });
  } catch (error) {
    send({ type: 'error', job_id: job.job_id, provider_id: job.provider_id, error: { code: String(error.code || 'webpage_error'), message: error.message || '网页生成失败。', ...(error.details ? { details: error.details } : {}) } }, connectedSocket);
    log('job_error', { job_id: job.job_id, request_id: job.request_id, provider_id: job.provider_id, code: String(error.code || 'webpage_error'), elapsed_ms: Date.now() - started, payload: { error: error.message || '网页生成失败。', ...(error.details ? { details: error.details } : {}) } });
  } finally { runningJobs.delete(job.job_id); }
}

function connectBridge() {
  if (closing) return;
  // A synthesis prompt permits 1.5M Unicode characters; UTF-8 and JSON escaping
  // exceed 4MB. Match uvicorn's frame cap while still checking prompt bytes above.
  const ws = new WebSocket(`ws://127.0.0.1:${PORT}/internal/bridge`, { headers: { Authorization: `Bearer ${token}` }, handshakeTimeout: 10000, maxPayload: 16 * 1024 * 1024 });
  socket = ws;
  let alive = true;
  ws.on('open', () => {
    reconnectAttempt = 0;
    bridgeStatus('connecting', '正在应用网页配置…');
    pingTimer = setInterval(() => { if (!alive) return ws.terminate(); alive = false; ws.ping(); }, 15000);
  });
  ws.on('pong', () => { alive = true; });
  ws.on('message', data => {
    let packet;
    try { packet = JSON.parse(data.toString('utf8')); } catch { log('invalid_bridge_packet'); return; }
    if (packet.type === 'config') {
      pendingConfigurations++;
      configuration = configuration.catch(() => {}).then(async () => {
        try {
          // A reconnect can arrive while an aborted helper is winding down. Finish
          // its local cleanup before applying the backend's fresh configuration.
          const deadline = Date.now() + 20000;
          while (browserLogin?.busy && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50));
          await applyConfig(packet.config);
          // Python pauses dispatch while settings change. Acknowledge only after sessions,
          // proxies and adapter definitions are ready, on initial connect and every update.
          send({ type: 'ready' }, ws);
          for (const [id, item] of providers) send({ type: 'status', provider_id: id, ...item.status }, ws);
          bridgeStatus('connected', '网页桥接已连接。');
        } finally { pendingConfigurations--; }
      });
      configuration.catch(() => bridgeStatus('error', '模型配置应用失败，请完成当前任务后重新保存配置。'));
    } else if (packet.type === 'generate') {
      void generate(packet, ws).catch(() => send({ type: 'error', job_id: packet.job_id, provider_id: packet.provider_id, error: { code: 'configuration_error', message: '网页配置未就绪。' } }, ws));
    } else if (packet.type === 'cancel') {
      const running = runningJobs.get(packet.job_id);
      if (running) running.controller.abort();
      else if (JOB_ID.test(packet.job_id || '')) {
        cancelledJobs.add(packet.job_id);
        if (cancelledJobs.size > 10000) cancelledJobs.delete(cancelledJobs.values().next().value);
      }
    }
  });
  ws.on('error', () => log('bridge_connection_error'));
  ws.on('close', () => {
    clearInterval(pingTimer);
    if (socket !== ws) return;
    browserLogin?.cancel();
    socket = undefined;
    for (const job of runningJobs.values()) job.controller.abort();
    if (!closing) {
      bridgeStatus('disconnected', '桥接连接中断，正在重连；进行中的请求不会自动重发。');
      const delay = Math.min(15000, 500 * 2 ** reconnectAttempt++) + Math.random() * 250;
      reconnectTimer = setTimeout(connectBridge, delay);
    }
  });
}

function verifySender(event) {
  if (!win || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame || event.senderFrame.url !== UI_URL) throw new Error('Untrusted IPC sender.');
}
function ipc(name, handler) {
  ipcMain.handle(`fusion:${name}`, async (event, payload) => { verifySender(event); return handler(payload); });
}
function registerIPC() {
  ipc('request', async payload => {
    if (!payload || !['GET', 'POST', 'PUT'].includes(payload.method) || typeof payload.path !== 'string') throw new Error('Invalid API request.');
    const routes = {
      GET: /^(?:\/health|\/internal\/(?:config|status|history(?:\/[a-zA-Z0-9_-]{1,100})?)|\/v1\/models|\/v1\/progress\/[0-9a-f-]{36}(?:\?after=\d{1,7})?)$/,
      POST: /^(?:\/v1\/chat\/completions|\/internal\/cancel)$/,
      PUT: /^\/internal\/config$/,
    };
    if (!routes[payload.method].test(payload.path)) throw new Error('This API route is not exposed to the UI.');
    if (payload.progressId !== undefined && (payload.method !== 'POST' || payload.path !== '/v1/chat/completions' ||
        typeof payload.progressId !== 'string' || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(payload.progressId))) throw new Error('Invalid progress correlation ID.');
    if (JSON.stringify(payload.body ?? null).length > 3000000) throw new Error('Request is too large.');
    if (browserLogin?.busy && ((payload.method === 'PUT' && payload.path === '/internal/config') || payload.path === '/v1/chat/completions')) throw new LoginError('BUSY');
    if (payload.path === '/v1/chat/completions' && payload.body?.stream) throw new Error('Desktop renderer uses non-stream responses.');
    const priorConfigSerial = appliedConfigSerial;
    const changingConfig = payload.method === 'PUT' && payload.path === '/internal/config';
    if (changingConfig) pendingConfigRequests++;
    let response;
    try { response = await requestLocal(payload.method, payload.path, payload.body, payload.path === '/v1/chat/completions' ? 3600000 : 15000, payload.path !== '/health', payload.progressId); }
    finally { if (changingConfig) pendingConfigRequests--; }
    if (payload.method === 'PUT' && payload.path === '/internal/config') {
      // The HTTP response can beat WS configuration delivery. The UI immediately selects
      // newly enabled tabs, so don't resolve until their views and proxies really exist.
      const wanted = JSON.stringify(response);
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        if (appliedConfigSerial > priorConfigSerial && JSON.stringify(config) === wanted) {
          await configuration;
          if (JSON.stringify(config) === wanted) return response;
        }
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      throw new Error('配置已保存，但网页配置尚未应用完成。请等待桥接恢复后重新打开设置。');
    }
    return response;
  });
  ipc('showProvider', id => { if (typeof id !== 'string' || !providers.has(id)) throw new Error('Unknown provider.'); return providerLayout.showProvider(id); });
  ipc('openRecoveryProvider', id => {
    if (typeof id !== 'string' || !providers.has(id) || !canOpenRecoveryProvider(id)) throw new Error('该模型当前没有等待人工重试的活动任务。');
    return providerLayout.showRecoveryProvider(id);
  });
  ipc('setBounds', bounds => providerLayout.setBounds(bounds));
  ipc('setLayout', payload => providerLayout.setLayout(payload));
  ipc('diagnoseSend', async id => {
    if (typeof id !== 'string' || !providers.has(id)) throw new Error('Unknown provider.');
    const item = providers.get(id);
    if (browserLogin?.busy || item.adapter.active) throw new Error('该模型正在执行任务，请结束后检测发送按钮。');
    return item.adapter.diagnoseSend();
  });
  ipc('reloadProvider', async id => {
    if (browserLogin?.busy) throw new LoginError('BUSY');
    if (typeof id !== 'string' || !providers.has(id)) throw new Error('Unknown provider.');
    const item = providers.get(id);
    if (item.adapter.active) throw new Error('该模型正在生成，暂时不能刷新。');
    await item.view.webContents.loadURL(item.provider.url);
    return { ok: true };
  });
  ipc('listBrowserProfiles', () => browserLogin.listProfiles());
  const importLogin = async (method, payload) => {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new LoginError('INVALID_REQUEST');
    const report = await browserLogin[method](payload);
    if (!report.canceled) providerStatus(report.provider_id, 'ready', `已导入 ${report.imported} 项 Cookie；请在网页确认登录。`);
    return report;
  };
  ipc('importBrowserLogin', payload => importLogin('importBrowser', payload));
  ipc('importLoginFile', payload => importLogin('importFile', payload));
  ipc('saveMarkdown', async payload => {
    if (!payload || typeof payload.content !== 'string' || payload.content.length > 16000000 || typeof payload.name !== 'string') throw new Error('Invalid Markdown export.');
    const name = path.basename(payload.name).replace(/[^\p{L}\p{N}_. -]/gu, '_').slice(0, 100) || 'reply.md';
    const result = await dialog.showSaveDialog(win, { defaultPath: name.endsWith('.md') ? name : `${name}.md`, filters: [{ name: 'Markdown', extensions: ['md'] }] });
    if (result.canceled || !result.filePath) return { canceled: true };
    await fsp.writeFile(result.filePath, payload.content, { mode: 0o600 });
    return { canceled: false, filePath: result.filePath };
  });
  ipc('copyText', async text => { if (typeof text !== 'string' || text.length > 16000000) throw new Error('Invalid clipboard text.'); await clipboard.writeText(text); });
  ipc('runtimeInfo', () => ({ baseUrl: `${BASE}/v1`, token, dataDir: DATA, version: app.getVersion() }));
}

async function shutdown() {
  closing = true;
  browserLogin?.cancel();
  clearTimeout(reconnectTimer); clearInterval(pingTimer);
  for (const job of runningJobs.values()) job.controller.abort();
  if (socket) { socket.removeAllListeners('close'); socket.terminate(); socket = undefined; }
  for (const item of providers.values()) {
    try { item.view.webContents.session.flushStorageData(); } catch {}
  }
  providerLayout?.shutdown();
  providers.clear();
  if (child && !backendExited) {
    child.kill('SIGTERM');
    await new Promise(resolve => {
      const timer = setTimeout(() => { if (!backendExited) child.kill('SIGKILL'); resolve(); }, 3000);
      child.once('exit', () => { clearTimeout(timer); resolve(); });
    });
  }
  log('shutdown'); shutdownComplete = true;
}

const locked = app.requestSingleInstanceLock();
if (!locked) app.quit();
else {
  app.on('second-instance', () => { if (win && !win.isDestroyed()) { if (win.isMinimized()) win.restore(); win.focus(); } });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', event => {
    if (shutdownComplete) return;
    event.preventDefault();
    if (!closing) void shutdown().finally(() => app.quit());
  });
  app.whenReady().then(async () => {
    initializeFiles();
    log('app_start', { version: app.getVersion(), electron: process.versions.electron, chrome: process.versions.chrome, node: process.versions.node, platform: process.platform, arch: process.arch, log_dir: log.directory, data_dir: DATA, no_sandbox: app.commandLine.hasSwitch('no-sandbox') });
    await startBackend();
    initializeBrowserLogin();
    registerIPC();
    win = new BrowserWindow({ width: 1500, height: 980, minWidth: 1000, minHeight: 680, title: 'MultiLLM Fusion', backgroundColor: '#101521',
      webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, navigateOnDragDrop: false },
    });
    win.setMenuBarVisibility(false);
    providerLayout = new ProviderLayout({ mainWindow: win, createWindow: options => new BaseWindow(options),
      isBusy: () => !!(runningJobs.size || browserLogin?.busy || pendingConfigurations || pendingConfigRequests),
      isMaintenance: () => !!(browserLogin?.busy || pendingConfigurations || pendingConfigRequests),
      canRecover: canOpenRecoveryProvider,
      onStatus: notify, log,
      getFocusedWebContents: () => webContents.getFocusedWebContents(),
      onInputInterrupted: (job, reason) => runningJobs.get(job.job_id)?.controller.abort(reason),
    });
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (event, url) => { if (url !== UI_URL) event.preventDefault(); });
    win.webContents.on('will-attach-webview', event => event.preventDefault());
    win.on('resize', layoutProviders);
    win.on('restore', () => providerLayout.resume());
    win.on('minimize', () => { if (providerLayout.state.mode !== 'windows') providerLayout.suspend(); });
    // Auxiliary windows may still exist, so closing the control window explicitly
    // runs the normal shutdown path instead of waiting for window-all-closed.
    win.on('close', event => { if (!closing) { event.preventDefault(); app.quit(); } });
    win.webContents.on('did-finish-load', () => {
      for (const [id, item] of providers) notify({ type: 'provider', provider_id: id, ...item.status });
      bridgeStatus(socket?.readyState === WebSocket.OPEN ? 'connected' : 'connecting', '请先在两个网页标签中手动登录，再发送消息。');
    });
    await applyConfig(config);
    await win.loadFile(UI_PATH);
    connectBridge();
  }).catch(error => {
    log('startup_error', { code: error.code || 'startup_failed' });
    dialog.showErrorBox('MultiLLM Fusion 启动失败', error.message || '未知错误，请从终端启动以检查依赖。');
    app.quit();
  });
}
