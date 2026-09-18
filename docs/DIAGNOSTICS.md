# 日志和问题分析包

1.14.0 / Agent 0.10.0 增加本地意图硬门控、模型反馈后的实时搜索升级、四级搜索后端和受控 Python 文件 fallback。附件 `events.jsonl` 的确定根因是 `ai\_news.md` 中 `\_` 不是合法 JSON 转义；三次回复都在 `files.write` 派发前失败，所以 `steps: 0`，不是文件写入失败。新版本将原文保留在日志；字符串内只允许固定文件动作顶层 `arguments.path` 的 `\_` 做结构验证后的窄规范化，工具名、命令、网址、摘要、计划、正文和其他非法标点转义不会自动修复或派发。完整判据、依赖和复测见 [Agent 意图、搜索与协议诊断](AGENT_INTENT_SEARCH_AND_FALLBACK.md)。

1.12.0 / Agent 0.9.0 新增同轮网页恢复与上下文延续。关注 `adapter.recovery_started`、`adapter.recovery_target`、`adapter.recovery_retry`、`adapter.recovery_manual_required`、`adapter.recovery_observed`；`recovery_timeout` 的 `details.original_code` 保留初始失败原因。终端阶段为 `retrying`、`manual_retry_required`、`recovering`，详细正文仍只写 Agent 文件日志。续聊记录 `run.continued`，异常引用修复记录 `result.normalized` 并保留原文。`/context` 查看当前上下文摘要，`/new` 开始新会话；用法和限制见 [显示、网页恢复与上下文延续](RECOVERY_CONTEXT_AND_FORMATTING.md)。

1.11.0 / Agent 0.8.0 的 ChatGPT 发送诊断增加 `sendActionConflict`，区分发送按钮与语音/停止共用控件；发送、接收和完整回答核验继续分开记录。Skill 录制的开始、逐条步骤、结束、命名及确认结果写入全局 `agent.log`；草稿另存 `skill-drafts/`。`/pack --include-content` 可包含脱敏后的录制日志正文，默认诊断包不包含步骤正文，也不收集草稿目录。详见 [发送与录制说明](CHATGPT_SEND_AND_SKILL_RECORDING.md)。

1.10.0 / Agent 0.7.0 默认隐藏自动输出的工作目录、任务及结果路径；使用 `./run.sh --v`、`/verbose on` 或 `/logs` 查看。网页阶段以 `model.web_progress` 写入全局与本轮日志，保留请求关联、模型、阶段和观察到的 HTTP 状态；日志线程安全，结束后不再接受迟到回调。终端不输出原始 JSON 日志。新增 `tool.alias_resolved`、`tool.alternatives_found`、Skill 与历史调试事件，具体用法见 [补全、进度与历史](AGENT_COMPLETION_PROGRESS_AND_HISTORY.md)。

1.9.0 / Agent 0.6.0 支持交互 `/logs`、`/pack` 和独立 `./run.sh pack`。父服务自动启动输出写入 `logs/agent-parent-start.log`，该文件及轮转备份也会进入诊断包。启动前失败或取消同样保留任务结果；详细日志继续只写文件。附件分析与命令用法见 [交互与恢复说明](AGENT_INTERACTIVE_AND_RECOVERY.md)。

父应用将关键操作、实际输入和输出、核验结果同时输出到终端和日志文件，默认目录为项目下 `logs/`，包含 `launcher.log`、`electron.log` 和 `backend.log`。从 1.8.0 / Agent 0.5.0 起，Agent 的详细日志仅写文件，终端显示必要步骤、选择提示和格式化结果。Agent 全局日志位于 `desktop-agent/.runtime/logs/agent.log`。每个日志最多 5 MiB，保留 3 份轮转文件，后缀为 `.1` 等数字。设置 `FUSION_LOG_DIR` 可以指定父应用的日志目录；Agent 日志跟随其 `runtime_dir`。

