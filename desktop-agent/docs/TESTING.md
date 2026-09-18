# Desktop Agent 验证记录

## 0.10.0 意图路由、实时搜索与受控文件 fallback

2026-09-10：**612 项 Agent 全量测试通过**。下方直接列出的专项模块合计 148 项：意图与运行器硬门控 64 项、`web.search` 和终端事件 37 项、协议/Markdown 端到端 31 项、Python fallback 16 项。

- 硬门控覆盖 `chat`、`knowledge_query`、`realtime_query`、`task_action`，并检查否定/教程语境、短易变主题、明确环境动作、后续“继续”和持久化状态；回答型任务中的工具动作被拒绝，意图元数据不授予 capability；动作还须匹配本轮 capability、mutation、操作族和每个文件/URL 目标范围，多来源到单目标保持读写方向，实时依赖的修改先完成时效检查。
- freshness gate 覆盖显式搜索必执行、知识截止/无法联网/空答升级、URL 不能单独证明实时、本日日期或明确检索痕迹可满足，以及成功搜索后继续任务不重复查询。
- 四级 `web.search` 覆盖 DDG/DDGo → Browser Use → OpenCLI → Playwright 的固定降级、首个有效结果、四路并发、稳定聚合去重、缺依赖、超时、异常、CLI 返回码、OpenCLI 单页上限、子进程环境凭据隔离、URL/凭据/大小边界及不含 query 的终端事件。生产 CLI 路径另外验证 stdout 超限会在解析前中止、stderr 不被保留、超时终止独立进程组及后代；Playwright 的启动/导航/等待共享一个截止时间。URL 用例覆盖整数/十六进制/八进制/畸形 dotted loopback、尾点 localhost、递归编码 key、AWS/GCS/CloudFront 签名和 `sig` 类字段，同时保留合法公网数值地址。
- 附件 `events.jsonl` 的 `ai\_news.md`、字符串外 `\[` / `\]`、字面量换行和精确 `/n/nN` 链接编号污染完成端到端回归；原始坏回复保留，规范化后仍经过 schema、授权、真实写入及写后回读。字符串内仅固定文件动作顶层 `arguments.path` 的 `\_` 可修复；工具名、shell 参数、浏览器 URL、摘要、计划、正文及其他非法标点转义均以执行级用例确认 `steps=0` 且不派发工具。
- 缺失文件工具仅从固定别名表装载 `LocalTools`；任意 `python.run` 保持拒绝，权限、工作区逃逸和已有文件覆盖在变更前阻止。

执行：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src:tests python3 -m unittest -v \
  tests.test_protocol_compat tests.test_protocol_repair \
  tests.test_markdown_document_normalization \
  tests.test_events_log_markdown_write \
  tests.test_intent tests.test_intent_runtime \
  tests.test_runtime_intent_hard_gates \
  tests.test_web_search tests.test_terminal \
  tests.test_python_fallback \
  tests.test_runtime_python_fallback
