'use strict';
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FusionLoginExport = api;
})(globalThis, function () {
  const MAX_STORAGE_BYTES = 512 * 1024;
  const MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024;
  const messages = Object.freeze({
    UNSUPPORTED_TAB: '请打开已登录的 ChatGPT、DeepSeek、Qwen、Claude、Grok、GLM 或 Kimi 对话网页，再点击导出。',
    PRIVATE_TAB: '请使用普通浏览窗口；不导出隐私窗口的登录信息。',
    CONTAINER_TAB: '此标签页使用容器或无法识别的 Cookie 存储。请使用 Firefox 默认标签页，或在应用中手动登录。',
    TAB_CHANGED: '导出期间标签页发生跳转，请回到模型对话网页重试。',
    COOKIE_ACCESS_FAILED: '无法读取当前标签页的 Cookie 存储。请在扩展管理页允许对应模型站点访问后重试。',
    SNAPSHOT_TOO_LARGE: '登录文件超过 2 MiB，未导出。请在应用中手动登录。',
    DOWNLOAD_FAILED: '文件未保存。请保持扩展窗口打开，并重试导出。',
    EXPORT_FAILED: '导出未完成。请确认模型网页已加载，并允许扩展访问当前站点后重试。',
  });
  const warningMessages = Object.freeze({
    EXT_PARTITIONED_SKIPPED: '已跳过分区或第一方隔离 Cookie；这类登录状态需要在应用中重新登录。',
    EXT_COOKIE_CONTEXT_SKIPPED: '已跳过其他 Cookie 存储中的记录。',
    EXT_COOKIE_INVALID_SKIPPED: '已跳过无效或过期 Cookie。',
    EXT_COOKIE_READ_FAILED: '部分模型域名的 Cookie 未能读取，请检查扩展站点权限。',
    EXT_STORAGE_UNAVAILABLE: '当前页面的 localStorage 无法读取；文件仅包含可读取的 Cookie。',
    EXT_STORAGE_TOO_LARGE: 'localStorage 超过 512 KiB，未包含在登录文件中。',
  });

  class ExportError extends Error {
    constructor(code) { super(messages[code] || messages.EXPORT_FAILED); this.code = code; }
  }
  function safeMessage(error) { return messages[error && error.code] || messages.EXPORT_FAILED; }
  function byteLength(value) { return new TextEncoder().encode(value).byteLength; }
  function hostInScope(host, domains) {
    if (typeof host !== 'string' || !Array.isArray(domains)) return false;
    const normalized = host.replace(/^\./, '').toLowerCase();
    if (!/^[a-z0-9.-]+$/.test(normalized) || normalized.endsWith('.') || normalized.startsWith('.')) return false;
    return domains.some(root => normalized === root || normalized.endsWith(`.${root}`));
  }
  function providerForTab(tab, sites) {
    if (!tab || !Number.isInteger(tab.id)) throw new ExportError('UNSUPPORTED_TAB');
    if (tab.incognito) throw new ExportError('PRIVATE_TAB');
    let parsed;
    try { parsed = new URL(tab.url); } catch { throw new ExportError('UNSUPPORTED_TAB'); }
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw new ExportError('UNSUPPORTED_TAB');
    for (const [id, site] of Object.entries(sites)) {
      if (hostInScope(parsed.hostname, site.domains) && site.origins.includes(parsed.origin)) {
        return { id, name: site.name, origin: parsed.origin, domains: site.domains };
      }
    }
    throw new ExportError('UNSUPPORTED_TAB');
  }

  // This static function executes only in the selected tab's isolated top frame.
  // It must be self-contained because the browser serializes the function.
  function readCurrentStorage(expectedOrigin, maximumBytes) {
    try {
      if (location.protocol !== 'https:' || location.origin !== expectedOrigin) return { code: 'TAB_CHANGED' };
      const values = Object.create(null);
      let approximateBytes = 2;
      const encoder = new TextEncoder();
      for (let index = 0; index < localStorage.length; index++) {
        const key = localStorage.key(index);
        if (key === null) continue;
        const value = localStorage.getItem(key);
        if (typeof value !== 'string') continue;
        approximateBytes += encoder.encode(JSON.stringify(key)).length + encoder.encode(JSON.stringify(value)).length + 2;
        if (approximateBytes > maximumBytes) return { code: 'EXT_STORAGE_TOO_LARGE' };
        values[key] = value;
      }
      const storage = [{ origin: location.origin, localStorage: values }];
      if (encoder.encode(JSON.stringify(storage)).length > maximumBytes) return { code: 'EXT_STORAGE_TOO_LARGE' };
      return { origin: expectedOrigin, localStorage: values };
    } catch { return { code: 'EXT_STORAGE_UNAVAILABLE' }; }
  }

  function normalizeCookie(cookie, provider, storeId, now) {
    if (!cookie || !hostInScope(cookie.domain, provider.domains)) return { skip: 'scope' };
    if (cookie.storeId && cookie.storeId !== storeId) return { skip: 'EXT_COOKIE_CONTEXT_SKIPPED' };
    if (cookie.partitionKey != null || cookie.partitioned === true || cookie.firstPartyDomain || cookie.originAttributes) {
      return { skip: 'EXT_PARTITIONED_SKIPPED' };
    }
    if (typeof cookie.name !== 'string' || typeof cookie.value !== 'string' ||
        typeof cookie.path !== 'string' || !cookie.path.startsWith('/') ||
        ['hostOnly', 'httpOnly', 'secure', 'session'].some(key => typeof cookie[key] !== 'boolean') ||
        !['unspecified', 'no_restriction', 'lax', 'strict'].includes(cookie.sameSite)) {
      return { skip: 'EXT_COOKIE_INVALID_SKIPPED' };
    }
    if (!cookie.session && (!Number.isFinite(cookie.expirationDate) || cookie.expirationDate <= now / 1000)) {
      return { skip: 'EXT_COOKIE_INVALID_SKIPPED' };
    }
    const normalized = {
      name: cookie.name, value: cookie.value, domain: cookie.domain.toLowerCase(), path: cookie.path,
      secure: cookie.secure, httpOnly: cookie.httpOnly, hostOnly: cookie.hostOnly,
      session: cookie.session, sameSite: cookie.sameSite,
    };
    if (!cookie.session) normalized.expirationDate = cookie.expirationDate;
    return { cookie: normalized };
  }

  async function resolveStore(api, tab, firefox) {
    if (firefox && tab.cookieStoreId && tab.cookieStoreId !== 'firefox-default') throw new ExportError('CONTAINER_TAB');
    let stores;
    try { stores = await api.cookies.getAllCookieStores(); } catch { throw new ExportError('COOKIE_ACCESS_FAILED'); }
    const matching = stores.filter(store => Array.isArray(store.tabIds) && store.tabIds.includes(tab.id));
    if (matching.length !== 1 || (tab.cookieStoreId && tab.cookieStoreId !== matching[0].id)) {
      throw new ExportError('COOKIE_ACCESS_FAILED');
    }
    const id = matching[0].id;
    if (typeof id !== 'string' || !id) throw new ExportError('COOKIE_ACCESS_FAILED');
    if (firefox && id !== 'firefox-default') throw new ExportError('CONTAINER_TAB');
    return id;
  }

  async function collectSnapshot(api, sites, options = {}) {
    const tabs = await api.tabs.query({ active: true, currentWindow: true });
    if (tabs.length !== 1) throw new ExportError('UNSUPPORTED_TAB');
    const tab = tabs[0];
    const provider = providerForTab(tab, sites);
    const storeId = await resolveStore(api, tab, options.firefox === true);
    const now = options.now === undefined ? Date.now() : options.now;
    const cookies = [];
    const warningCodes = new Set();
    const seen = new Set();
    let skipped = 0;
    for (const domain of provider.domains) {
      let items;
      try {
        const filter = { domain, storeId };
        // Firefox requires this field with first-party isolation. Read only the
        // unisolated store instead of collapsing firstPartyDomain isolation.
        if (options.firefox) filter.firstPartyDomain = '';
        items = await api.cookies.getAll(filter);
      } catch { warningCodes.add('EXT_COOKIE_READ_FAILED'); continue; }
      for (const item of items) {
        const normalized = normalizeCookie(item, provider, storeId, now);
        if (normalized.skip) {
          skipped++;
          if (normalized.skip !== 'scope') warningCodes.add(normalized.skip);
          continue;
        }
        const cookie = normalized.cookie;
        const key = JSON.stringify([cookie.domain.replace(/^\./, ''), cookie.path, cookie.name]);
        if (!seen.has(key)) { cookies.push(cookie); seen.add(key); }
      }
    }
    const storage = [];
    let results;
    try {
      results = await api.scripting.executeScript({
        target: { tabId: tab.id, frameIds: [0] }, world: 'ISOLATED',
        func: readCurrentStorage, args: [provider.origin, MAX_STORAGE_BYTES],
      });
    } catch { warningCodes.add('EXT_STORAGE_UNAVAILABLE'); }
    if (results) {
      const result = results.find(entry => entry.frameId === 0)?.result;
      if (result?.code === 'TAB_CHANGED') throw new ExportError('TAB_CHANGED');
      if (result?.code === 'EXT_STORAGE_TOO_LARGE') warningCodes.add('EXT_STORAGE_TOO_LARGE');
      else if (result && !result.code && result.origin === provider.origin && result.localStorage &&
          typeof result.localStorage === 'object' && !Array.isArray(result.localStorage) &&
          Object.values(result.localStorage).every(value => typeof value === 'string')) {
        const entry = { origin: provider.origin, localStorage: result.localStorage };
        if (byteLength(JSON.stringify([entry])) <= MAX_STORAGE_BYTES) storage.push(entry);
        else warningCodes.add('EXT_STORAGE_TOO_LARGE');
      } else warningCodes.add('EXT_STORAGE_UNAVAILABLE');
    }
    // If the tab moved to another origin/account context, do not export a mixed
    // snapshot. The data read above remains only in this popup's memory.
    let current;
    try { current = await api.tabs.get(tab.id); } catch { throw new ExportError('TAB_CHANGED'); }
    const currentProvider = providerForTab(current, sites);
    if (currentProvider.id !== provider.id || currentProvider.origin !== provider.origin ||
        (current.cookieStoreId && current.cookieStoreId !== storeId)) throw new ExportError('TAB_CHANGED');
    const warnings = [...warningCodes].map(code => ({ code, message: warningMessages[code] }));
    const snapshot = {
      format: 'multillm-fusion-login', version: 1, provider_id: provider.id,
      created_at: new Date(now).toISOString(), source: 'extension', cookies, storage, warnings,
    };
    const json = JSON.stringify(snapshot);
    if (byteLength(json) > MAX_SNAPSHOT_BYTES) throw new ExportError('SNAPSHOT_TOO_LARGE');
    return { json, providerName: provider.name, providerId: provider.id, cookieCount: cookies.length,
      storageCount: storage.reduce((count, entry) => count + Object.keys(entry.localStorage).length, 0),
      skipped, warnings };
  }
  return { collectSnapshot, providerForTab, hostInScope, normalizeCookie, readCurrentStorage,
    safeMessage, ExportError, MAX_STORAGE_BYTES, MAX_SNAPSHOT_BYTES };
});
