# 验证记录

## 1.17.1 GLM 模型选择与页面复用

2026-09-11：本次执行 **109 项无外部依赖 Node 测试，全部通过（32 项 GLM 专项、77 项原有回归）**，`npm run check` 通过。父项目 Python 执行 120 项，其中 119 项通过、1 项非法 Host 用例错误；在原始 1.17.0 包中同样复现。完整 npm/JSDOM、Electron 及真实 GLM 账号未完成验证。详细命令、限制和新建对话边界见 [GLM 不刷新验证记录](GLM_NO_REFRESH.md)。下方旧版本统计保留为原有历史记录，不作为本次执行结果。

## 1.14.0 / Agent 0.10.0 意图、搜索与协议恢复

2026-09-10：**1076 项全量自动测试通过：612 项 Agent、345 项 Node、119 项父项目 Python**。`npm run check`、父/子项目 Bash 语法及 Python 编译检查通过。下方直接列出的意图、搜索、协议和 fallback 专项模块合计 148 项通过。

| 范围 | 本轮覆盖 |
| --- | --- |
| 附件协议恢复 | `events.jsonl` 的 `ai\_news.md` 路径 `\_`、数组括号、字面量换行、列表编号/链接污染；字符串内仅固定文件动作顶层 `arguments.path` 的 `\_` 可修复，工具名、shell 参数、浏览器 URL、摘要、计划、正文及其他非法标点转义不派发；原回复保留，窄规范化后仍通过 schema、授权、真实文件写入和回读 |
| 意图门控 | chat/knowledge/realtime/task_action 硬门控、回答型工具动作拒绝、补充任务与“继续”继承、已完成任务与新任务的执行证明隔离；意图证据不能授予 capability；动作须匹配本轮 capability/mutation/操作族/每个文件或 URL 目标，多来源到单目标保持读写方向，实时依赖的修改先完成时效检查 |
| 时效反馈 | 显式搜索必执行；知识截止、无法联网、空答及缺少当日/检索证据时升级；URL 不单独视为实时证据；成功检索后继续任务不重复 |
| 多路搜索 | DDG/DDGo → Browser Use → OpenCLI → Playwright 顺序降级；仅显式 parallel、查询与模式派发绑定、稳定去重、依赖缺失、总截止时间、CLI 进程树清理和输出硬上限、OpenCLI 单页上限与环境凭据隔离、结果/URL/签名边界和日志事件 |
| Python fallback | 缺失文本文件工具的固定别名与 `LocalTools` 绑定；任意 Python/eval 拒绝；schema、files 权限、workspace、覆盖和回读规则保持 |

