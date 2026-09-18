'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { BrowserLoginController, normalizedCookie, domainAllowed, validateSnapshot, runPythonHelper,
  helperEnvironment, storageAction, MAX_SNAPSHOT, MAX_STORAGE } = require('../electron/browser-login.cjs');

const cookie = (overrides = {}) => ({ name: 'fixture-session', value: 'FIXTURE_SECRET_DO_NOT_REPORT', domain: 'chatgpt.com', path: '/',
  secure: true, httpOnly: true, hostOnly: true, session: true, sameSite: 'lax', ...overrides });
const snapshot = (overrides = {}) => JSON.stringify({ format: 'multillm-fusion-login', version: 1, provider_id: 'chatgpt',
  source: 'extension', created_at: '2026-01-01T00:00:00Z', cookies: [cookie()], storage: [], ...overrides });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

function fixture(extra = {}) {
  const events = [], stored = new Map(), connection = {};
  let connected = true, controller, currentURL = 'https://chatgpt.com/';
  const sandbox = vm.createContext({ Date, location: { origin: 'https://chatgpt.com', protocol: 'https:' },
    localStorage: { setItem: (key, value) => stored.set(key, value) } });
  const wc = {
    isDestroyed: () => false, stop: () => events.push('stop'), getURL: () => currentURL,
    loadURL: async url => { events.push(`load:${url}`); currentURL = extra.redirect && url !== 'about:blank' ? extra.redirect : url;
      const parsed = new URL(currentURL); sandbox.location.origin = parsed.origin; sandbox.location.protocol = parsed.protocol;
      if (extra.onLoad) await extra.onLoad(url); },
    executeJavaScriptInIsolatedWorld: async (world, scripts) => {
      assert.equal(world, 998); assert.equal(scripts.length, 1);
      const code = scripts[0].code;
      const action = /\)\("(prepare|apply|revoke)"/.exec(code)?.[1];
      events.push(`script:${action}`);
      const result = vm.runInContext(code, sandbox);
      if (extra.onScript) await extra.onScript(action, controller);
      return result;
    },
    session: { cookies: {
      set: async details => { events.push({ cookie: details }); if (extra.onSet) await extra.onSet(details, controller); },
      flushStore: async () => { events.push('flush'); },
    }, flushStorageData: () => events.push('flushStorage') },
  };
  const item = { provider: { id: 'chatgpt', enabled: true, url: 'https://chatgpt.com/' }, adapter: { active: false }, view: { webContents: wc } };
  const options = {
    getProvider: id => id === item.provider.id ? item : undefined,
    getConnection: () => connection,
    isConnectionCurrent: value => connected && value === connection,
    canStart: () => true,
    acquire: async () => { events.push('acquire'); return { active: true, lease_id: 'fixture-lease' }; },
    release: async lease => { assert.equal(lease, 'fixture-lease'); events.push('release'); },
    runHelper: async request => { events.push(`helper:${request.action}`); return { ok: true, provider_id: 'chatgpt', cookies: [cookie()], skipped: 0, warnings: [] }; },
    chooseFile: async () => 'fixture.json', readSnapshot: async () => snapshot(),
    onBusy: active => events.push(`busy:${active}`),
    ...extra.options,
  };
  controller = new BrowserLoginController(options);
  return { controller, events, stored, item, wc, sandbox, disconnect: () => { connected = false; controller.cancel(); } };
}
const importArgs = { profile_id: 'fixture-profile', provider_id: 'chatgpt' };