默认记录对话和工具正文，已知 API 密钥、Bearer、Cookie/Authorization、密码及 URL 中的凭据会脱敏。可在启动前设置 `FUSION_LOG_CONTENT=0`，只保留阶段、状态和长度等元数据；Agent 也支持配置 `log_content:false` 或 `run --log-metadata-only`。关闭正文记录后，打包选项无法补回未记录的内容。

1.5.0 增加网页视图/输入占用管理和手动按钮检测；布局使用与 Qwen 发送复测见 [网页布局与发送诊断](VIEWS_AND_SEND.md)。

每轮 `desktop-agent/.runtime/runs/<run_id>/events.jsonl` 与全局日志逐条同步、立即 flush；每轮日志不轮转。成功、失败或停止都写 `result.md`，CLI 显示准确路径。JSON 提取、慢响应与升级规则见 [1.6.0 修复说明](WAIT_AND_PROTOCOL.md)，当前终端显示与修复流程见 [1.8.0 说明](AGENT_TERMINAL_AND_JSON.md)。

1.7.0 增加 Qwen 会话/模型保留及评分层关闭的逐步日志，Agent 增加 `environment.command.started/completed/failed` 安装步骤和当前解释器诊断，失败输出区分 `execution.status:not_started` 与 `outcome_unknown`。细节见 [本版说明](QWEN_SESSION_AND_AGENT_RECOVERY.md)。

## 怎样查看每一步

| 日志事件 | 可查看的操作和结果 |
| --- | --- |
| `http.chat_request` / `bridge.dispatch` | API 收到的消息、发往模型网页的实际提示和任务标识 |
| `adapter.input_dispatch` / `adapter.input_acknowledged` / `adapter.input_verified` | 输入投递、CDP 回执、网页文本完整匹配结果 |
| `input_lease.queued` / `input_lease.acquired` / `input_lease.released` | 等待、获得、释放网页输入区域；含当前布局、等待原因、可见性、尺寸与任务关联 |
| `adapter.focus_state` / `adapter.focus_emulation` | 输入前页面的可见与聚焦状态、焦点模拟回执 |
| `adapter.send_diagnostic` | 手动检测当前网页输入框、发送按钮、命中点、可见性与焦点；只读，不触发发送 |
| `adapter.send_state` / `adapter.dispatch` | 发送按钮的选择器或语义匹配、可用状态、目标元素、点击坐标和投递方式 |
| `adapter.cdp_started` / `adapter.cdp_acknowledged` / `adapter.cdp_failed` | 每条 CDP 输入命令是否被接收；失败时的具体阶段 |
| `adapter.submission_accepted` | 页面生成标识、新助手内容或匹配的新用户回合等接收证据 |
| `adapter.network_response` / `adapter.network_failed` | 监听期间页面 Fetch/XHR/文档请求的 HTTP 状态或网络失败；不采集请求头、Cookie 和网络响应正文 |
| `adapter.complete` / `bridge.result` / `http.chat_response` | 采集到的 Markdown、模型返回结果和本地 API 响应 |
| `model.http_request` / `model.http_response` / `model.http_error` | Agent 请求内容、HTTP 状态、服务端错误码和请求标识 |
| `model.raw_reply` / `model.invalid_protocol` | Agent 收到的原始回复、JSON 解析错误及格式修正次数 |
| `model.protocol_normalized` | 确定性格式归一化的原文、方法和已校验动作 |
| `model.repair_requested` / `model.protocol_repaired` | 请求模型纠正格式的原因、次数，以及通过校验后的修复结果 |
| `result.normalized` | 最终 Markdown 的字面量换行规范化、原文及结果 |
| `intent.classified` / `intent.action_blocked` | 本地硬门控的路由、理由与证据，以及回答型输入中被拒绝且未执行的环境动作 |
| `intent.feedback_assessed` / `intent.search_escalated` / `intent.search_satisfied` | 实时回答是否有当日/检索证据、为什么升级搜索，以及是否取得至少一个有效结果 |
| `web.search_started` / `web.search_backend` / `web.search_completed` / `web.search_failed` | 顺序或并行模式、每个后端状态/结果数/安全错误码/耗时；元数据事件不含 query |
| `tool.python_fallback_resolved` | 缺失的固定文件工具名称绑定到受控 `LocalTools`；仍需检查随后的授权、工具结果和回读 |
| `workspace.required` | 被拒绝的请求目标、实际工作目录与未解决失败 |
| `parent.checked` / `parent.start_requested` / `parent.ready` | 父 API/网页桥接状态、启动记录和就绪检查 |
| `parent.start_failed` / `parent.start_timeout` | 父启动器退出或就绪等待超时，附启动日志路径 |
| `interactive.command_completed` / `diagnostics.pack_completed` | 交互调试命令、打包结果；终端仅显示格式化摘要 |
| `tool.started` / `tool.completed` / `tool.failed` | 工具名、参数、返回值、退出码、耗时和核验状态 |
| `verification.required` / `verification.completed` / `agent.finished` | 尚待核验的动作、实际核验结果和任务终态 |

