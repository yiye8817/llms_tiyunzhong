'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const exporter = require('../browser-extension/src/core.js');
const sites = require('../browser-login-sites.json');
const { collectSnapshot, providerForTab, normalizeCookie, readCurrentStorage } = exporter;
const now = 2_000_000_000_000;
const fixtureCookie = (extra = {}) => ({ name: 'fixture_session', value: 'fixture-value-only',
  domain: '.chatgpt.com', path: '/', secure: true, httpOnly: true, hostOnly: false,
  session: true, sameSite: 'lax', storeId: '0', ...extra });
function fixture(extra = {}) {
  const calls = [];
  const tab = { id: 17, url: 'https://chatgpt.com/c/fixture', incognito: false, ...extra.tab };
  const storeId = extra.storeId || '0';
  const api = {
    tabs: {
      async query(query) { calls.push(['query', query]); return [tab]; },
      async get(id) { calls.push(['get', id]); return extra.finalTab || tab; },
    },
    cookies: {
      async getAllCookieStores() { calls.push(['stores']); return [{ id: storeId, tabIds: [17] }]; },
      async getAll(filter) {
        calls.push(['cookies', filter]);
        if (extra.cookieError) throw new Error('do not leak fixture-sensitive-error');
        return filter.domain === 'chatgpt.com' ? (extra.cookies || [fixtureCookie({ storeId })]) : [];
      },
    },
    scripting: { async executeScript(spec) {
      calls.push(['script', spec]);
      if (extra.scriptError) throw new Error('do not leak fixture-sensitive-error');
      return [{ frameId: 0, result: extra.storageResult || { origin: new URL(tab.url).origin, localStorage: { token: 'fixture-token' } } }];
    } },
  };
  return { api, calls, tab };
}

test('extension matches only recognized HTTPS origins with strict domain boundaries', () => {
  assert.equal(providerForTab({ id: 1, url: 'https://chatgpt.com/c/a' }, sites).id, 'chatgpt');
  assert.equal(providerForTab({ id: 1, url: 'https://chat.qwenlm.ai/' }, sites).id, 'qwen');
  assert.equal(providerForTab({ id: 1, url: 'https://chatglm.cn/' }, sites).id, 'glm');
  assert.equal(providerForTab({ id: 1, url: 'https://www.chatglm.cn/' }, sites).id, 'glm');
  assert.equal(providerForTab({ id: 1, url: 'https://chat.z.ai/' }, sites).id, 'glm');
  assert.equal(providerForTab({ id: 1, url: 'https://www.kimi.com/chat/fixture' }, sites).id, 'kimi');
  assert.equal(providerForTab({ id: 1, url: 'https://kimi.com/' }, sites).id, 'kimi');
  for (const url of ['http://chatgpt.com/', 'https://evilchatgpt.com/', 'https://chatgpt.com.evil.test/',
    'https://auth.openai.com/', 'https://evilkimi.com/', 'https://chatglm.cn.evil.test/', 'https://chat.z.ai.evil.test/',
    'https://chatgpt.com:8443/', 'https://user@chatgpt.com/', 'about:blank']) {
    assert.throws(() => providerForTab({ id: 1, url }, sites), { code: 'UNSUPPORTED_TAB' });
  }
  assert.equal(exporter.hostInScope('.CHATGPT.COM', sites.chatgpt.domains), true);
  assert.equal(exporter.hostInScope('.evilchatgpt.com', sites.chatgpt.domains), false);
});

test('export queries only current provider domains, preserves HttpOnly and exact snapshot schema', async () => {
  const { api, calls } = fixture();
  const result = await collectSnapshot(api, sites, { now });
  const snapshot = JSON.parse(result.json);
  assert.deepEqual(Object.keys(snapshot).sort(), ['cookies', 'created_at', 'format', 'provider_id', 'source', 'storage', 'version', 'warnings']);
  assert.equal(snapshot.format, 'multillm-fusion-login');
  assert.equal(snapshot.version, 1);
  assert.equal(snapshot.source, 'extension');
  assert.equal(snapshot.provider_id, 'chatgpt');
  assert.equal(snapshot.cookies[0].httpOnly, true);
  assert.equal(snapshot.cookies[0].session, true);
  assert.equal('expirationDate' in snapshot.cookies[0], false);
  assert.equal(snapshot.storage[0].origin, 'https://chatgpt.com');
  assert.deepEqual(calls.filter(call => call[0] === 'cookies').map(call => call[1]), [
    { domain: 'chatgpt.com', storeId: '0' }, { domain: 'openai.com', storeId: '0' },
  ]);
  const spec = calls.find(call => call[0] === 'script')[1];
  assert.equal(spec.func, readCurrentStorage);
  assert.deepEqual(spec.target, { tabId: 17, frameIds: [0] });
  assert.equal(spec.world, 'ISOLATED');
});