test('scope compares domain labels and rejects unrelated account domains', () => {
  assert.equal(domainAllowed('.CHATGPT.COM', 'chatgpt'), true);
  assert.equal(domainAllowed('auth.openai.com', 'chatgpt'), true);
  for (const host of ['evilchatgpt.com', 'chatgpt.com.evil.invalid', 'google.com', 'github.com', 'chatgpt.com.', '..chatgpt.com', 'chatgpt.com/path'])
    assert.equal(domainAllowed(host, 'chatgpt'), false, host);
  assert.equal(domainAllowed('chatgpt.com', 'deepseek'), false);
  assert.equal(domainAllowed('.chatglm.cn', 'glm'), true);
  assert.equal(domainAllowed('chat.z.ai', 'glm'), true);
  assert.equal(domainAllowed('www.kimi.com', 'kimi'), true);
  assert.equal(domainAllowed('evilkimi.com', 'kimi'), false);
  assert.equal(domainAllowed('chatglm.cn.evil.invalid', 'glm'), false);
  assert.equal(domainAllowed('z.ai.evil.invalid', 'glm'), false);
});

test('host-only session Cookie retains flags and omits domain and expiry', () => {
  const { details } = normalizedCookie(cookie({ expirationDate: 1 }), 'chatgpt');
  assert.equal(details.url, 'https://chatgpt.com/');
  assert.equal(details.secure, true); assert.equal(details.httpOnly, true); assert.equal(details.sameSite, 'lax');
  assert.equal(Object.hasOwn(details, 'domain'), false); assert.equal(Object.hasOwn(details, 'expirationDate'), false);
});

test('domain cookie retains domain scope and persistent expiration', () => {
  const expiry = Date.now() / 1000 + 10000;
  const { details } = normalizedCookie(cookie({ domain: '.openai.com', hostOnly: false, session: false, expirationDate: expiry, secure: false, path: '/a?b#c', sameSite: 'strict' }), 'chatgpt');
  assert.equal(details.domain, '.openai.com'); assert.equal(details.expirationDate, expiry);
  assert.equal(details.url, 'http://openai.com/'); assert.equal(details.path, '/a?b#c');
});

test('isolated, expired, malformed and insecure prefixed cookies are skipped', () => {
  for (const change of [{ partitionKey: { topLevelSite: 'https://other.invalid' } }, { originAttributes: '^userContextId=1' },
    { storeId: 'firefox-container-1' }, { partitioned: true }, { session: false, expirationDate: 0 }, { httpOnly: 'yes' },
    { name: '__Secure-fixture', secure: false }, { name: '__Host-fixture', hostOnly: false }, { sameSite: 'no_restriction', secure: false },
    { domain: 'chatgpt.com.evil.invalid' }, { value: 'bad\nvalue' }]) assert.equal(normalizedCookie(cookie(change), 'chatgpt').details, undefined);
});

test('snapshot limits and model binding are enforced before processing', () => {
  assert.equal(validateSnapshot(snapshot(), 'chatgpt').cookies.length, 1);
  assert.throws(() => validateSnapshot(snapshot(), 'deepseek'), { code: 'INVALID_SNAPSHOT' });
  assert.throws(() => validateSnapshot('x'.repeat(MAX_SNAPSHOT + 1), 'chatgpt'), { code: 'SNAPSHOT_TOO_LARGE' });
  assert.throws(() => validateSnapshot(snapshot({ storage: [{ origin: 'https://chatgpt.com', localStorage: { fixture: 'x'.repeat(MAX_STORAGE) } }] }), 'chatgpt'), { code: 'SNAPSHOT_TOO_LARGE' });
  assert.throws(() => validateSnapshot(snapshot({ storage: [{ origin: 'https://chatgpt.com', localStorage: { fixture: 1 } }] }), 'chatgpt'), { code: 'INVALID_SNAPSHOT' });
});

test('lease precedes extraction, blank precedes merge, reports contain no session values', async () => {
  const f = fixture();
  const report = await f.controller.importBrowser(importArgs);
  assert.deepEqual(f.events.slice(0, 5), ['busy:true', 'acquire', 'helper:extract', 'stop', 'load:about:blank']);
  assert.equal(f.events[5].cookie.name, 'fixture-session');
  assert.deepEqual(f.events.slice(6), ['flush', 'load:https://chatgpt.com/', 'release', 'busy:false']);
  assert.equal(report.imported, 1); assert.equal(report.authenticated, false); assert.equal(report.storage_imported, 0);
  assert.doesNotMatch(JSON.stringify(report), /FIXTURE_SECRET|fixture-session/);
  assert.equal(f.controller.busy, false);
  // The fake session intentionally has no clearStorageData/remove API.
});