```

测试使用本地 HTTP、临时文件、伪终端、模拟搜索后端及短生命周期本地子进程；没有访问真实模型账号或公网页面，没有安装 Browser Use/OpenCLI，也没有启动原生 Playwright/桌面。并行超时不能强制撤销已经进入任意第三方 Python 调用的线程，自动验证确认的是内置 CLI 的进程树硬清理和 Playwright 单一 deadline；共享 CDP/Bridge 的真实标签页行为仍需在目标环境验证。目标环境的依赖检查、日志事件和人工复测见 [父项目诊断说明](../../docs/AGENT_INTENT_SEARCH_AND_FALLBACK.md)。以下为历史版本记录。

## 0.9.0 显示修复与上下文延续

2026-09-10：**497 项 Agent 全量测试通过**。包含真实本地 HTTP、临时文件操作、终端显示以及模拟浏览器工具；未访问真实模型账号或运行用户附件里的任务。

- 使用给定 BBC 损坏引用验证中文换行、列表编号、失效链接标记与窄终端宽度；合法 URL、代码和动作 JSON 不受影响，原始内容仍保留在日志与 transcript。
- 真实 HTTP 场景执行 5 次临时文件写入后返回 502，再继续时保留目标与观察，阻止同一写入重放，仅做新的读取核验。
- CLI 补充提示、模型切换、`/retry` 与 `/new`；`/model` 的本地建议包含 `glm`、`kimi`、`web-glm`、`web-kimi`，实际切换仍须通过父服务 `/v1/models` 硬校验；启动失败后未送达的补充内容继续保留；查询历史不会恢复执行，Skill 录制期间控制词仍作为步骤文字。
- 旧浏览器元素引用与未知执行结果继续门控；跨页、同路径不同查询参数、快照后跳页和缺失页面身份不能解除原待核验状态，原页面的新核验可以正常完成。
- 父服务恢复预算仅在身份及能力字段校验后扩展生成 POST 等待；读取模型列表维持原超时，不自动重复 POST。人工恢复阶段显示必要提示，不误报任务完成。

执行：`PYTHONPATH=src python3 -m unittest discover -s tests -q`。父项目网页恢复、界面入口及后端验证结果见 [父项目测试记录](../../docs/TESTING.md)。以下为历史版本记录。

## 0.8.0 Skill 对话录制

2026-09-09：**452 项 Agent 全量测试通过**；配套父应用 1.11.0 的 228 项 Node 全量通过，本轮合计 680 项。新增 22 项录制核心、11 项名称建议和 8 项交互门控测试，共 41 项；此前功能也通过全量回归。

- 逐条私有草稿落盘，步骤顺序、代码首行缩进及尾换行保持；明确开始/结束/确认状态，录制文字和多行控制词不执行。
- 命名请求只返回经过校验的 name/description/title；围栏、BOM和尾逗号有限修正，动作字段/重复键/非法名称拒绝；502不重发。
- finish先保存review，网络失败/取消仍可手动命名确认；无确认不发布，只有明确 /NAME 才运行，内置命令不被遮蔽。
- 原子无覆盖发布、并发同名、路径与链接边界、0600文件、写入和发布故障、取消/EOF保留草稿均覆盖；不承诺跨进程恢复录制。
- 真实录制生成的临时Skill通过skill-creator quick_validate；验证不执行录制步骤，不以静态通过冒充任务完成。

以下保留上一版验证记录。

日期：2026-09-09；Agent 0.7.0 / 父应用 1.10.0。测试使用 Python 标准库 unittest、临时目录、真实 POSIX 伪终端、本地 HTTP 夹具及模拟浏览器/桌面依赖。

**最终 411 项 Agent 全量测试通过；父项目 217 项 Node 和 110 项 Python 全量通过，总计 738 项。** 本版新增验证范围见下表；整体验证执行结果见 [父项目验证记录](../../docs/TESTING.md)。下方保留此前版本的验证结果，历史计数不代表本版重新执行次数。

## 0.7.0 新增验证

| 功能 | 验证方式与检查点 |
| --- | --- |
| 行内输入建议 | 真实 POSIX 伪终端检查灰色候选、Tab/行末右键接受后仍需 Enter、行中编辑、↑/↓ 历史和草稿恢复、长中文与组合字符删除；多行粘贴不会自动提交，Ctrl+C/Ctrl+D 与异常恢复终端状态；管道/TERM=dumb 使用普通输入，NO_COLOR 不产生颜色码且不接受不可见候选 |
| 网页响应阶段 | 本地真实 HTTP 夹具检查单次生成 POST 对应独立进度编号、认证及阶段采集、并发请求隔离、结束时的最后查询；旧接口/无效进度/观察线程失败仅退回普通等待，不改结果或重发 POST；通用 API 不发送 Fusion 扩展查询 |
| 默认简洁输出 | 任务和交互默认不自动显示工作目录、任务记录、结果路径；`--v`/`-v`/`--verbose` 与 `/verbose on`/`off` 控制额外信息，`/logs`/`/status` 主动查询仍可显示；完整文件日志保留 |
| 动态 Skill | 生成后立即发现和加载；名称列表去重、卸载、重新读取；任务之间修改正文能生效，非法 frontmatter、空正文和读取失败不会使用旧缓存；内置技能和新 demo 的静态结构检查、缺少资源、越界和符号链接均覆盖 |
| Skill 测试范围 | Python AST 检查语法但不执行脚本，测试内放置会写入标记的脚本并确认标记始终不存在；其他脚本语言报告语法未检查；结构检查成功不冒充实际任务完成 |
| 历史任务/会话 | 新会话分组在重建读取器后保留，旧版独立任务无需迁移可查询；关键词按原任务和结果查找；详情只显示用户任务、工具状态摘要和保存结果；不执行记录、不读取符号链接/FIFO，损坏或超大记录不阻断其它记录，写入失败保留原索引 |
| 工具替代 | `file.read` 等精确别名保留参数、文件边界和授权；未知工具返回候选，必须由模型提交新的合法动作；CLI 候选仍需 shell 授权；尚未启动的简单读文件命令只有对同一文件的完整读取才能解除原失败，换文件、部分读取和无关成功均不允许 |
| 不重放已执行命令 | 命令已启动后退出 127、超时、缺工作目录或脚本解释器等情形按实际证据区分，不误认成“程序未安装”而重放操作 |

Skill 相关的 34 项测试包含在全量通过结果中；CLI 集成、并发日志和终端阶段同样通过。本轮不安装第三方包、不下载 Chromium，也不访问真实模型账号。

定向验证可在 `desktop-agent/` 执行：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_input_editor.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_web_progress_client.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_history.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_skill*.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_tool_resolution.py' -v
```