test('scope, partition, first-party isolation and cookie-store boundaries never collapse', async () => {
  const { api } = fixture({ cookies: [
    fixtureCookie(), fixtureCookie({ domain: '.chatgpt.com.evil.test', value: 'unrelated-secret' }),
    fixtureCookie({ name: 'partition', partitionKey: { topLevelSite: 'https://chatgpt.com' } }),
    fixtureCookie({ name: 'partition-empty', partitionKey: {} }),
    fixtureCookie({ name: 'first-party', firstPartyDomain: 'chatgpt.com' }),
    fixtureCookie({ name: 'container', storeId: 'firefox-container-1' }),
  ] });
  const result = await collectSnapshot(api, sites, { now });
  assert.equal(result.cookieCount, 1);
  assert.equal(result.skipped, 5);
  assert.doesNotMatch(result.json, /unrelated-secret|topLevelSite|firefox-container/);
  assert.deepEqual(result.warnings.map(item => item.code), ['EXT_PARTITIONED_SKIPPED', 'EXT_COOKIE_CONTEXT_SKIPPED']);
});

test('Firefox only reads the selected default cookie store; containers/private tabs rejected before reading', async () => {
  const normal = fixture({ storeId: 'firefox-default', tab: { cookieStoreId: 'firefox-default' } });
  assert.equal((await collectSnapshot(normal.api, sites, { now, firefox: true })).cookieCount, 1);
  assert.ok(normal.calls.filter(call => call[0] === 'cookies').every(call => call[1].storeId === 'firefox-default' && call[1].firstPartyDomain === ''));
  for (const config of [
    { storeId: 'firefox-container-2', tab: { cookieStoreId: 'firefox-container-2' } },
    { storeId: 'firefox-container-2' },
    { storeId: 'firefox-private', tab: { incognito: true } },
  ]) {
    const context = fixture(config);
    await assert.rejects(collectSnapshot(context.api, sites, { now, firefox: true }), error => ['CONTAINER_TAB', 'PRIVATE_TAB'].includes(error.code));
    assert.equal(context.calls.some(call => call[0] === 'cookies' || call[0] === 'script'), false);
  }
});

test('missing or mismatched tab cookie store fails before login data reads', async () => {
  const { api, calls } = fixture();
  api.cookies.getAllCookieStores = async () => [{ id: '0', tabIds: [8] }];
  await assert.rejects(collectSnapshot(api, sites, { now }), { code: 'COOKIE_ACCESS_FAILED' });
  assert.equal(calls.some(call => call[0] === 'cookies'), false);
});

test('persistent cookie expiry and SameSite are preserved; malformed/expired cookies skipped', () => {
  const provider = { domains: ['chatgpt.com'] };
  const result = normalizeCookie(fixtureCookie({ session: false, expirationDate: now / 1000 + 60, hostOnly: true, sameSite: 'strict' }), provider, '0', now);
  assert.equal(result.cookie.expirationDate, now / 1000 + 60);
  assert.equal(result.cookie.hostOnly, true);
  assert.equal(result.cookie.sameSite, 'strict');
  assert.equal(normalizeCookie(fixtureCookie({ session: false, expirationDate: 1 }), provider, '0', now).skip, 'EXT_COOKIE_INVALID_SKIPPED');
  assert.equal(normalizeCookie(fixtureCookie({ sameSite: 'bad' }), provider, '0', now).skip, 'EXT_COOKIE_INVALID_SKIPPED');
});

test('static page function reads only exact current origin and preserves prototype-named keys', () => {
  const dom = new JSDOM('', { url: 'https://chatgpt.com/', runScripts: 'outside-only' });
  dom.window.TextEncoder = TextEncoder;
  dom.window.localStorage.setItem('__proto__', 'fixture-proto');
  dom.window.localStorage.setItem('token', 'fixture-token');
  const execute = (origin, maximum) => dom.window.eval(`(${readCurrentStorage.toString()})(${JSON.stringify(origin)},${maximum})`);
  const value = execute('https://chatgpt.com', exporter.MAX_STORAGE_BYTES);
  assert.equal(value.localStorage.__proto__, 'fixture-proto');
  assert.equal(value.localStorage.token, 'fixture-token');
  assert.equal(execute('https://chat.deepseek.com', exporter.MAX_STORAGE_BYTES).code, 'TAB_CHANGED');
  dom.window.localStorage.setItem('huge', '中'.repeat(180000));
  assert.equal(execute('https://chatgpt.com', exporter.MAX_STORAGE_BYTES).code, 'EXT_STORAGE_TOO_LARGE');
  dom.window.close();
});

test('oversized or unavailable storage is omitted with safe warning and cookies retained', async () => {
  for (const extra of [
    { storageResult: { origin: 'https://chatgpt.com', localStorage: { large: '中'.repeat(180000) } } },
    { scriptError: true },
    { storageResult: { origin: 'https://evil.test', localStorage: { unrelated: 'fixture-secret' } } },
  ]) {
    const { api } = fixture(extra);
    const result = await collectSnapshot(api, sites, { now });
    assert.equal(result.cookieCount, 1);
    assert.equal(result.storageCount, 0);
    assert.equal(JSON.parse(result.json).storage.length, 0);
    assert.ok(result.warnings.length > 0);
    assert.doesNotMatch(result.json, /fixture-sensitive-error|fixture-secret/);
  }
});

