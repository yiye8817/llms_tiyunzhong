# 1.17.1：GLM 选择 glm-5.3-flash 后不再每轮刷新

## 使用方式

在设置中启用 GLM，打开 GLM 网页并完成登录，在网站自己的模型选择器中手动选择 `glm-5.3-flash`。之后从统一对话或本地 API 发送任务，已打开且与配置网址同源的 GLM 页面不再执行每轮 `loadURL()`。

本次修改保留网页中的手动选择，不新增或猜测远端模型接口，也不替账号开通模型权限。实际可选模型以网页及账号提供的选项为准。本地 API 的 provider 名称仍是 `glm` / `web-glm`，不是新增 `model: "glm-5.3-flash"` 的 API 别名。

## 修改行为

- GLM 与 Qwen 共同使用页面复用与干净会话准备流程。空白会话直接发送；已有消息时，通过明确的新建对话控件执行一次可信点击，等待旧消息清空后再输入本轮完整逻辑历史。不把完整历史重复追加到旧的网页会话中。
- 新建对话期间拦截 `will-navigate` / `will-redirect` 所报告的整页导航，不以刷新作为新建失败的后备方案。不能确认新会话时停止，不重复点击或发送。
- 可识别的 GLM 模型选择器支持 `glm-5.3-flash`、`GLM-5.3-Flash`、`GLM 5.3 Flash` 等大小写、空格及连字符差异。只读取当前选择控件；不把历史回答、菜单选项、列出多个模型的容器或推广按钮当成当前模型。
- 新建对话前后及发送前校验可识别的型号。发现型号变化时返回 `glm_model_changed`；原先可识别的型号在发送前不可确认时返回 `glm_model_unverified`，不悄悄用默认模型发送。
- 原有草稿不会被清空。找不到新建对话按钮时返回 `glm_new_chat_required`，请在网页手动新建空白会话或调整 `new_chat` 选择器。
- 旧配置无需迁移或重置。GLM 的英文新建对话、侧栏按钮候选同时加入运行时回退选择器，因此保留原有配置文件仍可使用。Qwen 的评分面板处理仍只用于 Qwen，不在 GLM 上自动点击评分面板。

## 仍可能加载网页的场景

首次打开、重启应用、用户主动点击刷新、修改站点 URL 或代理、显式导入登录信息等原有页面加载流程不受本修改影响。普通保存名称、选择器或限速参数不会主动重载相同 URL/代理的网页。这里的“不刷新”指程序不再为每次 GLM 生成任务主动重新加载整个页面，不禁止用户主动刷新，也不能保证网站自身在所有情况下都不刷新或重置选择。

如果网站内部的新建对话操作本身重置了模型，本版会在能够识别这一变化时停止并提示重新选择，不会冒充仍在使用 Flash。如果无法可靠读取型号，则只记录 `no_reload_unverified_model`，不声称已经核实服务器实际使用的型号。

## 诊断信息

关键事件为 `adapter.navigation_reused`（`reason=glm_preserve_selected_model`）、`adapter.glm_session_inspected`、`adapter.glm_session_ready`、`adapter.glm_new_chat_target` 和 `adapter.glm_full_navigation_blocked`。模型标识位于原有诊断日志的 payload 中，沿用现有脱敏与打包规则。

## 本次验证记录

2026-09-11，在当前容器使用 Node 22.16.0、Python 3.13 执行：

| 检查 | 结果与范围 |
| --- | --- |
| GLM 专项无外部依赖测试 | **32 项通过**；执行实际适配器源码，模拟 DOM/CDP 边界及 HTML 转换，覆盖连续两轮零加载、首次单次加载、草稿保留、新建失败、模型变化、标签归一化、跨站限制、取消、配置保存等 |
| 其他无外部依赖 Node 回归 | **77 项通过**；启动器、布局、登录导入、诊断及恢复权限；合计 **109 项 Node 测试通过** |
| JavaScript 语法 | `npm run check` 通过；新增的两个 GLM 测试文件和修改的 origin 测试均另行通过 `node --check` |
| 父项目 Python 全量测试 | 执行 **120 项，119 项通过，1 项错误**。`test_backend.HTTPTests.test_auth_host_and_strict_api` 的非法 Host 用例抛出 `ValueError: 'evil' does not appear to be an IPv4 or IPv6 address`。对未修改的原包单独执行该用例，同样复现；本次没有改动后端代码 |
| 完整 npm / JSDOM / Electron | **未完成**；当前容器没有项目所需的 npm 依赖，安装时无法解析 npm registry。新增 `tests/test_glm_session.cjs` 的 13 项真实 DOM 结构测试已编写并通过语法检查，但未执行；未将它们计入通过数 |
| 真实 Z.ai 账号 | **未验证**；没有在用户的 Linux/Electron、登录状态及实站 DOM 上验证，不承诺账号能选择该模型或服务器一定使用某个型号 |

本地已执行的 Node 命令：

```bash
node --test tests/test_glm_no_reload.cjs \
  tests/test_node_diagnostics.cjs tests/test_launcher.cjs \
  tests/test_provider_layout.cjs tests/test_browser_import.cjs \
  tests/test_recovery_access.cjs
npm run check
```

在已有完整依赖的项目中可运行 DOM 专项及全量回归：

```bash
node --test tests/test_glm_session.cjs tests/test_qwen_session.cjs \
  tests/test_glm_kimi_send.cjs tests/test_provider_origin.cjs
npm test
```

## 更新

完整包保留原有父应用和 `desktop-agent/` 功能，父应用版本为 `1.17.1`，Agent 版本不变。源码包不包含 npm 依赖、Python 虚拟环境或浏览器登录数据。关闭旧应用后解压运行 `./run.sh`；已有安装目录可应用同版本配套的统一 diff 补丁，再运行 `./run.sh --skip-install`。默认数据目录和现有登录分区不变，不需要删除配置或重新创建数据目录。
