'use strict';
// Rebuild checked-in, standalone extensions from the app's fixed site scopes.
const fs = require('node:fs');
const path = require('node:path');
const directory = __dirname;
const sites = JSON.parse(fs.readFileSync(path.join(directory, '..', 'browser-login-sites.json'), 'utf8'));
for (const site of Object.values(sites)) {
  if (!site.domains.every(domain => /^[a-z0-9]+(?:[.-][a-z0-9]+)+$/.test(domain)) ||
      !Array.isArray(site.origins) || !site.origins.every(origin => {
        const url = new URL(origin);
        return url.protocol === 'https:' && url.origin === origin &&
          site.domains.some(domain => url.hostname === domain || url.hostname.endsWith(`.${domain}`));
      })) throw new Error('Invalid fixed browser login scope');
}
const hostPermissions = [...new Set(Object.values(sites).flatMap(site => site.domains.map(domain => `https://*.${domain}/*`)))];
for (const family of ['chromium', 'firefox']) {
  const destination = path.join(directory, family);
  fs.mkdirSync(destination, { recursive: true });
  for (const filename of ['core.js', 'popup.js', 'popup.html', 'popup.css']) {
    fs.copyFileSync(path.join(directory, 'src', filename), path.join(destination, filename));
  }
  fs.writeFileSync(path.join(destination, 'sites.js'), `'use strict';\nglobalThis.FUSION_LOGIN_SITES = ${JSON.stringify(sites, null, 2)};\n`);
  fs.writeFileSync(path.join(destination, 'browser-login-sites.json'), `${JSON.stringify(sites, null, 2)}\n`);
  const manifest = {
    manifest_version: 3, name: 'MultiLLM Fusion 本机登录导出', version: '1.2.0',
    description: '用户点击后，将当前模型站点的登录 Cookie 和 localStorage 导出为本机 JSON 文件。',
    permissions: ['cookies', 'activeTab', 'scripting', 'downloads'], host_permissions: hostPermissions,
    action: { default_title: '导出当前模型登录', default_popup: 'popup.html' },
    content_security_policy: { extension_pages: "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; connect-src 'none'; base-uri 'none'" },
  };
  if (family === 'chromium') manifest.minimum_chrome_version = '119';
  else manifest.browser_specific_settings = { gecko: { id: 'local-login-export@multillm-fusion.local', strict_min_version: '128.0' } };
  fs.writeFileSync(path.join(destination, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
}
