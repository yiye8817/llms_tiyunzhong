# 浏览器登录导入（1.13.0）

可把你在 Firefox、Chrome、Chromium、Brave、Edge、Vivaldi、Opera 中的模型网页登录信息单向导入 MultiLLM Fusion。直接读取适用于 Cookie 登录；扩展文件方式还支持当前模型网页的 localStorage，适合部分把登录令牌保存在网页存储中的站点。

**这是按需导入，不是持续双向同步。** 在原浏览器重新登录后可再次导入；原浏览器退出登录不会保证 Electron 同时退出。程序显示“已导入”的数量，实际是否被站点接受需要在右侧网页确认。

## 升级与打开

将 1.13.0 源码解压到新目录后运行 `./run.sh`。已有 `~/.local/share/multillm-fusion` 的配置、登录、会话历史继续复用；不需要删除这个目录。旧 schema 1/2 配置会追加缺失的 GLM 与 Kimi，两者均保持默认关闭；已存在的同名项及其 URL、代理、选择器、启用状态和排列顺序不会被覆盖。

首次运行会尝试安装 `requirements-browser.txt` 中的 Chromium Cookie 解密依赖。它安装失败时，应用仍可启动，Firefox 直接读取及扩展文件导入仍可使用。

```bash
./run.sh
# 或只补装可选的 Chromium 解密依赖：
.venv/bin/python -m pip install -r requirements-browser.txt
```

在桌面界面打开 **设置 → 浏览器登录**。

GLM 和 Kimi 默认不显示在导入目标中。先在 **设置 → 模型网页** 勾选“参与回答”并保存，然后返回“浏览器登录”选择对应模型。

## 方式一：从浏览器配置直接导入

1. 在 Chrome、Firefox 等源浏览器中登录目标模型网站。
2. 进入应用的“浏览器登录”页，检测本机配置。
3. 选择浏览器和用户配置。Chrome 的 Default、Profile 1 等以及 Firefox 的各个 profile 会分别列出。
4. 选择目标模型，点击“从浏览器导入登录”。
5. 查看 Cookie 导入数量、跳过原因，然后回到模型标签确认登录。

仅查询当前目标模型域名的 Cookie；不读取浏览器保存的密码，也不会复制整个浏览器配置目录。原浏览器数据库以只读事务访问，使用包含 WAL 更新的快照。浏览器数据库或密钥环无法访问时，正常关闭源浏览器后重试，或改用方式二；程序不结束浏览器进程、不破解或关闭密钥环保护。

| 浏览器类型 | 读取方式 | 常见限制 |
|---|---|---|
| Firefox | 直接读取选定 profile 的 cookies.sqlite | Container/分区 Cookie 跳过；Cookie 以外的登录数据用扩展方式 |
| Chrome、Chromium、Brave、Edge、Vivaldi、Opera | 读取选定 profile 的 Cookie 数据库，支持时通过 browser-cookie3 调用桌面密钥环解密 | 需要在自己的桌面会话中运行；系统可能提示允许密钥环访问；无法解密时给出提示 |
| Snap / Flatpak 安装 | 检测常见安装位置及 profile 路径 | 沙箱和文件权限可能禁止读取；可改用源浏览器内的扩展方式 |

请勿使用 sudo 启动程序。SSH、容器或不带桌面会话的进程可能无法使用 GNOME Keyring / KWallet。程序不会把系统登录密码当作通用“解密密码”，也不会修改浏览器加密配置。

## 方式二：浏览器扩展导出后导入

如果直接导入后仍未登录，原因可能是网站需要 localStorage，或者源 Cookie 数据库被锁定/无法解密。随源码提供的扩展通过浏览器公开扩展接口读取**当前模型站点**的 Cookie 与当前页面 localStorage。

### Chrome / Chromium / Brave / Edge / Vivaldi

1. 打开对应浏览器的扩展管理页，例如 Chrome 的 `chrome://extensions`、Brave 的 `brave://extensions`、Edge 的 `edge://extensions`。
2. 打开开发者模式，选择“加载已解压的扩展程序”，选中源码中的 `browser-extension/chromium` 文件夹。
3. 打开已登录的模型对话页，点击扩展图标，在弹窗中导出该站点登录文件。
4. 回到 MultiLLM Fusion 的“浏览器登录”页，选择同一目标模型，点击“导入登录文件”，选择刚才的 JSON 文件。

### Firefox

1. 打开 `about:debugging#/runtime/this-firefox`。
2. 选择“临时载入附加组件”，选中 `browser-extension/firefox/manifest.json`。
3. 在已登录的模型对话页点击扩展图标并导出，再从应用导入 JSON 文件。
4. 临时扩展在 Firefox 重启后需要重新加载。该源码包未提供 Mozilla 签名的永久安装版。