```bash
cd desktop-agent
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

自动测试使用模拟搜索后端和临时文件，不访问真实模型账号、公网页面或浏览器扩展。诊断、依赖与目标 Linux 复测步骤见 [Agent 意图、搜索与协议诊断](AGENT_INTENT_SEARCH_AND_FALLBACK.md)。

## 1.13.0 Qwen 繁忙重试与 GLM/Kimi

2026-09-10：**961 项全量自动测试通过：497 项 Agent、345 项 Node、119 项父 Python**。`npm run check`、父/子项目 Bash 语法检查通过。

| 范围 | 本轮覆盖 |
| --- | --- |
| Qwen 截图场景 | 精确识别 `Qwen3.8-Max` 双语繁忙卡及其下方无文字圆箭头；只在当前已接受用户回合、单一相邻可点击控件时执行一次可信重试；覆盖同一回答内与相邻 footer 两种结构 |
| 重试负例 | 拒绝旧回合、侧栏/页面刷新、错误卡之前图标、多个图标、过远/过大/遮挡/禁用控件、普通重新生成及无法绑定用户回合；自动动作回执不确定时不重复点击 |
| 回答完整性 | 自动重试、用户手动重试和原慢响应均继续等待完整回答；二次网络失败、缓存尾段或只消失的错误卡不能冒充完成；恢复预算为 0 时错误卡仍不能进入 Markdown 或融合 |
| GLM/Kimi | schema 3 默认关闭预设、schema 1/2 无损迁移、单模型 API 别名、CDP 原生输入、明确发送按钮、助手回合 Markdown、停止/错误/重试选择器；找不到 Send 时不盲按 Enter |
| 登录适配 | ChatGLM/Z.AI 与 Kimi 固定域名、严格域名后缀和精确 localStorage origin；Chromium/Firefox 扩展 1.2.0 生成副本一致，未扩大到任意网站 |
| 页面边界 | 自动诊断、隔离世界 DOM、原生输入及每次可信鼠标/键盘动作均校验配置 origin；任务期间阻止主框架跨站跳转，同源路径和 query 仍允许 |
| Agent | `/model` 补全和服务端硬校验支持 `glm`、`kimi`、`web-glm`、`web-kimi`；两种模型可在交互任务间切换，默认模型保持不变 |

执行命令：

```bash
npm test
npm run check
PYTHONPATH=.venv/lib/python3.12/site-packages python3 -m unittest discover -s tests -p 'test_*.py' -q
bash -n run.sh package.sh desktop-agent/run.sh desktop-agent/package.sh
cd desktop-agent
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -q
```

测试使用本地网页/CDP、HTTP、Cookie 数据库和文件系统夹具。**本环境没有登录真实 Qwen、GLM 或 Kimi 账号，也没有在用户 Linux 图形桌面运行原生 Electron**，因此不把结构化选择器测试表述为线上站点验证；账号实验版本或网站改版仍可能需要在设置中更新选择器。Qwen 自动重试仅覆盖截图中可被安全绑定的按钮结构，无法唯一确认时会提示用户在原网页手动重试并继续采集。

## 1.12.0 网页恢复、上下文延续与格式修复

2026-09-10：**925 项自动测试通过：496 项 Agent、314 项 Node、115 项父 Python 全量**。JavaScript 语法检查（package 中 check 的全部目标）、父/子项目 Bash 语法、Agent Git 差异检查通过。

| 范围 | 本轮覆盖 |
|---|---|
| 网页恢复 | 同一个 job/POST、一次可信网页重试、人工操作后恢复、长时间流、再次失败、窗口超时、取消与调试器清理；已知失败流的缓存尾片不能冒充完整回答 |
| 本轮隔离 | 精确识别当前用户回合，拒绝旧回复、其他用户输入、跳转会话及页面正文中的伪重试；Qwen 原提示仍在输入框时等待人工检查 |
| 界面入口 | 标签页、双栏及独立窗口可显示等待人工处理的原网页；忙时普通切换仍受限，输入占用/维护/非当前任务无法打开该入口 |
| 后端及客户端 | generation 恢复预算默认180、范围0–600；状态协议、总等待预算、同一个HTTP POST贯通manual→recovering→complete；不自动重复POST |
| Agent 上下文 | 5次实际临时文件写入后HTTP502，继续保留原目标与工具观察并拦截旧写入重放；新提示、启动失败、模型切换、/retry、/new与历史查询 |
| 执行核验 | 续轮旧元素引用失效，待核验与未知执行结果继续保留；原页、不同页、不同query、快照后跳页及缺失页面身份分别验证 |
| 显示 | 给定BBC损坏引用、中文软换行及编号恢复；合法URL、代码、JSON原义与原文日志保留；TTY与纯文本宽度 |

执行命令：

```bash
npm test
# package.json scripts.check 中的所有 node --check 目标
PYTHONPATH=.venv/lib/python3.12/site-packages python3 -m unittest discover -s tests -p 'test_*.py' -q
cd desktop-agent
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

上面的父项目 PYTHONPATH 是本次环境已有依赖的位置；本机安装完成后也可使用父项目虚拟环境的 Python 运行测试。本轮没有登录真实模型网站，没有在用户 Linux 桌面运行原生 Electron，也没有执行附件中的用户任务。DOM/CDP、浏览器页面和错误服务由本地夹具模拟；真实站点改版或账号差异仍需现场核对。恢复时无法确认当前回合则保留失败，不宣称全站兼容。

以下为历史版本的验证记录。

## 1.11.0 ChatGPT 可信发送与 Skill 对话录制

2026-09-09：**680 项自动测试通过：452 项 Agent 全量、228 项 Node 全量**。`npm run check`、父/子项目 Bash 语法和 Agent `git diff --check` 通过；真实录制生成的临时 Skill 通过 skill-creator 的 `quick_validate.py`。本轮未修改父 Python 调度/API 逻辑，除版本标识外保持不变，未重复运行其 110 项历史测试。