## 0.6.0 新增验证

当时 **326 项 Agent 测试通过**，父项目 8 项启动器、16 项诊断打包和 215 项 Node 测试通过。

- 20项父自动启动测试：真实localhost与轻量子进程夹具验证API+bridge、并发一次启动、单实例交接、认证失败/错误端口/外部API不误拉起、超时保留进程、日志与相对数据路径、FIFO拒绝和启动前轮转。
- 15项交互测试：默认入口启动检查、离线仍保留调试入口、模型校验切换、真实HTTP两轮任务模型与上下文隔离、临时权限不跨任务、/workspace+/retry、打包参数限制及格式化输出。
- 预启动失败或取消同样生成0600结果/状态/任务记录，步骤为0；任务已完成后渲染报错不会覆盖已保存状态。
- 10项Demo生成测试：真实加载及脚本运行、不覆盖、原子发布、并发同名、链接/路径边界和失败清理；临时Skill通过创建规范校验。
- 11项附件恢复测试：files.list越界保留原目标，返回workspace_required；三条截断JSON不执行任何工具，修正耗尽返回incomplete_response；目录元数据不冒充递归容量。
- 3项新增渲染测试覆盖双重转义Markdown、inline code/路径/JSON原义和幂等性；附件报告的58处字面量换行全部成功规范化。原始回复仍留在transcript及日志。

父应用未闭合JSON采集保护与整体验证范围见 [父项目验证记录](../../docs/TESTING.md)。本轮没有启动原生Electron、真实模型或桌面任务，也没有执行附件里的用户命令。

## 0.5.0 新增验证

- 详细日志仅写全局/每轮文件且逐条一致；终端默认没有模型原始回复、分段载荷或命令输出。文件失效只发一次简短告警，日志 handler 不转储私有记录或 traceback。
- 真实 localhost HTTP → CLI 端到端用例核对正文留在文件、终端结果已排版，失败保留结果和可用日志路径；必要授权仍完整显示命令、URL 和路径。
- 24 项渲染测试覆盖中文宽度、Markdown 标题/列表/引用/表格/代码/链接、JSON 缩进、TTY/NO_COLOR/纯文本输出、ANSI/OSC 控制字符、重复 JSON 键、深嵌套 JSON 与超长 HTML 实体。
- 11 项新协议测试覆盖 BOM、整体 JSON 围栏、字符串外数组转义和尾逗号的组合修复，字符串逐字符保持；附件两个未转义命令引号请求模型修正为 argv 后才执行一次。
- 修复后仍拒绝重复键、NaN、多个对象、解释文字、无效参数和未经授权工具；无法修复时达到两次预算后明确失败。
- pending_verification 存在时，局部检查成功不能显示原操作已核验；上一版依赖恢复与完整任务循环继续通过。