扩展不会上传登录数据；只有点击导出时才读取当前支持站点。导出的文件包含登录会话信息，**不要发给别人或上传到聊天、网盘及代码仓库**；完成本机导入后可删除文件。

## 支持的站点范围

| 模型 | 当前页面 localStorage 来源 | 可导入 Cookie 域 |
|---|---|---|
| ChatGPT | https://chatgpt.com | chatgpt.com、openai.com |
| DeepSeek | https://chat.deepseek.com | deepseek.com |
| Qwen | https://chat.qwen.ai、https://chat.qwenlm.ai | qwen.ai、qwenlm.ai |
| Claude | https://claude.ai | claude.ai |
| Grok | https://grok.com | grok.com |
| GLM（智谱清言 / Z.AI） | https://chatglm.cn、https://www.chatglm.cn、https://z.ai、https://chat.z.ai | chatglm.cn、z.ai |
| Kimi | https://kimi.com、https://www.kimi.com | kimi.com |

域名匹配必须完整相等或属于其真实子域，`fakechatgpt.com` 等相似字符串会拒绝。localStorage 仅写入与目标模型配置完全相同的 HTTPS origin；跳转到登录域或其他 origin 时不会在那里注入数据。范围定义在源码 `browser-login-sites.json`，扩展包包含对应副本。

GLM 默认配置为 [Z.AI](https://chat.z.ai/)；单个 provider 一次只绑定一个 origin。Kimi 默认使用 [Kimi 官方对话页](https://www.kimi.com/)。详细启用与 API 用法见 [GLM / Kimi 网页模型](GLM_KIMI.md)。

不导入 Google、GitHub、X 等通用身份提供商的登录 Cookie。目标模型站点已经完成 OAuth 登录后，优先导入该模型自己的登录会话。

## 导入过程与结果

导入时暂时占用模型网页：应用会停止该标签的加载，先离开正在运行的站点页面，写入目标 session Cookie，然后加载模型网页。文件中的 localStorage 如适用会在确认目标 origin 后写入并刷新。其他 API 请求、配置变更或导入不会在中途交错执行。

导入采用合并更新，不清空整个 Electron Cookie 库。Cookie 的 host-only、路径、Secure、HttpOnly、SameSite 和有效期会尽量保持；无法兼容的项目会跳过并报告。会话 Cookie 不会被偷偷改成长期 Cookie，应用重启后可能需要重新导入。

“Cookie 导入完成”只表示 Electron 接受了 Cookie。站点可能已撤销会话，或将会话与设备、浏览器、验证码、IndexedDB、Passkey 等其他状态绑定；这些情况仍需要手动登录。分区 Cookie、Firefox Container、IndexedDB、Service Worker 和密码管理器不在本次导入范围内。

取消或超时后不再安排新的写入；已经提交给浏览器的 Cookie / localStorage 写入可能完成，已导入内容不会自动回滚。应用会等待这些写入结束后解除本地锁。如果操作中断或出现部分失败，请先查看模型标签，再按需要重试。界面及日志只显示数量和诊断，不显示 Cookie 值或 localStorage 值；直接导入的秘密内容通过 Python 与 Electron 主进程之间的本机管道传递，不经过 OpenAI 兼容 HTTP API。

## 登录时机与 origin 绑定

模型任务开始后，输入、发送、错误重试和回答采集都绑定到该 provider 配置 URL 的 origin。如果在任务期间跳到通用身份站、其他模型站或不同的 GLM origin，程序会以 `provider_origin_changed` 明确停止本轮，不读取、记录或点击跨站页面，也不会把它的内容当成模型回答。

需要 OAuth、验证码或跨站登录时：

1. 等待当前请求完成或取消，确认应用处于空闲状态。
2. 打开目标模型标签，完成站点的正常登录与验证。程序不绕过这些验证。
3. 登录完成后返回该 provider 配置的模型页，确认地址已回到同一 origin，再发起新请求或使用 Agent 的“继续”。

## 实现依据与验证

- [Electron Cookie API](https://www.electronjs.org/docs/latest/api/cookies)：主进程 session Cookie 写入和持久化。
- [browser-cookie3](https://github.com/borisbabic/browser_cookie3)：支持的浏览器和 Linux Cookie 解密读取接口。
- 源码自带合成 SQLite、模拟 Electron session、模拟扩展 API 和 UI 测试；没有真实 GLM、Kimi 或 Qwen 账号，也未在用户的 Electron/Linux 桌面完成实站登录、系统密钥环和完整浏览器导入验证。详细结果见 `docs/TESTING.md`。