| 范围 | 本轮验证 |
| --- | --- |
| ChatGPT DOM | 7 项新增测试覆盖可见 ProseMirror、隐藏 textarea、原生输入准备、段落/软换行/内部空行与缩进保留、其它表单隔离、发送与语音/停止语义冲突、无明确按钮不盲发 Enter |
| 双模型发送 | 4 项新增集成覆盖 ChatGPT＋DeepSeek 在 tabs/split/windows 三布局依次持有实际输入租约、各插入/提交一次；点击未确认不重复提交；原 Qwen 与全部网页适配回归通过 |
| 录制核心 | 22 项新增真实文件测试：硬状态转换、每条草稿落盘、顺序/原文、无确认不发布、metadata-only建议、取消保留、无覆盖并发、非法路径/名称/链接、私有权限、内容上限、写入或发布失败；发布已成功但草稿末次更新失败按实际结果报告 |
| 名称建议 | 11 项新增测试：真实 localhost HTTP、单次 POST、502 不重发、进度握手、严格元数据字段与有限 JSON 修正、重复键/动作字段/密钥回显拒绝、取消关闭日志、不修改步骤、不创建或执行 Skill |
| 交互门控 | 8 项新增测试：录制中的 /model、/retry 只记为步骤；多行里的结束词不触发；finish先保存、confirm后创建、明确 /NAME 才运行；名称冲突/内置词/越界拒绝；EOF保留、上下文补全、原始首行缩进和尾换行保留 |

独立审查复现过一次录制正文被交互入口 `.strip()` 删除首尾空白的问题，已改为原文交给录制器，仅用单独的规范化文本识别控制命令，并增加回归。自动测试没有真实网站账号、原生 Electron 或用户桌面端到端验证；本轮没有新实站日志，不能把可复现代码缺口说成已确认唯一现场根因。使用方法见 [ChatGPT 发送与 Skill 录制](CHATGPT_SEND_AND_SKILL_RECORDING.md)。

## 1.10.0 输入补全、网页进度、动态技能和历史

2026-09-09：**738 项自动测试通过：411 项 Agent 全量、217 项 Node 全量、110 项父项目 Python 全量**。`npm run check`（含新网页进度模块）、父/子项目 Bash 语法及 Agent `git diff --check` 通过。Agent 首轮两条旧测试预期分别按默认隐藏路径和派发前参数修复更新后，最终全量 411 项通过。

| 范围 | 关键验证 |
| --- | --- |
| 输入补全 | 18 项输入测试，其中 13 项使用真实 POSIX PTY；灰色候选、Tab/右方向键接受但不提交、中文滚动、历史与草稿、粘贴防连发、终端模式恢复、NO_COLOR 隐藏且禁用候选接受 |
| 网页进度 | 4 项父进度 HTTP/桥接测试、7 项 Agent 进度 HTTP 测试和 2 项 Node 阶段映射测试；UUID 隔离、认证、错误 job/provider 拒绝、实际生成 POST 状态、保留/裁剪、旧服务降级、观察线程故障和最后收尾；聊天 POST 不重发 |
| 控制台集成 | 5 项新增组合测试验证 --v 参数位置、详细开关对后续任务生效、静默模式完整记录、动态 Skill 生成/加载/卸载、原样保留任务引号和换行、/retry 保留一次性 Skill、历史只读和本地补全不调用模型 |
| 日志与终端 | 6 项新增测试覆盖双线程分片不交错、全局与本轮日志逐字一致、close 后忽略回调、元数据模式保留阶段、多模型去重、投递/接受/等待/生成的语义区别 |
| 技能 | 技能相关共 34 项测试；生成即发现、正文每任务重读、动态选择和卸载、非法新内容不回用缓存、静态检查不执行脚本、支持的资源引用和 Python AST 检查 |
| 历史 | 17 项新增测试；真实记录与会话索引跨实例读取、旧任务查询、关键词搜索、答案渲染来源、只读摘要、密钥脱敏、路径/链接/FIFO/大小边界、索引原子写入 |
| 工具恢复 | 20 项新增测试；精确别名、真实 schema 派发前修复、未启动缺命令候选、同目标完整读取核验、工作区和权限仍生效、未知副作用/真实失败/换目标不能假报恢复 |
| 父应用兼容 | 原 Qwen 会话、评分层、并排/窗口、可信发送、慢响应与完整采集、单模型与融合、诊断打包全量通过；9 项 bootstrap 测试含 --v 下正确选择浏览器环境 |

测试使用本地 HTTP、模拟 CDP/网页桥接和真实临时文件/伪终端，不访问模型账号、不执行附件中的命令。没有在本环境完成原生 Electron、真实 Qwen 或用户桌面的端到端验证；沿用历史原生环境限制。安装与复测方法见 [补全、进度与历史](AGENT_COMPLETION_PROGRESS_AND_HISTORY.md)。

## 1.9.0 交互、父服务启动与 agent2 附件恢复

2026-09-09：**565 项自动测试通过：326 项 Agent 全量、215 项 Node 全量、16 项父项目诊断打包、8 项 Agent 启动器测试**。`npm run check`、父/子项目 Bash 语法和 Agent `git diff --check` 通过。本轮未重复运行未修改的父项目其余 Python HTTP/Engine 测试；历史结果保留如下。