test('full snapshot exceeding 2 MiB is rejected without truncating values', async () => {
  const { api } = fixture({ cookies: [fixtureCookie({ value: 'x'.repeat(exporter.MAX_SNAPSHOT_BYTES) })] });
  await assert.rejects(collectSnapshot(api, sites, { now }), { code: 'SNAPSHOT_TOO_LARGE' });
});

test('navigation to another origin aborts export and does not return mixed session', async () => {
  const { api } = fixture({ finalTab: { id: 17, url: 'https://chat.deepseek.com/', incognito: false } });
  await assert.rejects(collectSnapshot(api, sites, { now }), { code: 'TAB_CHANGED' });
  const redirected = fixture({ storageResult: { code: 'TAB_CHANGED' } });
  await assert.rejects(collectSnapshot(redirected.api, sites, { now }), { code: 'TAB_CHANGED' });
});

test('cookie permission failures and arbitrary errors never expose exception strings', async () => {
  const { api } = fixture({ cookieError: true });
  const result = await collectSnapshot(api, sites, { now });
  assert.equal(result.cookieCount, 0);
  assert.equal(result.storageCount, 1);
  assert.ok(result.warnings.some(item => item.code === 'EXT_COOKIE_READ_FAILED'));
  assert.doesNotMatch(result.json, /fixture-sensitive-error/);
  assert.doesNotMatch(exporter.safeMessage(new Error('fixture-secret')), /fixture-secret/);
});

test('standalone extension bundles have exact fixed host permissions and identical shared code', () => {
  const base = path.join(__dirname, '..', 'browser-extension');
  for (const family of ['chromium', 'firefox']) {
    const folder = path.join(base, family);
    const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json')));
    assert.equal(manifest.version, '1.2.0');
    assert.deepEqual(manifest.permissions, ['cookies', 'activeTab', 'scripting', 'downloads']);
    assert.deepEqual(manifest.host_permissions.sort(), Object.values(sites).flatMap(site => site.domains.map(domain => `https://*.${domain}/*`)).sort());
    assert.equal(manifest.manifest_version, 3);
    assert.equal(manifest.background, undefined);
    assert.equal(manifest.content_scripts, undefined);
    assert.match(manifest.content_security_policy.extension_pages, /connect-src 'none'/);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(folder, 'browser-login-sites.json'))), sites);
    for (const name of ['core.js', 'popup.js', 'popup.html', 'popup.css']) assert.equal(fs.readFileSync(path.join(folder, name), 'utf8'), fs.readFileSync(path.join(base, 'src', name), 'utf8'));
  }
});

test('popup never collects automatically; explicit button causes only local blob download', async () => {
  const base = path.join(__dirname, '..', 'browser-extension', 'src');
  const dom = new JSDOM(fs.readFileSync(path.join(base, 'popup.html'), 'utf8'), { url: 'https://extension.test', runScripts: 'outside-only' });
  const { api, calls } = fixture();
  let downloaded, blob, changedListener, revoked = 0;
  api.downloads = {
    onChanged: { addListener(listener) { changedListener = listener; }, removeListener() {} },
    async download(spec) {
      downloaded = spec;
      // Browser completion can precede resolution of the download ID.
      changedListener({ id: 42, state: { current: 'complete' } });
      return 42;
    },
    async search(query) { assert.deepEqual(query.id, 42); return [{ id: 42, state: 'complete' }]; },
  };
  dom.window.chrome = api;
  dom.window.FUSION_LOGIN_SITES = sites;
  dom.window.TextEncoder = TextEncoder;
  dom.window.URL.createObjectURL = value => { blob = value; return 'blob:https://extension.test/fixture'; };
  dom.window.URL.revokeObjectURL = () => { revoked++; };
  dom.window.eval(fs.readFileSync(path.join(base, 'core.js'), 'utf8'));
  dom.window.eval(fs.readFileSync(path.join(base, 'popup.js'), 'utf8'));
  assert.equal(calls.length, 0);
  assert.equal(downloaded, undefined);
  dom.window.document.querySelector('#export').click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(downloaded.url, 'blob:https://extension.test/fixture');
  assert.equal(downloaded.saveAs, true);
  assert.equal(downloaded.filename, 'multillm-login-chatgpt.json');
  assert.ok(blob.size > 0 && blob.size <= exporter.MAX_SNAPSHOT_BYTES);
  assert.equal(revoked, 1);
  assert.doesNotMatch(dom.window.document.body.textContent, /fixture-token|fixture-value-only/);
  assert.equal(dom.window.document.querySelector('#export').disabled, false);
  dom.window.close();
});