网页网络监听只覆盖 CDP 监听已启用期间；它可能包含站点后台请求，单个 HTTP 200 不能证明模型已接收提示或完成回答。输入正确、命令收到、网页接收和回答完成分别记录，不能互相替代。Qwen 的 `adapter.send_state` 可区分 `input_changed`、`send_missing`、`send_disabled`、`send_obscured` 等原因；细节见 [Qwen 发送修复说明](QWEN_SEND.md)。

Agent 用 `run_id` 串起本地任务，每次 HTTP 调用可通过 `http_id` / `request_id` 找到父应用记录，再用 `job_id` 定位某次网页操作。API 响应返回 `X-HTTP-ID`；已创建模型任务时还返回 `X-Request-ID`。HTTP 502 的错误正文保留具体来源错误，Agent 不会因失败自动重发 POST。

正文放在 `payload` 字符串中。较长内容分成多条日志，具有相同 `payload_id`，按从 1 开始的 `part` 排序，`parts` 表示段数。拼接所有 `payload` 后按 JSON 解析；不要逐段当作完整 JSON。单份载荷默认最多 2,000,000 字符，超限会设置 `truncated:true`；日志轮转或分析包裁剪也可能使段落不全，此时不能声称已取得完整内容。

## 生成分析包

复现问题后，在项目目录执行：

```bash
./package.sh
```

无需 root，也不安装额外依赖。默认生成 `diagnostics/multillm-fusion-diagnostics-时间.tar.gz`，终端会打印完整路径。命令只在本地生成文件，不会自动上传。将该文件作为附件上传即可用于分析。

```bash
# 指定输出文件；不会覆盖已有文件
./package.sh --output "$PWD/chatgpt-send-problem.tar.gz"

# 排查 Markdown 内容提取/融合：主动包含已保存的原始及融合 Markdown
./package.sh --include-markdown

# 排查提示词、JSON 动作协议或工具输出：保留脱敏后的日志正文
./package.sh --include-content

# 同时包含日志正文及父应用保存的 Markdown
./package.sh --include-content --include-markdown

# 使用此前启动应用时相同的数据和日志目录
FUSION_DATA_DIR=/path/to/app-data FUSION_LOG_DIR=/path/to/logs ./package.sh

./package.sh --help

# Agent 使用自定义 runtime_dir 时，额外指定其目录
./package.sh --agent-runtime-dir /path/to/agent-runtime

# 额外选择某次任务的事件日志，正文选项仍需显式启用
./package.sh --include-content --agent-run-id 20260909T032443Z-69d70e4c
```

`--include-content` 保留原日志中已记录并脱敏的发送内容、模型回复和工具参数/结果；默认打包会去除这些正文。`--include-markdown` 独立控制数据目录 `runs/` 下的 `.md` 文件。两者都可能包含用户内容，启用后终端会提示。如果网页提取失败，可能尚未生成相应 Markdown，日志仍可以定位失败阶段。