| 范围 | 关键验证 |
| --- | --- |
| 父自动启动 | 20 项新测试：真实 localhost GET/轻量子进程夹具，API+认证bridge门槛、离线和仅API时拉起、并发单次启动、401/未知服务/远端不误启动、超时保留、单实例交接、相对数据/日志目录、FIFO快速拒绝、启动前轮转 |
| 交互 CLI | 15 项新测试：入口检查父服务、失败仍可调试、模型验证切换、两轮任务独立上下文/run_id、临时权限不继承、/workspace+/retry、/pack严格参数、预启动失败/取消写结果，渲染异常不覆盖已完成结果；旧19项CLI集成继续通过 |
| Skill demo | 10 项新测试：实际生成并通过SkillLibrary catalog/load，脚本执行、文件不覆盖、目录原子发布、并发同名只成功一次、无半成品、拒绝路径逃逸与链接；临时Demo通过skill-creator校验 |
| 附件任务恢复 | 11 项新测试复现三种越界参数和三个截断回复；未解决目标保留，workspace_required/incomplete_response准确，零动作不会假报成功，目录项大小语义明确 |
| 输出格式化 | 27 项渲染测试，含3项新转义边界回归；实际附件58处字面量换行全部恢复为段落/表格行，原始会话保留 |
| 网页采集 | 7 项新增测试，片段稳定后继续等待至完整；超时不返回残片、不重复发送；普通Markdown与完整畸形JSON不误阻塞；原Qwen会话/并排/窗口/网络门禁全量回归 |
| 打包/启动脚本 | 16 项诊断包测试含父启动日志及正文/密钥脱敏；8项bootstrap测试含交互模式环境选择 |

独立审查还用148个JSON前缀、完整对象和普通Markdown样例验证采集分类，不计入上述正式测试数量。自动启动测试没有启动原生Electron，测试中的子进程身份适配当前容器PID视图；生产实现使用Linux进程启动标识和所属子进程状态。没有访问真实模型账号、安装浏览器或执行附件中的家目录命令。实际线上返回片段的根因仍需父应用日志确认。方案和复测步骤见 [交互与恢复说明](AGENT_INTERACTIVE_AND_RECOVERY.md)。

## 1.8.0 Agent 简洁终端、结果渲染与 JSON 修复

2026-09-09：**本轮 289 项自动测试通过：267 项 Agent 全量、15 项父项目诊断打包、7 项 Agent 启动脚本测试**。`npm run check` 和父/子项目 Bash 语法检查通过。本轮未重复运行未修改的父项目其余 Python 或 Node 全量测试；上一版结果保留如下。

| 范围 | 验证内容 |
| --- | --- |
| 终端与文件 | 默认不输出详细 JSONL、模型原始回复和工具 stdout；全局/每轮日志仍一致并立即写入；文件故障只告警一次，不转储日志记录或 traceback |
| CLI 集成 | 真实 localhost HTTP 服务与 run.sh 子进程，执行命令和读回核验；stdout 为格式化结果，stderr 为必要进度；失败仍保存结果和日志路径 |
| 渲染 | 24 项针对测试覆盖中文宽度、标题/列表/表格/链接/代码、TTY/重定向/NO_COLOR、控制字符清理、重复 JSON 键、深嵌套 JSON 和超长 HTML 实体 |
| JSON 修复 | 11 项新增测试覆盖 BOM、整体围栏、字符串外数组转义/尾逗号组合、原始字符串不变、附件两种引号错误的模型修复、修复耗尽失败、重复键和未授权动作拒绝 |
| 状态与依赖 | 原操作待核验时不会因局部指针检查显示全部成功；旧附件中的缺包/错误环境/执行前失败恢复用例继续通过 |
| 打包与启动 | 15 项诊断包回归及 7 项启动环境选择回归；源码新增文件可随分析包收集 |

`agent1.tar(1).gz` 与前一份附件字节相同，均为旧 1.6.0 任务记录。本轮复现其格式错误并保留 1.7.0 依赖恢复修复，没有执行附件中的下载或安装命令。没有真实模型账号或原生 Linux 桌面端到端验证；渲染检查使用输出样例及模拟 TTY。运行与日志说明见 [Agent 终端与 JSON 修复](AGENT_TERMINAL_AND_JSON.md)。

## 1.7.0 Qwen 会话、评分面板和 Agent 依赖恢复

2026-09-09：**531 项自动测试通过：208 项 Node/jsdom、103 项父项目 Python、220 项 Agent**。三个项目均完成全量回归；Agent 的附件场景随后改为更完整的真实工具组合并通过 6 项针对性复测，没有重复计数。`npm run check`、Bash 语法检查通过。