test('overlapping imports and rejected backend leases cannot read profiles', async () => {
  const waiting = deferred();
  const f = fixture({ options: { acquire: () => waiting.promise } });
  const one = f.controller.importBrowser(importArgs);
  await assert.rejects(f.controller.importBrowser(importArgs), { code: 'BUSY' });
  waiting.reject(new Error('backend detail must stay private'));
  await assert.rejects(one, { code: 'MAINTENANCE_FAILED' });
  assert.equal(f.events.some(item => item === 'helper:extract'), false);
  assert.equal(f.controller.busy, false);
});

test('invalid configured host is rejected before lease and extraction', async () => {
  const f = fixture(); f.item.provider.url = 'https://chatgpt.com.evil.invalid/';
  await assert.rejects(f.controller.importBrowser(importArgs), { code: 'INVALID_PROVIDER' });
  assert.deepEqual(f.events, []);
});

test('disconnect aborts a pending helper and refuses late cookie results', async () => {
  const waiting = deferred();
  const f = fixture({ options: { runHelper: () => waiting.promise } });
  const pending = f.controller.importBrowser(importArgs);
  await new Promise(resolve => setImmediate(resolve));
  f.disconnect();
  await assert.rejects(pending, { code: 'IMPORT_ABORTED' });
  waiting.resolve({ ok: true, provider_id: 'chatgpt', cookies: [cookie()], warnings: [] });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.events.some(item => typeof item === 'object'), false);
  assert.equal(f.controller.busy, false); assert.ok(f.events.includes('release'));
});

test('cancellation stops before the next Cookie and flush/navigation mutation', async () => {
  const f = fixture({ onSet: (_details, controller) => controller.cancel(), options: { runHelper: async () => ({ ok: true, provider_id: 'chatgpt', cookies: [cookie(), cookie({ name: 'second' })], warnings: [] }) } });
  await assert.rejects(f.controller.importBrowser(importArgs), { code: 'IMPORT_ABORTED' });
  assert.equal(f.events.filter(item => typeof item === 'object').length, 1);
  assert.equal(f.events.includes('flush'), false); assert.equal(f.events.includes('load:https://chatgpt.com/'), false);
});

test('Cookie setter error is sanitized and other scoped Cookies continue', async () => {
  let calls = 0;
  const f = fixture({ onSet: () => { if (!calls++) throw new Error('FIXTURE_SECRET_DO_NOT_REPORT'); }, options: {
    runHelper: async () => ({ ok: true, provider_id: 'chatgpt', cookies: [cookie(), cookie({ name: 'second' }), cookie({ domain: 'evil.invalid' })], warnings: [{ code: 'whatever', message: 'ANOTHER_SECRET' }] }),
  } });
  const report = await f.controller.importBrowser(importArgs);
  assert.equal(report.imported, 1); assert.equal(report.skipped, 2);
  assert.doesNotMatch(JSON.stringify(report), /SECRET|fixture-session|evil.invalid/);
  assert.ok(report.warnings.some(item => item.code === 'COOKIE_SET_FAILED'));
});

test('file cancellation releases lease without reading a file or changing target', async () => {
  const f = fixture({ options: { chooseFile: async () => null, readSnapshot: () => { throw new Error('must not read'); } } });
  assert.deepEqual(await f.controller.importFile({ provider_id: 'chatgpt' }), { canceled: true });
  assert.deepEqual(f.events, ['busy:true', 'acquire', 'release', 'busy:false']);
});