以下 0.4.0 / 0.3.0 小节保留历史验证内容；0.5.0 已按最新要求替换其中的终端日志镜像行为。

## 0.4.0 新增验证

- 按附件顺序复现：当前 Python 缺 Playwright，修复当前环境后仅导航一次、实际 DOM 核验，再写文件和读回，任务完成；不是重复播放附件命令。
- `dependency_missing`、命令无法启动、无效引用等明确执行前失败不生成虚假待核验；实际导航/输入未知结果继续受核验门禁约束。
- 固定 `environment.browser_setup` 命令使用当前虚拟环境解释器，受 shell 授权控制，系统 Python 拒绝安装；命令失败、超时和安装核验都保留日志。
- `environment.browser_check` 返回缺包诊断时不产生未解决失败；存在运行中浏览器时借用原驱动，只释放自己创建的检查驱动。
- 合法回复重置连续协议纠正计数，不同已完成动作不累计耗尽核验纠正次数。失败结果保留具体步骤和错误。

## 0.3.0 新增验证

- 使用用户日志中的 `plan` 数组转义和自动网址链接复现；只改字符串外数组括号，命令字符串保持原样。
- 拒绝不同目的地链接、多对象、重复字段、前后说明和未经授权工具；规范化日志记录原文与动作。
- 终端、全局 `agent.log` 与本轮 `events.jsonl` 同一条记录逐字一致，Unicode/分段正文、凭据脱敏、元数据模式、即时 flush、0600 权限通过。
- 文件不可写、既有日志及符号链接拒写；任务目录使用原子创建，撞到仅有旧事件日志的目录时换新编号，八次冲突后明确停止。
- 真实 localhost HTTP → CLI 成功和失败用例：二者都生成 `result.md`，成功保留 `final.md`，CLI 显示准确文件路径。
- 默认 API 超时为 900 秒，自定义值与合法边界保持有效。

## 自动验证范围

| 范围 | 方式与关键检查 |
| --- | --- |
| API 客户端 | 实际 localhost HTTP 服务验证路径、鉴权头、文本消息、模型选择、响应解析、错误与大小限制；HTTP 502 来源错误、X-Request-ID / X-HTTP-ID、完整请求/响应日志与凭据脱敏；无重定向、无自动重试 |
| 任务循环 | 模拟模型响应验证严格单对象协议、两次格式修正、拒绝围栏前后示例文字、步数/上下文/父接口限制、中断及 Markdown 结果；待核验动作阻止继续修改/完成，失败不能被无关读取或错误核验解除，对应成功重试可恢复 |
| CLI 集成 | 实际 `run.sh` 子进程连接本地 HTTP 夹具，由模型夹具要求执行真实 Python 命令写文件，随后读取核验并显示格式化结果；另复现无效协议 → 修正请求 → HTTP 502，确认零工具执行、失败退出码、具体网页错误、文件日志和不自动重发 |
| 命令与文件 | 真实临时目录和子进程测试输出限额、超时、进程组清理、环境过滤、路径和符号链接限制、原子写入及覆盖拒绝；非零退出码/超时为失败，文件写入后实际字节回读并检查文件身份 |
| 浏览器 | 模拟 Playwright 验证独立配置、沙箱选项、DOM 文本和引用、元素改变/导航检测、标签切换、错误与截图路径；输入和选择值回读、HTTP 4xx 拒绝、待核验状态、fresh DOM 文本/完整 URL 断言与关闭后状态检查 |
| 桌面 | 模拟 PyAutoGUI、Pillow、OCR 和剪贴板，验证 X11/Wayland 判断、观察有效期、坐标/按键校验、紧急停止及本地截图；动作先返回 pending，新截图 OCR 断言、实际指针位置、不可用 OCR/不匹配结果返回失败 |
| Skills | frontmatter、每任务动态加载、卸载/重读、路径/大小限制、示例发现、资源引用和 Python 静态语法；可信示例报告脚本在临时目录验证实际产物与写入边界，普通 skill-test 不执行用户脚本 |
| 交互及历史 | 默认简洁输出、verbose 切换、输入候选不会提交或联网、历史只读查询和格式化、模型/技能选择不恢复旧执行状态 |
| 日志与注册表 | 能力授权、参数 schema、未知工具、已知密钥过滤、全局/每轮文件相同正文分段、终端仅必要进度、可选仅元数据、明确截断及异常副作用标记 |
| 父项目诊断包 | 包含 Agent 代码/技能/审计日志，排除配置、任务记录、工作区、截图、浏览器配置及会话数据；显式 --include-content 保留脱敏日志正文 |