| 范围 | 本次验证 |
| --- | --- |
| Qwen 会话 | 23 项新增测试；已打开页面连续两次生成不 loadURL、保留 Qwen3.8-Max；首次页面只加载一次；SPA 新建后旧消息必须清空；无法新建或模型发生可识别变化时不发送 |
| 评分面板 | 新建会话后出现评分层，关闭一次并等待消失和输入恢复；嵌套星级区不遮蔽外层 Close；不点击评分/提交、普通链接或无关弹层；完整导航被拦截 |
| 原有适配 | 单次可信发送、可见性和输入租约、慢服务器接收、暂停流、断流拒绝、Markdown/JSON 原文、并排/窗口及登录导入回归 |
| Agent 附件 | 按缺依赖→同解释器安装→仅导航一次→DOM 核验→写文件并读回→完成的顺序复现；环境检查、注册表、Runtime、本地文件工具使用真实实现，安装/网络/浏览器为 fixture |
| 失败与纠正 | 执行前失败不生成未知副作用；实际导航超时仍需核验；协议纠正按连续失败，核验纠正按待核验动作；失败详情写结果文件 |
| 环境工具 | 同一 sys.executable 的固定安装命令、虚拟环境限制、shell 权限、缺包诊断不留失败、超时/安装错误、当前驱动复用与清理 |
| 启动脚本 | 7 项新增父测试，包括真实 bash 子进程验证显式 Python/激活 venv/项目 venv/PATH 的优先级、空格路径、字面量参数及 PYTHONPATH；测试不安装依赖 |

附件确证原任务在第 10 步因 `verification_pending` 停止，并未用尽 20 步。没有执行、重放附件里的安装或视频下载命令。本次未访问真实模型账号、真实评分弹层或原生 Linux 桌面；没有将合成页面视作 Qwen 官方 DOM。原生 Electron 环境限制仍沿用历史记录，本轮只更新本地原生夹具以支持 Qwen SPA 会话，不将其计作原生运行通过。

完整使用方法见 [Qwen 会话与 Agent 恢复](QWEN_SESSION_AND_AGENT_RECOVERY.md)。

## 1.6.0 慢响应、完整融合和 Agent 日志

2026-09-09：本轮覆盖 **466 项不同的自动测试：185 项 Node/jsdom、96 项父项目 Python、185 项 Agent**。验证顺序为 Node 全量 183 项通过后，网络关联修改的 24 项针对性复测通过（新增 2 项）；父项目 Python 全量 95 项通过后，保存失败处理的 27 项 Engine 复测通过（新增 1 项）；Agent 全量 185 项通过。`npm run check` 和启动脚本 Bash 语法检查通过。

| 范围 | 关键验证 |
| --- | --- |
| 默认并排 | 首次安装及 v1 布局升级默认双栏；保留模型/尺寸；随后主动选择持久保存；单模型回退标签页 |
| 慢 Qwen | 虚拟时间超过旧 15 秒后才接收，仍完成且只发送一次；完整手势后释放输入占用，其他模型可继续发送 |
| 完成判断 | 输出暂停但关联响应未结束时继续等待；网络断流拒绝半截回答；永不结束达到总时限明确失败；无关 SSE 不影响门禁 |
| 融合与存储 | 最慢候选完成前不融合；默认任一失败阻断融合；等待/完成/失败模型日志准确；原文保存失败同样记失败；单模型 API 保持直返 |
| 配置升级 | schema 1→2 只迁移一次；旧默认 180→600 秒、新增接收确认 120 秒、关闭部分结果；自定义时限和后续显式设置保留 |
| JSON 提取 | 精确复现用户网址与数组被 Turndown 改写；经 WebsiteAdapter 完整流程保留原文；格式化文章、命名链接、图片和非 JSON 代码不被拼成动作 |
| Agent | 严格协议基础上的有限旧格式兼容、歧义 URL 拒绝、命令字符串不变；终端/global/per-run 逐条相同；所有终态 result.md；日志目录碰撞不混用 |
| 分析包 | 15 项打包回归；实际 package.sh 的 --include-content + --agent-run-id 合成检查，确认选中日志、正文与凭据脱敏、源码包含和 0600 权限 |

修正过一项 UI 测试竞态：一次性 `reopen_windows` 命令可能被随后正常布局包替换，旧测试只看最后一包会误报超时；现检查点击后的调用记录，验证命令恰好一次且不会残留，没有放宽等待时间。