test('snapshot storage applies only at exact configured recognized origin and merges keys', async () => {
  const f = fixture({ options: { readSnapshot: async () => snapshot({ storage: [{ origin: 'https://chatgpt.com', localStorage: { fixtureToken: 'FIXTURE_STORAGE_SECRET' } }] }) } });
  f.stored.set('already-present', 'preserved');
  const report = await f.controller.importFile({ provider_id: 'chatgpt' });
  assert.equal(report.storage_imported, 1); assert.equal(f.stored.get('fixtureToken'), 'FIXTURE_STORAGE_SECRET');
  assert.equal(f.stored.get('already-present'), 'preserved');
  assert.ok(f.events.includes('flushStorage'));
  assert.equal(f.events.filter(item => item === 'load:https://chatgpt.com/').length, 2);
  assert.doesNotMatch(JSON.stringify(report), /STORAGE_SECRET|fixtureToken/);
});

test('redirect or alternate snapshot origin never receives localStorage injection', async () => {
  for (const change of [{ redirect: 'https://auth.openai.com/login' }, {}]) {
    const f = fixture({ ...change, options: { readSnapshot: async () => snapshot({ storage: [{ origin: change.redirect ? 'https://chatgpt.com' : 'https://auth.openai.com', localStorage: { fixtureToken: 'SECRET' } }] }) } });
    const report = await f.controller.importFile({ provider_id: 'chatgpt' });
    assert.equal(report.storage_imported, 0);
    assert.equal(f.events.some(item => typeof item === 'string' && item.startsWith('script:')), false);
    assert.ok(report.warnings.some(item => item.code === 'STORAGE_ORIGIN_SKIPPED'));
  }
});

test('revoked isolated-world operation cannot apply late storage', () => {
  const sandbox = vm.createContext({ Date, location: { origin: 'https://chatgpt.com', protocol: 'https:' }, localStorage: { setItem: () => { throw new Error('must not apply'); } } });
  const args = { id: 'test-operation', origin: 'https://chatgpt.com', deadline: Date.now() + 10000, entries: [['key', 'secret']] };
  const run = action => vm.runInContext(`(${storageAction.toString()})(${JSON.stringify(action)},${JSON.stringify(args)})`, sandbox);
  assert.equal(run('prepare').prepared, true); run('revoke'); assert.equal(run('apply').skipped, true);
});

test('cancel during storage preparation revokes marker and performs no storage apply', async () => {
  const f = fixture({ onScript: (action, controller) => { if (action === 'prepare') controller.cancel(); }, options: {
    readSnapshot: async () => snapshot({ storage: [{ origin: 'https://chatgpt.com', localStorage: { fixtureToken: 'SECRET' } }] }),
  } });
  await assert.rejects(f.controller.importFile({ provider_id: 'chatgpt' }), { code: 'IMPORT_ABORTED' });
  assert.equal(f.events.includes('script:apply'), false); assert.equal(f.stored.size, 0);
  assert.equal(f.sandbox.__fusionLoginImport, undefined);
});

test('canceled submitted storage operation drains under lock before release', async () => {
  const waiting = deferred(), applied = deferred();
  const f = fixture({ onScript: async action => { if (action === 'apply') { applied.resolve(); await waiting.promise; } }, options: {
    readSnapshot: async () => snapshot({ storage: [{ origin: 'https://chatgpt.com', localStorage: { fixtureToken: 'SECRET' } }] }),
  } });
  const pending = f.controller.importFile({ provider_id: 'chatgpt' });
  await applied.promise;
  f.disconnect();
  assert.equal(f.controller.busy, true); assert.equal(f.events.includes('release'), false);
  // A submitted renderer operation may already have written state before it can
  // return to main. Cancellation prevents further stages; it is not rollback.
  assert.equal(f.stored.get('fixtureToken'), 'SECRET');
  waiting.resolve();
  await assert.rejects(pending, { code: 'IMPORT_ABORTED' });
  assert.equal(f.controller.busy, false); assert.equal(f.events.includes('flushStorage'), false);
  assert.equal(f.events.filter(value => value === 'load:https://chatgpt.com/').length, 1);
});