执行命令：

```bash
# desktop-agent 目录
PYTHONPATH=src python3 -m unittest discover -s tests -v
bash -n run.sh setup.sh

# 父项目目录
python3 -m unittest discover -s tests -p test_package_diagnostics.py -v
```

## 目标环境仍需验证

1. 在日常 Linux 终端运行 `./run.sh`，输入 `/mod` 检查灰色建议；按 Tab 或行末右键应只补全，按 Enter 后才查询/切换。用长中文输入、左右移动和多行粘贴检查显示；必要能力确认不应进入任务补全编辑器。
2. 分别以默认模式和 `./run.sh --v` 启动，在交互中切换 `/verbose on` / `/verbose off`，确认目录及结果路径的自动显示符合设置，`/logs` 仍能主动查询。
3. 连接本版父应用发送任务，同时观察网页与终端：发送动作、网页接收、服务器 HTTP 状态、生成和采集只在相应观察存在时出现。关闭进度能力的通用 API 应保持普通等待，不能伪造服务器已返回。
4. `/skill-demo my-check` 后执行 `/skill test my-check`、`/skill load my-check`；在临时工作区运行示例任务，编辑正文后再次运行，确认新内容生效。故意破坏 frontmatter 应明确失败。结构检查通过后仍需核对实际任务产物。
5. 完成两个任务并重启 Agent，使用 `/history`、`/history show <任务编号>`、`/sessions`、`/session <会话编号>` 验证旧任务和会话可查，查询过程中不发送模型请求或执行旧动作。

浏览器、桌面及打包验证：

1. 启动父应用并登录所选模型，运行 `./run.sh doctor --api`，然后执行 README 中的环境报告任务。实际网页的 JSON 遵循程度、生成速度和 DOM 兼容性不由本地夹具保证。
2. 安装浏览器依赖，完成 example.com 阅读任务，检查报告与浏览器现场；需要账号的站点在 Agent 独立浏览器内手动登录。
3. 在 X11 会话中打开空白编辑器，执行桌面示例；核对 OCR、坐标、中文剪贴板依赖及紧急停止。没有 OCR 时无法核验输入等应用动作；指针位置核验只适用于移动鼠标，不能替代应用结果核验。
4. 运行父目录 `./package.sh` 并检查包清单；默认诊断包移除日志正文；定位协议和工具输出问题时使用 `./package.sh --include-content`。完整 transcript、工作区、截图与账号配置仍不会加入包。

本次没有访问真实模型账号、启动原生浏览器或操作真实桌面，父项目已通过 FastAPI TestClient 的 HTTP/WebSocket/SSE 用例，但没有完成 FastAPI + 原生 Electron + 登录模型网页 + Agent 的端到端联调。浏览器截图和桌面截图不会发送给纯文本 API。