**没有真实 Qwen/DeepSeek 账号和原生 Linux 桌面端到端验证。** 慢网络测试采用模拟 CDP 与虚拟时间；没有声称等待了真实服务器 120/600 秒。仅识别到生成路径的 POST 才参与网络完成门禁，未知端点继续使用 DOM 停止状态和稳定窗口，不能保证每次网页改版都自动适配。原生 Electron 环境限制沿用下节记录，本轮未重复启动失败的原生检查。

启动及日志说明见 [1.6.0 修复说明](WAIT_AND_PROTOCOL.md)。以下保留历史验证记录。

## 1.5.0 后台发送与网页布局

2026-09-09：**158 项 Node/jsdom 测试、13 项 Python 诊断打包测试通过**；`npm run check`、Bash 语法和后端版本文件的 Python 语法检查通过。

| 范围 | 本轮验证 |
| --- | --- |
| 输入与按钮检测 | Qwen 原生输入单次投递；可见性、页面/编辑器焦点、控件选择器、禁用/遮挡/视口；独立只读诊断不填充、不点击、不滚动、不修改任务上下文 |
| 视图宿主 | 18 项测试：三种模式、同一视图重挂、分栏尺寸、窗口关闭仅隐藏、最小化/关闭后显式恢复、用户命名空间启动策略不变；串行输入占用、取消/超时/隐藏、异常恢复及清理 |
| 宿主与适配器联合 | 5 项测试执行真实 ProviderLayout / WebsiteAdapter / 序列化 pageAction，由模拟 Electron 和 jsdom 提供页面：三种模式下 Qwen+DeepSeek 各提交一次；输入不重叠，回复等待可并行；取消/未确认不补发；恢复布局和焦点 |
| UI | 24 项测试：模式与比例持久化、左右模型、分隔条鼠标/键盘调整、原生视图隐藏/恢复、只读检测先采集后弹窗、复制 JSON、外部 API 忙时仍能恢复窗口、双栏检测目标不污染宿主 active_provider |
| 回归 | 原有 Markdown 提取、Qwen 控件变体、网络日志、登录导入/扩展、启动器、API 配置与 UI 测试一并通过；诊断打包默认/含正文模式继续通过 |

执行命令：

```bash
npm test
npm run check
python3 -m unittest discover -s tests -p test_package_diagnostics.py -q
bash -n run.sh package.sh
```

**原生 Electron 和真实 Qwen 3.8 网页未通过现场验证。** 本次尝试 `npm run smoke:electron`，进程在渲染器启动前因 dbus/netlink/udev 权限限制以 SIGSEGV 退出；当前没有 DISPLAY 或 Xvfb。新增 `tests/electron_layout_smoke.cjs` 已做脚本及页面脚本静态编译检查，未将它计为运行通过。该夹具只访问 localhost，可在用户 Linux 桌面以 `npm run smoke:layout` 检查真实可见性、焦点、单次请求、Markdown、窗口重挂和尺寸调整。

上传日志只有 backend 事件，能确认 Qwen 投递后未获接收证据，但不能确定其实际按钮 DOM。没有宣称取得 Qwen 3.8 官方选择器；新版本提供实际页面检测报告。详细复测方法见 [网页布局与发送诊断](VIEWS_AND_SEND.md)。

## 1.4.0 操作日志、协议与结果核验

2026-09-09：**365 项自动测试通过：114 项 Node/jsdom、88 项父项目 Python、163 项 Agent**，`npm run check` 通过。Agent 的各模块回归结果见 [Agent 验证记录](../desktop-agent/docs/TESTING.md)。

| 范围 | 本轮实际检查 |
| --- | --- |
| 网页发送 | Qwen 原生输入、完整输入回读、按钮定位、单次 CDP 点击、提交证据、Markdown 完成检测；点击回执和网页接受请求分别记录 |
| 网页 HTTP 日志 | 模拟 CDP 网络事件记录 429、网络失败及关联 URL；不采集请求头、Cookie 或网络响应正文；监听失败时释放监听器 |
| 日志 | 终端/文件相同 JSONL、正文分段、明确截断、可选仅元数据、轮转、Cookie/Bearer/Basic/含转义引号密钥脱敏 |
| 后端协议与接口 | 实际 FastAPI TestClient 的 HTTP、WebSocket 桥接及 SSE 用例；单模型、融合、格式要求保留、JSON 不附加 Markdown 前缀、502 原因与请求编号、持久化及异常凭据过滤 |
| Agent CLI | 真实 localhost HTTP 服务与 `run.sh` 子进程；成功任务执行真实命令并读取文件。另复现首次无效协议、第二次 HTTP 502，检查退出码 2、零工具执行、具体 Qwen 错误、请求编号、双写日志与无自动重发 |
| 分析包 | 默认移除正文，`--include-content` 保留脱敏正文分段；两种模式均排除账号、工作区、任务记录和私有配置 |