## 压缩包内容

| 路径 | 内容 |
| --- | --- |
| `code/` | 允许的项目源码、依赖清单、测试、文档和扩展文件；移除已知密钥及明显密钥字符串赋值，主要用于分析 |
| `logs/current/` | 当前日志目录的 electron/backend/launcher 及 agent-parent-start 日志和数字后缀轮转日志 |
| `logs/legacy/` | 兼容早期数据目录内的同名日志 |
| `logs/agent/` | `desktop-agent/.runtime/logs/agent.log` 及数字后缀轮转日志，可用 `--agent-runtime-dir` 指定运行目录 |
| `logs/agent/runs/<run_id>/events.jsonl` | 仅通过 `--agent-run-id` 显式选择的任务事件日志，可重复指定 |
| `config-summary.json` | 白名单配置摘要：启用模型、URL、网页选择器、融合方式、超时；密钥/代理仅标记是否配置 |
| `environment.json` | 应用、Python、Node、Electron 版本以及内核、架构和显示环境是否存在 |
| `manifest.json` | 每个实际收集文件的大小、SHA-256、类别，以及跳过/截断/文件变化等提示；不包含自身哈希 |

数据目录来自 `FUSION_DATA_DIR`，默认 `~/.local/share/multillm-fusion`。打包只读取其中指定的配置、API token（仅用于脱敏，不加入压缩包）和旧日志；仅启用 `--include-markdown` 才读取 `runs/` 下的 Markdown。

默认不打包对话 Markdown、SQLite 数据库、浏览器账号目录、Cookie、登录导出 JSON、完整私有配置、API key 文件、`.env`、`node_modules`、`.venv`、`.git`、core dump 或已有压缩包。不会跟随符号链接。浏览器登录信息的读取/导入功能不会因打包而触发。

日志中的已知配置密钥、Bearer、Cookie/Authorization、URL 用户名密码及查询参数值会脱敏；默认也会移除结构化日志中的 `payload`、消息、回答、Markdown、工具参数和结果等正文键。`--include-content` 只取消正文移除，凭据脱敏仍生效。对任意旧日志中未标记的自然语言或手工写入源码的非标准秘密，自动脱敏不能保证全部识别，分享前可解压检查。

每个源码/Markdown 文件最多 2 MiB，每个日志最多保留最近 4 MiB 的完整行，载荷总量最多 64 MiB、1,500 个文件。超限、文件锁定/消失、权限不足等情况写入 `manifest.json`，尽量继续收集；即使暂时没有日志也会生成分析包。压缩包权限为 `600`，新建默认输出目录权限为 `700`。

这是问题分析包，源码可能经过密钥脱敏，不能替代正常发布包或数据备份。

1.3.0 起包含 `desktop-agent/` 的源码、依赖清单、Skill 示例、测试和文档，只扫描固定的 `src/skills/tests/docs` 子目录及允许的顶层文件。Agent 的 `agent.config.json` 仅用于确定需要排除的工作区、运行目录和密钥文件路径，不复制其内容；配置损坏时跳过 Agent 源码收集并写入清单提示。`--agent-runtime-dir` 指定的路径同样从 Agent 源码中排除。请将自定义工作区和运行目录放在这些源码目录之外。

Agent 的完整任务记录、工作区、浏览器配置、截图和私有配置在使用 `--include-content` 或 `--include-markdown` 时也不会收集。`--include-markdown` 只针对父应用保存的对话 Markdown；`--include-content` 可保留 Agent 日志中已有的任务、命令参数和工具结果，不会读取被排除的记录文件。

`--agent-run-id` 只额外读取所选任务的 `events.jsonl`，沿用日志大小限制和脱敏规则；缺失或链接会写入清单提示。它不收集 `result.md`、`transcript.json` 或 `state.json`。
