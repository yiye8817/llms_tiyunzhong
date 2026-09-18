# 1.14.0 / Agent 0.10.0：意图、实时搜索与协议诊断

本文适用于父应用 1.14.0 与 `desktop-agent` 0.10.0。两部分必须同时升级并重启父应用；旧任务记录可以继续查询，但不会在原进程外恢复成可执行会话。

## 附件 `events.jsonl` 的确定根因

附件共有 89 条事件。当前运行中出现三组相同的 `model.raw_reply` → `model.invalid_protocol`，修复计数依次为 0、1、2；最后 `agent.finished` 为 `invalid_protocol`、`steps: 0`。期间没有 `tool.attempt` 或 `tool.started`，因此这不是 `files.write` 写入失败，也不是写后核验失败：动作从未通过 JSON 协议层。

首个确定错误在第 1 行第 70 列：模型给出的路径是 `ai\_news.md`。JSON 允许 `\\`、`\n`、`\t`、`\uXXXX` 等转义，但不允许单个 `\_`，所以旧解析器报 `Invalid \escape`。同一回复还同时带有三类网页 Markdown 序列化污染：

- `plan` 数组被写成 `\[` 和 `\]`；旧版已经识别并移除了这两个字符串外反斜杠。
- Markdown 正文中的段落分隔被二次编码为字面量 `\n`。
- 下一个列表编号被挤入链接标签，目标又附加 `/n/n2` 一类伪路径；末尾链接带 `/n`。

因此旧版即使两次请求模型修复，Qwen 仍返回同一个非法 `\_`，任务只能在第 0 步停止。附件中的原始数据足以证明这个协议根因，但不能证明此前网页搜索结果的真实性，也不能证明任何文件已经产生。

## 0.10.0 的窄修复

解析顺序现在是：

1. 保留 `model.raw_reply` 原文，再尝试严格 JSON 解析。
2. 仅移除可确定的网页 Markdown 结构污染：首部 BOM、字符串外的 `\[` / `\]` 和合法值后的尾逗号。JSON 字符串只有一个例外：固定文件动作（`files.read/list/write` 及 `file.read/list/write`）顶层 `arguments.path` 中的非法 `\_`。解析器用源文中不存在的唯一标记做严格探测，确认所有标记都只在该字段后，才把附件的 `ai\_news.md` 恢复为 `ai_news.md`。
3. 再次进行严格解析，并继续拒绝重复字段、非有限数字、多个对象、对象外说明、未转义嵌套引号及截断对象。工具名、shell 参数、浏览器 URL、`summary`、`plan`、文件内容和其他非法标点转义不会自动修复，也不会派发动作。合法 JSON 转义和编码为 `\\` 的真实反斜杠保持不变；解析器不会猜测缺失命令或补齐截断内容。
4. 只有动作已经解析为 `files.write`，且目标后缀是 `.md` 或 `.markdown` 时，才对可唯一确认的正文损坏做第二层规范化：恢复明确的段落/列表换行，并将“可见完整 URL + `/n/nN` + 被挤出的编号”恢复为原 URL 与编号。非 Markdown 文件、代码块、行内代码、普通 URL、不匹配的路径和歧义内容不改写。
5. 规范化后的动作仍须通过真实工具 schema、能力授权、工作区与符号链接边界、覆盖规则，最后由 `files.write` 逐字节回读核验。

`model.protocol_normalized` 会记录变更类型、原始位置、原始回复和规范化动作。附件形态成功时应出现 `markdown_json_string_escapes`、`escaped_markdown_line_breaks`、`polluted_full_url_link` 等记录，随后才允许出现 `tool.attempt`。如果仍是 `model.invalid_protocol` 且没有 `tool.attempt`，文件工具仍未执行，不应去排查文件权限来替代协议问题。

## 首轮意图硬门控

Agent 不再把所有对话都交给工具循环。分类完全在本地完成并带有 `confidence`、`reasons`、`evidence`，不是由另一个不透明分类模型决定。

| 路由 | 进入条件 | 允许的下一步 |
| --- | --- | --- |
| `chat` | 社交消息或没有命中实时/动作硬门控的普通输入 | 所选网页模型直接返回 `final` |
| `knowledge_query` | 稳定信息、解释、比较、教程等，且没有明确环境操作指令 | 所选网页模型直接返回 `final` |
| `realtime_query` | 明确联网搜索；或最新/实时/今日等时间证据与易变主题；或“AI 新闻”“北京天气”“BTC 价格”等短易变主题 | 先取得模型反馈，再执行时效门控；必要时只读搜索 |
| `task_action` | 环境动作动词、明确目标和指令证据同时存在，且没有否定/纯教程语境 | 进入工具循环，但仍须逐项授权和核验 |