执行命令（先安装项目依赖）：

```bash
npm test
npm run check
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -q
(cd desktop-agent && PYTHONPATH=src python3 -m unittest discover -s tests -q)
```

本轮父项目 Python 测试使用 Python 3.12 加项目 `.venv/lib/python3.12/site-packages` 中的依赖。FastAPI 测试运行在本地进程内，网页桥接由夹具响应；Agent CLI 使用真实本地 HTTP 端口。**没有真实模型账号、原生 Electron/Playwright 浏览器或 X11 桌面端到端验证**。DOM、CDP、OCR 与鼠标测试采用模拟对象，不能据此承诺当前线上页面全部兼容，也不能推断旧日志中那次 502 的真实服务端原因。

以下为历史版本记录；其测试数量和当时的环境限制不代表本轮验证状态。

另执行实际 `./package.sh --include-content` 合成环境检查：分析包可读取、正文保留、测试密钥移除、权限 0600，且包含 Agent 源码；没有加入工作区或账号目录。Bash 语法、Python 编译、三个 Skill frontmatter 检查通过。

## 1.3.1 Qwen 发送修复验证

2026-09-08：**107 项 Node/jsdom 测试通过、5 项 Python 配置与存储测试通过**，`npm run check` 通过。Node 测试包含原有 91 项及新增 16 项 Qwen 回归：CRLF 完整输入、带快捷键的发送标签、aria-labelledby、禁用重复控件、三种图标按钮结构、专用编辑器绑定、真实截断/禁用/遮挡/未知表单拒绝、原生输入与单次点击、完整 Markdown 回传，以及输入回包丢失/取消时不重复输入且释放 debugger。

```bash
npm test
npm run check
PYTHONPATH=tests python3 -m unittest test_storage -v
```

Qwen 集成用例由模拟 CDP 的 `Input.insertText` 触发合成页面输入事件，使发送按钮从禁用变为可用，再通过模拟鼠标释放点击并生成回复；它验证应用调用顺序和状态处理，**不等于真实 Chromium 输入事件或 Qwen 账号验证**。本次没有操作真实桌面、模型账号，也没有重跑未修改的 Agent 和后端 HTTP 测试。复测方法与诊断字段见 [Qwen 发送修复说明](QWEN_SEND.md)。

## 1.3.0 新增验证

2026-09-08：独立 `desktop-agent` 的 **109 项测试通过**，父项目诊断打包的 **11 项测试通过**（包含原有 8 项及新增 3 项 Agent 收集/隐私边界用例）。真实 localhost HTTP 夹具贯通 Agent CLI、命令执行、文件核验与 Markdown 终态。Bash 语法和 Python 编译检查通过。Agent 的原生浏览器、X11 桌面和真实模型账号未联调，完整范围见 [Agent 验证记录](../desktop-agent/docs/TESTING.md)。

以下保留 1.2.0 验证记录；1.3.0 没有重复执行未修改的父项目网页适配、Electron 和 UI 测试。

## 1.2.0 验证记录

日期：2026-09-08。交付源码、启动脚本及诊断打包脚本。没有使用真实账号验证模型网页。

## 本次已执行

| 范围 | 数量 | 验证内容 |
| --- | ---: | --- |
| Python Engine / Bridge | 19 | 单模型 canonical / 别名路由、5 个模型 × 2 种名称 × stream 开关的 20 个子用例、只分发一次且不融合、未启用/不存在拒绝、失败不回退、request_id 关联、并行来源与整轮串行、取消/维护锁/断线/超时、原文落盘 |
| Python 配置与存储 | 5 | 配置校验、已有配置不覆盖、令牌权限、Unicode Markdown 和 SQLite 持久化 |
| Python 浏览器登录读取 | 17 | 多浏览器检测、WAL/锁定、站点边界、Cookie 属性、隔离上下文、解密失败与不泄露会话值 |
| Python 日志 | 10 | 终端与文件相同 JSONL、字段白名单/凭据脱敏、轮转权限、重复配置、目录不可写时仅保留终端并可重试、拒写符号链接 |
| Python 诊断打包 | 8 | 代码与新旧日志采集、脱敏、默认不含对话/数据库/账号、显式 Markdown、文件大小上限、符号链接与自包含排除、不覆盖输出、无日志仍可打包 |
| Node 网页适配 | 25 | ChatGPT 旧配置命中新控件、CDP 一次可信点击/回车、发送确认及未确认错误、不自动重发、取消、Qwen 最新助手回合、多片段代码/表格、thinking/user/hidden 过滤、display:contents、aria-busy、根节点语义和 Markdown |
| Node Electron 登录导入 | 21 | 导入互斥、域名/来源/隔离边界、Cookie 属性、localStorage 同源、取消排空及报告脱敏 |
| Node 启动器 | 13 | 无 root 启动、显式 no-sandbox、参数与退出码/信号、失败诊断、原生 stderr 捕获和已有应用日志仅镜像一次 |
| Node 浏览器扩展 | 13 | 手动点击、当前模型范围、隔离 Cookie、localStorage 同源、导出上限、下载竞态与构建一致性 |
| Node 日志 | 6 | 终端/文件双写、凭据与正文键脱敏、轮转、拒写符号链接、跨 chunk / Unicode 与超长行 |
| UI 脚本 + jsdom | 13 | API 模型下拉与 curl/复制命令、启用模型刷新、聊天仍使用融合、安全 Markdown 渲染、历史、配置及登录导入流程 |