test('operation timeout releases lease even while file picker is still pending', async () => {
  const waiting = deferred();
  const f = fixture({ options: { chooseFile: () => waiting.promise, timeout: 15 } });
  await assert.rejects(f.controller.importFile({ provider_id: 'chatgpt' }), { code: 'IMPORT_ABORTED' });
  assert.equal(f.controller.busy, false); assert.ok(f.events.includes('release'));
  waiting.resolve('late-file.json');
});

test('profile listing returns metadata only and removes helper warnings text', async () => {
  const f = fixture({ options: { runHelper: async request => {
    assert.equal(request.action, 'list'); return { profiles: [{ id: 'profile1', family: 'firefox', browser: 'Firefox', name: 'default', path: '/fixture/profile', cookies: [cookie()] }],
      capabilities: { chromium_decryption: true, api_token: 'SECRET' }, warnings: [{ code: 'DECRYPTION_FAILED', message: 'SECRET' }] };
  } } });
  const response = await f.controller.listProfiles();
  assert.equal(response.profiles.length, 1); assert.equal(response.capabilities.chromium_decryption, true);
  assert.doesNotMatch(JSON.stringify(response), /SECRET|fixture-session|cookies/);
  assert.equal(f.events.length, 0);
});

function fakeSpawn(response, captured, { code = 0, stall = false } = {}) {
  return (python, argv, options) => {
    Object.assign(captured, { python, argv, options });
    const child = new EventEmitter();
    child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
    child.kill = signal => { captured.killed = signal; setImmediate(() => child.emit('close', 1)); };
    let input = '';
    child.stdin.on('data', chunk => { input += chunk.toString(); });
    child.stdin.on('finish', () => {
      captured.input = input;
      if (stall) return;
      child.stderr.write('FIXTURE_KEYRING_SECRET that must never be returned');
      child.stdout.write(typeof response === 'string' ? response : JSON.stringify(response));
      setImmediate(() => child.emit('close', code));
    });
    return child;
  };
}

test('helper uses stdin, no shell, minimum environment, and does not expose stderr', async () => {
  const captured = {};
  const response = await runPythonHelper({ python: '/fixture/python', root: '/fixture/root', payload: { action: 'list' },
    spawnProcess: fakeSpawn({ ok: true, profiles: [] }, captured) });
  assert.equal(response.ok, true); assert.equal(captured.options.shell, false);
  assert.deepEqual(captured.argv, ['-m', 'backend.browser_login']); assert.deepEqual(JSON.parse(captured.input), { action: 'list' });
  assert.equal(Object.hasOwn(captured.options.env, 'FUSION_TOKEN'), false);
  assert.deepEqual(helperEnvironment({ HOME: '/fixture', PATH: '/bin', FUSION_TOKEN: 'SECRET', OPENAI_API_KEY: 'SECRET', RANDOM_SECRET: 'SECRET' }),
    { HOME: '/fixture', PATH: '/bin', PYTHONUNBUFFERED: '1', PYTHONUTF8: '1' });
  assert.doesNotMatch(JSON.stringify(response), /KEYRING_SECRET/);
});

test('helper timeout kills subprocess and malformed/private errors are sanitized', async () => {
  const captured = {};
  await assert.rejects(runPythonHelper({ python: '/fixture/python', root: '/fixture/root', payload: { action: 'extract' }, timeout: 10,
    spawnProcess: fakeSpawn(null, captured, { stall: true }) }), { code: 'IMPORT_ABORTED' });
  assert.equal(captured.killed, 'SIGTERM');
  for (const response of ['FIXTURE_SECRET', { ok: false, error: { code: 'DECRYPTION_FAILED', message: 'FIXTURE_SECRET' } }]) {
    await assert.rejects(runPythonHelper({ python: '/fixture/python', root: '/fixture/root', payload: { action: 'list' }, spawnProcess: fakeSpawn(response, {}) }),
      error => !error.message.includes('FIXTURE_SECRET'));
  }
});