示例：“写一首诗”属于回答内容；“把这首诗写入 `poem.md`”才属于环境动作。“如何读取文件”是知识问题；“读取 `report.md` 并总结”是文件任务。聊天或知识路由中的模型工具动作会被 `intent.action_blocked` 拒绝。`side_effect_gate_open` 只说明用户文字明确要求了副作用，不会打开 `shell`、`browser`、`desktop` 或越过 `files` 边界。

动作型路由还持久化 `requested_capabilities`、`mutating_capabilities` 和 `requested_targets`。运行器要求工具 capability 属于本轮明确请求的范围；修改型工具还必须属于明确的 mutation 范围。文件写入必须匹配用户给出的路径，明确 URL 的 `browser.open` 也必须保持该地址。完成门控进一步绑定操作族与每个目标：读取不能证明写入，`browser.open` 不能证明 click，`desktop.move` 不能证明 click，用户列出的每个写入目标都要分别成功。对于“把 SOURCE1 和 SOURCE2 的内容保存到 DEST”一类明确结构，来源只匹配读取，目标才匹配写入；模型若交换来源和目标，会在派发前以 `operation_target_not_requested` 拒绝。要求浏览器操作不会顺带打开 shell 或文件权限；依赖实时信息的修改在 freshness gate 或必要搜索完成前以 `freshness_required_before_mutation` 阻止。之后仍继续执行工具目录、schema、配置授权、工作区和核验检查。

同一次交互中的“继续”继承上一条未完成任务的路由与实际进度，不会把回答型任务升级为副作用任务，也不会重复已经满足的同一搜索。补充要求会结合原目标重新分类。失败或步数停止后若本轮明确提出新的环境操作，完成门控按 capability、操作族、mutation 类型和目标建立本轮证据边界：旧的 `desktop.click` 不能证明新点击，但“用中文回答”等纯输出补充不会导致已核验副作用重放。上一任务已经完成后，明显的新任务只保留自然语言对话背景，旧点击、命令、写入、失败和实时性状态不会成为新任务的成功证明；随后再输入“继续”会绑定这个新目标。`/new` 仍可用于显式清空全部对话背景。

## 根据反馈升级实时搜索

`realtime_query` 先让当前模型按自己实际拥有的信息回答。运行器随后执行 freshness gate：

- 用户明确要求联网搜索、网页搜索或并行搜索时，模型记忆不能代替搜索，始终升级。
- 模型返回空答，或明确说明知识截止、无法联网、不能确认最新信息时，升级。
- 模型给出本日日期或明确的“已实时检索/获取于某时”证据时，可以直接完成；来源 URL 是辅助证据，不是必需条件。
- 只有 URL、没有当日时间或实际检索痕迹时，仍升级，避免把看似合理的旧回答当成实时结果。

正常日志顺序是 `intent.classified` → 模型响应 → `intent.feedback_assessed`；需要升级时继续出现 `intent.search_escalated` → `tool.attempt(web.search)` → `web.search_started` / `web.search_backend` / `web.search_completed` → `intent.search_satisfied`，最后模型根据结果整合并 `run.completed`。这些事件来自真实状态，不会由定时器补造。

`web.search` 使用独立的只读 `web` capability，不会因此取得可操作页面的 `browser` 权限。默认配置已经允许 `web`；若自定义配置移除了它，交互模式可在首次调用时确认，非交互模式必须预先使用 `--allow web`，否则得到 `capability_denied`，不会悄悄改用 browser、shell 或 Python 绕过授权。

## 四级搜索后端

公开参数为：`query` 必填且 1–2048 字符，`mode` 为 `sequential` 或 `parallel`，`max_results` 为 1–20（默认 8），`timeout_seconds` 为 1–120（默认 15）。