**合计 150 项测试通过：59 项 Python、91 项 Node/jsdom。** 上表中的参数化子用例包含在所属测试中，不重复计数。

执行命令：

```bash
# 本次使用环境提供的 Python 3.12 / Pydantic 运行以下核心测试
PYTHONPATH=tests python3 -m unittest test_engine test_storage test_browser_login test_diagnostics test_package_diagnostics -v
npm test
npm run check
bash -n run.sh package.sh
python3 -m compileall -q backend scripts tests
```

诊断打包另做了一次 CLI 合成环境烟测：实际项目代码加合成日志可以生成可读 tar.gz，权限 0600。所有浏览器和会话数据测试都使用合成数据库、DOM、Mock WebContents / CDP 或扩展 API。

## 1.2.0 当时的验证边界

1. **真实 ChatGPT / DeepSeek / Qwen / Claude / Grok 网页未联调。** 没有用户账号，也没有核验线上站点当前 DOM。针对已确认代码缺陷实现了补充匹配、可信输入与发送确认；不代表所有账号、语言及页面实验版本都已兼容。实际失败可用 package.sh 收集结构和阶段诊断。
2. **FastAPI HTTP / WebSocket / SSE 完整联调未执行。** 本环境没有 FastAPI/httpx/uvicorn，依赖下载此前受运行环境限制。tests/test_backend.py 随包提供但未执行，不计入通过数量。单模型 stream 开关验证的是共享 Engine 路由，不是 HTTP 流传输实测；新增响应头通过代码检查。
3. **原生 Electron 图形测试未完成。** 此前 localhost 夹具在当前运行环境启动渲染器时发生 SIGSEGV，并伴随 dbus/netlink/udev 权限错误；本次未重复启动，也没有 UI 截图。CDP 调用已通过模拟测试，但实际桌面行为需本机验证。
4. **实际模型答案质量、网页额度和生成完成判断仍需现场检查。** 完成检测依赖新回复、停止状态和稳定时间；长思考、未识别的思考区、公式/画布或页面结构变化可能需要进一步适配。不会用拼接兜底冒充语义整合。
5. **真实浏览器密钥环、设备绑定和验证码未验证。** 登录导入仍为手动单向导入。

取消后不会安排新的 CDP 输入事件，但已发出的 mousePressed/keyDown 无法撤回；不会补发可能触发提交的释放事件。导入取消同样不承诺撤回已提交的 Cookie/localStorage 操作。

## 目标 Linux 桌面复测

```bash
./run.sh --install-only
./run.sh --skip-install --check
./run.sh --skip-install
# 如果系统同时限制 SUID 与用户命名空间，只用于临时排障：
./run.sh --skip-install --no-sandbox
```

1. 选择 ChatGPT + DeepSeek，发送问题，日志应包含 adapter.dispatch、adapter.submission_accepted、adapter.complete；ChatGPT 输入应实际提交。未确认应明确报错，不再次点击。
2. 选择 DeepSeek + Qwen，发送包含标题、代码块、表格的提问，检查 Qwen 原文文件是否完整且无思考/工具栏内容。
3. API 设置页选择 web-qwen，复制命令调用；再以 model=qwen 调用，返回 fusion.mode 应为 single，只出现一个 candidate 任务，不出现 fusion 任务。多轮由客户端携带完整 messages。
4. 检查 logs/ 与终端的 request_id/job_id 可关联。复现后运行 ./package.sh；核对已保存 Markdown 时使用 ./package.sh --include-markdown，再上传生成的 tar.gz。
5. 确認此前配置、登录数据和历史仍能复用。诊断包只用于分析，不代替数据备份。

源码包含 npm run smoke:electron，其夹具只访问自己启动的 localhost 页面。当前环境未能完成这项测试。
