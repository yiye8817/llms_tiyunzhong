# 本机浏览器登录导出扩展

当应用直接导入浏览器 Cookie 时遇到加密、数据库占用，或站点把登录状态放在 localStorage 中，可使用此扩展。它读取你当前已登录的模型站点，将 Cookie（包括 HttpOnly）和当前页面 localStorage 下载为本机 JSON 文件，再由桌面应用导入。

## Chrome / Chromium / Brave / Edge

1. 打开扩展管理页：Chrome 或 Chromium 为 `chrome://extensions`，Brave 为 `brave://extensions`，Edge 为 `edge://extensions`。
2. 开启“开发者模式”，点击“加载已解压的扩展程序”，选择本目录下的 `chromium` 文件夹。
3. 将扩展固定到工具栏。若浏览器提示站点访问权限，允许你需要的模型站点。
4. 在普通窗口打开已登录的模型对话网页，点击扩展按钮，然后点击“导出当前模型登录文件”。
5. 保持弹出窗口打开，等待文件下载完成。在桌面应用“设置 → 浏览器登录”选择对应模型，点击“导入登录文件”并选择刚保存的 JSON。
6. 在模型网页确认是否已登录。文件含有效登录会话，请仅保留在本机，不要上传到网盘、聊天或代码仓库；导入后可删除。

使用 Chromium 119 或更新版本。不需要关闭浏览器或修改系统密钥环。

## Firefox

1. 使用 Firefox 128 或更新版本，打开 `about:debugging#/runtime/this-firefox`。
2. 点击“临时载入附加组件”，选择本目录 `firefox/manifest.json`。
3. 按上方步骤从已登录的模型对话网页导出，再导入桌面应用。

临时扩展会在 Firefox 重启后卸载，需要重新载入。此源码扩展未提交 Mozilla 签名，不能直接当作已签名的永久扩展安装。若需要使用，请在普通、默认标签页操作；扩展拒绝隐私窗口和容器标签页，避免把不同身份的登录状态混合。

## 支持范围和限制

| 模型 | 可点击导出的网页 |
| --- | --- |
| ChatGPT | `https://chatgpt.com` |
| DeepSeek | `https://chat.deepseek.com` |
| Qwen | `https://chat.qwen.ai`、`https://chat.qwenlm.ai` |
| Claude | `https://claude.ai` |
| Grok | `https://grok.com` |
| GLM（智谱清言 / Z.AI） | `https://chatglm.cn`、`https://www.chatglm.cn`、`https://z.ai`、`https://chat.z.ai` |
| Kimi | `https://kimi.com`、`https://www.kimi.com` |

Cookie 范围使用项目根目录 `browser-login-sites.json` 中对应模型的固定域名后缀。localStorage 只从当前模型网页的顶层页面读取；应用仅向所配置模型的相同 HTTPS 源导入，Qwen 两个地址不能互换 localStorage。

扩展只在点击导出按钮后读取登录内容，不会自动登录、自动同步、上传内容、读取密码库，或读取浏览历史。没有后台脚本、远程脚本、遥测、`<all_urls>`、`nativeMessaging` 或扩展永久存储。对登录信息的操作只包括浏览器公开扩展 API 读取，以及用户发起的本地文件保存。

以下状态无法完整迁移：分区 Cookie、Firefox 第一方隔离或容器 Cookie、sessionStorage、IndexedDB、服务工作线程缓存、通行密钥、硬件绑定令牌，以及 Google/GitHub/X 等独立身份提供商的通用账号。扩展只查询普通 Cookie 存储；浏览器默认不返回的隔离 Cookie 不计入跳过数，返回了明确隔离标记的记录会跳过并提示。网站验证、设备绑定、会话过期或风控仍可能要求重新登录。导入条数不代表登录已通过验证。

localStorage 最多 512 KiB（按 JSON UTF-8 字节计，包括 origin 和数组结构）；超过时整段省略并提示，Cookie 仍可导出。完整 JSON 文件最多 2 MiB，超过则拒绝保存，不截断登录值。

## 维护和验证

`src/` 是共享源码，两个可加载扩展文件夹已包含完整文件。修改固定站点范围或共享源码后，在项目根目录运行：

```bash
node browser-extension/build.cjs
node --test tests/test_login_extension.cjs
```

测试使用虚构 Cookie、模拟浏览器 API 和 jsdom 页面，不读取真实浏览器账号。真实登录流程需要在用户本机验证。

API 行为参考：[Chrome cookies](https://developer.chrome.com/docs/extensions/reference/api/cookies)、[Firefox cookies.getAll](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/cookies/getAll)、[Firefox scripting.executeScript](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/scripting/executeScript)、[Firefox 临时安装扩展](https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/)。