| 顺序 | 后端与真实调用 | 依赖与降级 |
| --- | --- | --- |
| 1 | `ddgo`：Python 标准库向 `https://html.duckduckgo.com/html/` 发送 HTML POST，并解析标题、链接和摘要 | 无第三方包；`ddgo` 只是本项目诊断名称，不是必须安装的命令。网络、HTTP、内容类型、大小或解析失败时继续 |
| 2 | `browser_use`：执行裸命令 `browser-use`，通过 stdin 传入固定 Python 模板；查询和数量只经受限环境变量传入 | 当前 CLI 3 需要 Python 3.11+，可作为独立 CLI 环境安装；不存在时记录 `dependency_missing` / `unavailable`。不启动 Browser Use Agent、不调用第二个 LLM、不向子进程传模型/API 凭据 |
| 3 | `opencli`：`opencli duckduckgo search <query> --limit <N> -f json` | Node.js 20.18.1+、Chrome/Chromium 与 Browser Bridge；内部把单页数量限制到上游支持的 10。子进程仅继承运行与 bridge 所需白名单环境，不继承 Fusion token 或模型 API Key。退出码 66 视为空结果，69/75/77/78 分别报告 bridge、超时、认证、配置问题，然后继续 |
| 4 | `playwright`：用 Agent 当前解释器的 `playwright.sync_api` 启动独立无头 Chromium，读取 DuckDuckGo HTML 结果 | 需要 Python 包及 Chromium；不存在时为 `unavailable`，启动/页面/选择器失败时报告后端错误 |

`sequential` 是默认模式：依次调用，首个有效结果即停止，最适合普通查询。运行器在派发前同时绑定查询指纹和搜索模式，模型不能把普通查询擅自扩成四路调用。`parallel` 仅在用户明确说“并行/多路搜索”时自动选择；它同时运行可用四路，在总超时内收集结果，再按固定优先级合并和 URL 去重。外部 CLI 可能共享本机浏览器资源，出现焦点、daemon 或 bridge 争用时应退回顺序模式，而不是无限重试。

缺少 Browser Use、OpenCLI 或 Playwright 不会阻断后面的后端。四路都没有有效结果时才返回 `search_failed`，其中 `attempts` 保留每路 `status`、`result_count`、安全错误码和耗时。生产 CLI 使用无 shell 的独立进程组，stdout 在解析前流式执行硬上限，stderr 只排空；超时、输出超限或异常会清理进程组及后代。Playwright 的启动、导航与选择器等待共享一个总截止时间。`web.search_backend` 仅写 method/provider/status/count/code/elapsed 等元数据，不写查询正文；查询和结果仍由 ToolRegistry 的脱敏 payload 写入文件日志。

所有结果都经过边界处理：只接受带标题的公开 HTTP(S) URL，拒绝用户名/密码 URL、尾点 localhost、特殊地址及整数/十六进制/八进制/畸形 dotted IPv4 写法，移除片段、常见跟踪参数、云签名及递归编码的疑似 token/cookie 查询参数，并限制响应、子进程输出、标题和摘要长度。搜索结果仍是不可信网页观察，关键事实应回到来源页面核对。Browser Use/OpenCLI 若配置了共享 CDP/Bridge，可能临时打开固定 DuckDuckGo 标签并在采集后关闭；不希望接触共享会话时不要传入这类配置，使用标准库 DDG 或独立无头 Playwright。

## 外部依赖检查

```bash
cd desktop-agent
./run.sh doctor
command -v browser-use && browser-use --doctor
command -v opencli && node --version && opencli doctor
./setup.sh --browser
./run.sh tools
```

`doctor` 的 `optional_cli` 会分别显示 `browser-use` 和 `opencli` 是否在当前 `PATH`。Browser Use 上游当前要求 Python 3.11+，项目自身仍支持 Python 3.10；不要为了这个可选后端破坏 Agent 当前解释器，可用独立的 Python 3.12 工具环境安装并保证可执行文件在 `PATH`。OpenCLI 的 bridge 或登录态失效时先运行 `opencli doctor` 并在 Chrome/Chromium 处理，不要把同一失败命令循环执行。

上游接口参考：

- [Browser Use CLI 3 源码与 stdin 入口](https://github.com/browser-use/browser-use/blob/main/browser_use/cli.py)
- [Browser Use PyPI（Python 版本要求）](https://pypi.org/project/browser-use/)
- [OpenCLI DuckDuckGo search 适配器](https://github.com/jackwener/OpenCLI/blob/main/clis/duckduckgo/search.js)
- [OpenCLI 中文安装、Browser Bridge 与 doctor](https://github.com/jackwener/OpenCLI/blob/main/README.zh-CN.md)
- [Playwright Python 安装](https://playwright.dev/python/docs/library)

## 受控 Python 文件 fallback

该能力只在正常工具查找失败后匹配固定名称表：

- 读取：`file.read`、`filesystem.read`、`filesystem.read_file`、`read_file`、`read_text_file`。
- 列目录：`file.list`、`filesystem.list`、`filesystem.list_files`、`list_files`、`list_directory`。
- 写入：`file.write`、`filesystem.write`、`filesystem.write_file`、`filesystem.write_text`、`write_file`、`write_text_file`。

规范 `files.read`、`files.list`、`files.write` 已存在时优先调用已注册实现，记为 `tool.alias_resolved`；确实缺失时才装载同一个 `LocalTools` Python 实现，记为 `tool.python_fallback_resolved`。参数原样保留并重新校验。

这不是通用代码生成：`python`、`python.run`、`eval`、`execute` 和其他相似名称都不匹配；模型不能提供 Python 源码、handler 或 shell 命令。fallback 不能绕过 `files` capability、workspace、符号链接、大小、原子写、覆盖保护或写后回读。若权限被拒、路径逃逸或未授权覆盖，执行状态为 `not_started`，目标文件保持不变。

## 复测与日志判据

在 `desktop-agent/` 执行：

```bash
PYTHONPATH=src:tests python3 -m unittest -v \
  tests.test_protocol_compat tests.test_protocol_repair \
  tests.test_markdown_document_normalization \
  tests.test_events_log_markdown_write \
  tests.test_intent tests.test_intent_runtime \
  tests.test_runtime_intent_hard_gates \
  tests.test_web_search tests.test_terminal \
  tests.test_python_fallback \
  tests.test_runtime_python_fallback

PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
bash -n run.sh setup.sh
```

0.10.0 当前 Agent 全量为 612 项通过。上方直接列出的专项模块合计 148 项：意图与运行器硬门控 64 项，`web.search` 和终端事件 37 项，协议/Markdown 端到端 31 项，Python fallback 16 项。测试使用本地 HTTP/临时文件与模拟搜索后端，不访问真实模型账号，也不证明 DuckDuckGo、Browser Use、OpenCLI 或 Playwright 在用户网络中一定可达。

目标环境建议依次执行：

```bash
# 应直接回答，日志中不应有 tool.attempt
./run.sh run "解释 Linux namespace 的基本原理" --model qwen

# 模型反馈缺少实时证据时应升级；默认配置已经预授权只读 web
./run.sh run "AI 新闻" --model qwen --non-interactive

# 明确选择 parallel；四路 attempts 应写入文件日志
./run.sh run "并行搜索今天的 AI 新闻并列出来源" --model qwen --non-interactive
```

检查 `.runtime/runs/<run_id>/events.jsonl` 时，可按以下顺序判断：

| 现象 | 结论与下一步 |
| --- | --- |
| `model.invalid_protocol`，没有 `tool.attempt` | 动作没有执行；先看错误列和 `raw_reply`，不能当作文件或命令失败 |
| `model.protocol_normalized` 后有 `tool.attempt(files.write)` | 已通过窄规范化；继续检查 `tool.completed`、verification 与写后回读，不能仅凭派发判断成功 |
| `intent.classified` 为 `knowledge_query`，没有搜索 | 预期直接回答；若问题确实要求最新信息，应明确补充时间或“联网搜索” |
| `intent.feedback_assessed` 表示需要搜索，但随后 `capability_denied` | 自定义配置未授权 `web`；交互确认或非交互增加 `--allow web` 后重试 |
| 某后端为 `unavailable`，后续后端继续 | 可选依赖缺失的正常降级；按上方 doctor 命令修复该后端即可 |
| `web.search_failed` / `search_failed` | 四路均无有效结果；查看 `attempts` 区分网络、依赖、bridge、认证、配置和超时，不要把旧模型回答标为实时完成 |

需要上传分析包时，在父项目目录执行：

```bash
./package.sh --include-content --agent-run-id <run_id>
```

默认打包会移除正文；排查本附件这类非法转义必须显式 `--include-content`，否则只能看到错误元数据。打包仍会脱敏凭据，并排除完整 transcript、workspace、截图、浏览器配置和账号会话。

## 验证边界

自动测试证明的是解析、路由、授权、降级和日志状态机，不等于真实网站可用性。DuckDuckGo 可能限流或改变 HTML；Browser Use 和 OpenCLI 可能受本机浏览器、daemon、扩展、账号或版本影响；Playwright 可能缺系统动态库或受用户命名空间策略限制。发生现场差异时保留失败 `attempts` 和 run id，按单路依赖检查后再决定重试，不自动重放可能产生副作用的旧动作。
