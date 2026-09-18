# MultiLLM Fusion Desktop 1.17.10

Python + Electron Linux 桌面应用：在一个窗口登录多个大模型网页，通过统一对话提问，保存原始 Markdown，用大模型按语义整合回答，并提供本地 OpenAI Chat Completions 兼容接口。

**1.17.10（Agent 仍为 0.17.0）**：修复界面对话 Qwen 三级重试漏入/卡住：识别缺少角色标记的当前错误卡；仅在最新生成明确失败、无在途请求且同回合错误已核对时忽略残留停止按钮；未观察到新请求的等待不再被假忙状态拖到总超时。候选状态显示各级和人工倒计时，未启动也记录原因。小型 HTTP 200 JSON/SSE 错误响应在本地检测，不记录正文。见 [界面对话重试修复](docs/QWEN_CHAT_RETRY_11710.md) 与 [本版实测](docs/TEST_RESULTS_11710.md)。

**1.17.9 / Agent 0.17.0**：所有模型统一使用本地 JSON 修复，增加工具名 Markdown 自链接恢复；内置和用户技能合并发现，支持 `skill` / `/skill` 列表。Qwen 增加页面重试、截图辅助原生点击、默认 20 秒人工重试及成功后学习控件的三级恢复，每级独立落盘。详见 `docs/LOCAL_JSON_SKILLS_QWEN_RETRY_1179.md`。不刷新、不重新填写原问题；真实账号仍需现场验证。

**1.17.8（Agent 仍为 0.16.1）**：继续修复 Qwen / GLM 网页发送：重试使用独立的提交确认窗口，当前失败回合的屏外重试按钮在获取输入占用后可滚动并重新核验；网络错误归因检查当前用户消息与来源，不再把旁路保存/标题请求直接当作本轮回答。GLM 支持发送按钮在输入后才挂载的编辑器，并排除搜索、只读和回答示例输入框。真实账号网络问题仍需现场验证。详见 `docs/QWEN_GLM_FOLLOWUP_1178.md`。

**1.17.7（Agent 仍为 0.16.1）**：修复隐藏模型页面尚未显示就等待输入框的准备顺序，补充 GLM 无 role 富文本编辑器、包装层及英文输入框识别；长纯文本输入分段原生写入并逐段校验，富文本保留一次原生写入，均只提交一次。Qwen / GLM 点击后短暂保留交互页面直到观察到生成请求或达到上限，避免提前切换；新增输入长度、哈希与请求匹配诊断。不截断上下文，不改变代理或登录数据。真实账号网络错误尚未端到端验证。见 [发送修复说明](docs/QWEN_AGENT_GLM_SEND_1177.md) 和 [本版测试记录](docs/TEST_RESULTS_1177.md)。

**1.17.6（Agent 仍为 0.16.1）**：Qwen 显示 “Oops! There was an issue connecting to Qwen3.8-Max.网络错误” 时，识别当前回合的文字/图标重试按钮，并最多点击一次。支持错误卡和按钮延迟出现；点击前后核对会话与网络，人工或站点已开始新一轮生成时不再补点。保持总超时、模型选择，不刷新或重新填入问题。见 [Qwen 网络重试](docs/QWEN_NETWORK_RETRY_1176.md) 与 [本版实测记录](docs/TEST_RESULTS_1176.md)。

**1.17.5 / Agent 0.16.1**：修复 `events3.jsonl` 中 Python 源码字符串的 JSON 引号转义；为修复后的代码做只编译、不执行的语法检查并保存独立诊断源码。修复 GLM 用户消息先出现、首次会话 URL 后生成时的错误会话切换判断，以及多段输入/复制按钮干扰用户消息比对。支持两种已交付的 1.17.4 配置迁移。详见 [本版说明](docs/JSON_GLM_FIXES_1175.md) 和 [本版实测记录](docs/TEST_RESULTS_1175.md)。真实 GLM 账号端到端尚未验证。

**1.17.4 / Agent 0.16.0（历史版本）**：所有输入统一进入工具循环，文件默认 `host` 范围，workspace 仅为默认目录；模型、目录和 Agent 设置可自动保存。严格本地 JSON 检查、响应文件交接、失败记录、能力授权及结果核验继续保留。本版兼容此前两种 1.17.4 配置，不需要删除配置。历史说明见 [Agent JSON / 工具 / 配置](docs/AGENT_JSON_TOOLS_CONFIG_1174.md)。

**1.17.3 / Agent 0.15.0（历史版本）**：修复“解析网址中今天的内容”被当作闲聊的问题；新增本地工具发现、原生命令优先的 `local.run`、缺失工具的 `python.run` 实现，以及无需浏览器的 `web.fetch/web.read`。生成的 Python 先保存再执行，仍要求 shell 授权，不是系统沙箱；失败、超时和拒绝不会自动触发重复执行。详见 [本地工具与 Python 兜底说明](docs/LOCAL_TOOLS_PYTHON_FALLBACK_1173.md) 和 [实测记录](docs/TEST_RESULTS_1173.md)。

**1.17.2 / Agent 0.14.0（保留）**：增强本地 JSON 纠正并保存可回放样本；服务器回复默认落盘，再由本地 Agent 读回校验后解析执行。多模型对话新增默认 **60 秒**的候选共同截止时间和逐模型状态，全部成功并保存原文后才整合；整合超时单独配置。GLM 检测到可见验证码时暂停，支持打开原网页人工验证后继续，保持上一版不刷新的模型选择保护。详见 [本版使用与验证说明](docs/JSON_FILE_HANDOFF_AND_CHAT_1172.md)。

默认启用 **ChatGPT + DeepSeek**；配置中也预置 **Qwen、Claude、Grok、GLM、Kimi**，其中 GLM 和 Kimi 默认关闭。网页操作依赖 DOM 适配；这些预设是可调整的起点，不能保证所有账号、语言和页面版本都适用。交付环境没有你的登录账号，未完成这些真实网站的登录后端到端验证。

新增：**设置 → 浏览器登录** 可从 Firefox、Chrome 等浏览器导入模型站点 Cookie；同时附带扩展，支持导出当前站点的 Cookie + localStorage 后导入。详见 [浏览器登录导入说明](docs/BROWSER_LOGIN.md)。

1.2.0 修复网页发送与回复采集：使用 CDP 的可信点击/回车并检查网页是否接收请求；Qwen 按助手回合聚合 Markdown 片段。关键日志同步显示在终端并写入 `logs/`，新增 `./package.sh` 生成问题分析包。API 支持 `model: "chatgpt"`、`"deepseek"`、`"qwen"` 等单模型调用。

1.3.0 新增独立子目录 [desktop-agent](desktop-agent/README.md)：通过上述单模型 API 解析任务并执行命令行、文件、浏览器和 X11 桌面操作，保存 Markdown 结果；附带三个 Skill 示例及编写教程。核心命令/文件工具依赖 Python 标准库和 json-repair，浏览器和桌面依赖可按需安装，不使用 root。

1.3.1 修复 Qwen“已填入但没有点击发送”的多条路径：采用 CDP 原生输入，补充 Qwen 图标发送控件、快捷键标签和可访问名称识别，统一 CRLF/LF 输入校验。错误与日志给出具体等待原因；已有配置自动获得程序内的补充匹配。详见 [Qwen 发送修复说明](docs/QWEN_SEND.md)。

1.4.0 将发送正文、按钮定位、输入/提交核验、网页 HTTP 状态、模型回复及 Agent 工具参数和结果同步写入终端与文件，密钥等凭据继续脱敏。修复父接口强制 Markdown 与 Agent JSON 动作协议的冲突，保留 HTTP 502 的具体服务端错误；Agent 0.2.0 增加浏览器与桌面结果核验，未核验的操作不能直接报告完成。升级时请同时更新父应用和 `desktop-agent/`，重启父应用后再运行 Agent。

1.5.0 增加 **标签页 / 双栏并排 / 独立窗口** 三种显示方式，支持拖动分隔条调整对话区和网页区、左右模型网页的宽度。发送阶段依次为目标网页提供可见视口与输入焦点，避免后台标签页直接接受点击；输入完成后按顺序投递发送动作，继续等待回复。新增“检测发送按钮”，可查看 Qwen 当前页面实际命中的控件、禁用/遮挡、视口及焦点状态。使用方法见 [网页布局与发送诊断](docs/VIEWS_AND_SEND.md)。

1.6.0 默认双栏并排；Qwen 接收确认默认等待 120 秒、总生成等待 600 秒，所有来源成功完成后才融合。修复网页转 Markdown 时污染 JSON 的数组与网址；Agent 0.3.0 将每条日志同时写入终端、全局日志和本轮 `events.jsonl`，失败也保存 `result.md`。旧配置自动迁移，完整说明见 [慢响应与 Agent JSON 修复](docs/WAIT_AND_PROTOCOL.md)。

1.7.0 保留 Qwen 当前网页与手动模型选择，通过网页内新建对话替代每轮刷新，识别评分面板的明确关闭按钮并核验消失。Agent 0.4.0 修复缺依赖被误判为待核验操作导致提前退出的问题，增加当前环境的浏览器依赖检查与安装工具。详见 [Qwen 会话与 Agent 恢复说明](docs/QWEN_SESSION_AND_AGENT_RECOVERY.md)。

1.8.0 将 Agent 终端改为简洁步骤、必要选择提示和格式化结果，详细日志仅写入文件。Agent 0.5.0 增加分层 JSON 修复，命令中的歧义引号交回模型修正，校验后再执行；保留上一版依赖恢复与 Qwen 会话修复。使用方法见 [Agent 终端与 JSON 修复](docs/AGENT_TERMINAL_AND_JSON.md)。

1.9.0 / Agent 0.6.0 增加父应用自动启动、交互输入与模型切换、调试命令、日志打包和 demo Skill 生成。针对 `agent2.tar.gz` 修复报告的字面量换行、越界目录恢复提示，并防止未闭合 JSON 因页面短暂停顿提前回传。日志分析、修改方案与使用方式见 [Agent 交互与附件修复](docs/AGENT_INTERACTIVE_AND_RECOVERY.md)。

1.10.0 / Agent 0.7.0 增加灰色输入候选（右方向键或 Tab 接受）、关联当前请求的网页进度、`--v` / `/verbose on` 详细显示开关、Skill 动态加载和静态测试、历史任务与交互会话查询、缺失工具的别名和 CLI 替代建议。默认只显示必要步骤与格式化结果；详细日志继续写入文件。请同时更新父应用和 Agent 并重启父应用，以启用网页进度。完整命令与阶段说明见 [Agent 补全、进度与历史](docs/AGENT_COMPLETION_PROGRESS_AND_HISTORY.md)。

1.11.0 / Agent 0.8.0 修复 ChatGPT 的可信输入、ProseMirror 多段文本核验与发送按钮识别；新增对话录制 Skill：`创建skill` → 录入步骤 → `结束创建skill` → 查看名称建议 → 显式确认创建。录制内容不会作为任务执行，确认后可以 `/技能名称` 调用。完整方法见 [ChatGPT 发送与 Skill 录制](docs/CHATGPT_SEND_AND_SKILL_RECORDING.md)。

1.12.0 / Agent 0.9.0 修复异常引用与中文换行显示；网页失败时在原请求内尝试本轮重试，无效则等待用户手动重试并继续采集。交互中的“继续”和补充提示继承原目标、工具结果与待核验状态，`/new` 才开始新上下文。用法与日志原因见 [显示、网页恢复与上下文延续](docs/RECOVERY_CONTEXT_AND_FORMATTING.md)。

1.13.0 针对 Qwen3.8-Max “目前服务访问量较大”错误卡（同时兼容“当前”文案），可在确认当前回合、唯一可点击的重试控件和同源页面后，对红色卡片下方的圆形箭头执行最多一次可信点击；不满足门控时保留现场并请用户手动重试。新增默认关闭的 GLM 与 Kimi 网页模型，旧 schema 升级时只追加缺失项，不覆盖现有 URL、代理和选择器。详见 [Qwen 发送与重试](docs/QWEN_SEND.md) 和 [GLM / Kimi 配置](docs/GLM_KIMI.md)。

1.17.0 / Agent 0.13.0 支持为每个网页模型配置 **0–3600 秒最小访问间隔**。同一模型的下一次网页提交过快时，会先进行可取消的延迟，再打开或操作网页；等待时间计入桥接超时预算，并写入文件日志。桌面代理只显示简洁进度，例如“Qwen · 访问过快，正在限速等待（12.5 秒）”。设为 `0` 保持原行为。1.16.0 的本地 Python payload 修复器继续保留。

1.17.1 修复 GLM 手动选择 `glm-5.3-flash` 后被下一次任务刷新重置的问题：复用同源页面、通过站内新建对话清空旧消息，并在可识别的模型发生变化时停止发送。首次打开、主动刷新、修改网址/代理仍会加载。验证范围和使用说明见 [GLM 不刷新](docs/GLM_NO_REFRESH.md)。

## 1. 启动

要求：Linux x86_64/arm64 图形桌面，Python 3.10+，Node.js 22.12+，npm。首次需要联网安装 Python/npm 依赖及 Electron 二进制。源码包不包含虚拟环境、node_modules 或模型权重。

```bash
tar -xzf multillm-fusion-1.17.10-qwen-chat-retry-fix.tar.gz
cd multillm-fusion
./run.sh
```

Ubuntu/Debian 如果没有 venv，先安装 `python3-venv`。Fedora 安装 Python、pip 和 Node/npm 后在普通用户桌面终端运行。不要使用 sudo 启动应用。

```bash
./run.sh --install-only       # 只安装
./run.sh --skip-install       # 已安装后的快速启动
./run.sh --check              # 后端与网页适配器本地测试
./run.sh --skip-install --no-sandbox  # 系统不允许用户命名空间时的临时排障选项
```

需要检查后端错误时，可运行 `./run.sh --backend` 在终端前台启动服务。它单独运行时不具备网页能力；再启动桌面程序才会连接网页桥接。

启动后：

1. 在“设置 → 模型网页”启用需要的模型；再从“设置 → 浏览器登录”导入已有登录，或在右侧模型页手动登录。GLM 和 Kimi 默认不启用；导入后仍需在网页确认状态。
2. 在“设置”中选择参与模型和“语义整合”模型。默认整合使用 ChatGPT 网页，无需官方 API Key。
3. 回到统一对话，输入问题并发送。可以在右侧观察网页的生成过程。
4. 左侧显示整合结果；展开模型原文可查看各模型回答并导出 `.md`。

统一对话保留完整历史。网页任务使用干净会话并发送完整逻辑历史；Qwen 和 GLM 复用已打开的同源页面，通过站内新建对话清空旧消息以保留模型选择，其他模型仍从配置首页开启会话；整合步骤也用干净上下文。这会在模型网站建立多条会话，但能防止不同 API 请求及整合提示互相污染。它不会继续你在右侧手动打开的任意旧会话。

## 2. 能力与实现

| 需求 | 实现 |
|---|---|
| 打开两个或更多模型网页 | Electron WebContentsView，默认双栏；也可选标签页和独立窗口 |
| 保存和导入登录 | 每个模型独立 Electron session；支持 Firefox/Chrome 等配置读取、扩展 Cookie + localStorage 文件导入 |
| 统一对话 | 一个输入框；Python 分发；不同模型并行生成 |
| 获取 Markdown / JSON | 普通回答只提取匹配的助手回答容器并用 Turndown + GFM 转换；Qwen 在提示要求 JSON 时依次尝试 HTML 原文、可信点击“复制 Markdown”读取剪贴板、实时 DOM 文本三条路径，只接受完整严格 JSON。成功结果额外保存为 `runs/<request_id>/<provider>.json`（融合结果为 `merged.json`），原始 `.md` 和 API 内容仍保留 |
| 语义整合 | 二次调用指定网页模型，或调用配置的 OpenAI 兼容服务 |
| OpenAI 接口 | `GET /v1/models`、`POST /v1/chat/completions`，普通 JSON 和 SSE |
| 历史和文件 | SQLite 对话历史；每次运行保留原文、整合结果与元数据 |
| 失败处理 | 默认要求全部来源成功；失败保留已完成原文并返回错误，可明确开启部分结果 |
| 代理 | 设置每个模型独立的 Electron proxy rules |
| 网站改版 | 设置页修改 input/send/assistant/stop 等 CSS 选择器数组 |

没有使用“相似段落拼接”冒充语义整合。默认路径会真正再调用一次大模型，将多个回答去重、归纳、保留互补信息，并标出无法确定的矛盾。模型也可能整合出错，原文始终保留供核对。

### 网页整合（默认，无需额外 API Key）

例如两个来源 ChatGPT、DeepSeek，整合者 ChatGPT：先获得两份原文，再把问题与原文发给 ChatGPT 整合，总计三次网页生成。需要对应账号额度。来源内容也会发送给整合者。

### API 整合（可接本地模型）

设置 → 语义整合 → API：

- Base URL：你的服务地址，以 `/v1` 结尾，例如 `http://127.0.0.1:11434/v1`。
- Model：该服务已经加载、支持聊天的模型名。
- API Key：该服务要求的密钥；本地无需认证的服务可留空。

这里只替换整合阶段：前面的来源回答仍来自 Electron 中打开的网页。应用不自动下载或启动本地模型。不要把整合 Base URL 指回本应用的 8765 端口，否则会递归等待。

## 3. 调用 OpenAI 兼容接口

桌面程序需要保持运行，且网页已登录。API 默认只监听 `127.0.0.1:8765`；设置页可查看地址并复制随机生成的令牌。

```bash
export FUSION_KEY="$(cat "$HOME/.local/share/multillm-fusion/api-key.txt")"

curl http://127.0.0.1:8765/v1/models \
  -H "Authorization: Bearer $FUSION_KEY"

curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $FUSION_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"web-fusion","messages":[{"role":"user","content":"如何分析 Android native 内存泄漏？"}],"stream":false}'
```

模型名称：

- `web-fusion`：分发至所有启用来源并整合。
- `chatgpt` / `web-chatgpt`、`deepseek` / `web-deepseek`、`qwen` / `web-qwen`、`claude` / `web-claude`、`grok` / `web-grok`、`glm` / `web-glm`、`kimi` / `web-kimi`：仅调用指定的已启用网页，直接返回采集到的回答，不执行整合。默认使用 Markdown；提示明确要求 JSON 等格式时尊重该要求。
- 自定义 provider 使用 `web-<provider-id>`。`GET /v1/models` 仅列出已启用模型；未启用的单模型请求返回 `model_not_enabled`（HTTP 404）。

例如只向 Qwen 提问，先在设置启用 Qwen 并确认网页已登录：

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $FUSION_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen","messages":[{"role":"user","content":"分析 Android native 内存泄漏的排查步骤"}],"stream":false}'
```

将 `qwen` 改成 `chatgpt`、`deepseek`、`glm` 或 `kimi` 即可单独调用对应网页；`stream:true` 同样遵循这个模型选择。GLM/Kimi 的启用、登录与 API 示例见 [GLM / Kimi 网页模型](docs/GLM_KIMI.md)。设置 → API 接入的“调用模型”可直接生成对应 curl 命令。响应包含 `X-HTTP-ID`，已创建模型任务的响应还包含 `X-Request-ID`，用于查找对应日志。

普通响应包含标准 `choices[0].message.content`。扩展字段 `fusion` 包含 `conversation_id`、`request_id`、`sources`、`errors`、`mode`、`merged_path`。已有逻辑会话可继续发送完整 `messages`，并带上 `conversation_id` 保存到同一历史记录。该字段不会替你补齐缺失的上下文。

Python SDK 示例见 [`scripts/api_example.py`](scripts/api_example.py)：

```bash
python3 -m pip install openai
python3 scripts/api_example.py
python3 scripts/api_example.py --model qwen
```

请为调用者设置足够长的超时，例如 600 秒，并设置 `max_retries=0`，避免不确定的超时后自动重复向网页发问。

### SSE 和兼容范围

`stream:true` 使用 `text/event-stream`，等待期间发送 SSE 注释保活；整合完成后输出 `chat.completion.chunk`，最后 `[DONE]`。这是**最终整合文本分块输出**，不是底层网页的实时逐 token 流。反向代理或客户端可能缓冲 SSE 注释。

支持文本 `system/developer/user/assistant` 消息。网页 UI 控制温度、token 限额等生成参数；本接口严格拒绝未支持字段，包括 `temperature`、`max_tokens`、`stream_options`、工具调用、图片/音频和 `response_format` 等结构化输出字段（HTTP 422）。可以在文本消息中要求 JSON，1.4.0 不再用附加 Markdown 要求覆盖它，但网页模型仍可能返回格式错误。具体校验见 `backend/models.py`。不实现 `/v1/responses`、embeddings、文件上传或原始网页账号额度查询；不要据此假定所有 OpenAI 客户端功能都兼容。

API 调用共享网页会话资源，因此整轮请求排队执行，单轮不同来源并行。队列有上限。不要用多个服务 worker 启动后端。

## 4. 数据和配置

默认目录：`~/.local/share/multillm-fusion/`；可通过 `FUSION_DATA_DIR` 指定。

- `config.json`：实际配置；首次从源码 `config.example.json` 初始化。1.13.0 将旧 schema 1/2 升级为 3 时，只追加尚不存在的 `glm` / `kimi` 默认关闭项；已有同名配置、顺序、URL、代理和选择器保持不变。之后修改示例文件不会覆盖实际配置。
- `api-key.txt`：本机访问令牌，只允许当前用户读取。
- SQLite 数据库：保存逻辑对话和运行记录。
- `runs/<request_id>/`：各来源 `.md`、`merged.md`，Qwen/整合结果若严格解析为对象或数组还会有对应 `.json`，以及失败来源的 `errors.json`（如有）。问题、成功运行的源信息和整合记录保存在 SQLite。
- Electron 会话目录：网页 Cookie、登录会话、本地存储。
- 日志从 1.2.0 起单独写到项目 `logs/`，可用 `FUSION_LOG_DIR` 指定；不存入对话数据库。

实际配置若填写了整合 API Key，会以本机配置文件保存；不要把运行数据或完整配置上传公共仓库。导出按钮可选择任意 `.md` 保存位置。

```bash
FUSION_DATA_DIR="$HOME/.local/share/my-fusion" FUSION_PORT=8877 ./run.sh
```

对后台网页设置 `backgroundThrottling:false`，采集不调用窗口抢焦点；未选中的模型仍可生成。但窗口系统和站点自己的后台行为仍可能影响页面执行。

## 5. 代理和常见问题

### 依赖下载失败

Python/npm 会沿用命令行代理；Electron 二进制也可设置镜像。依赖下载代理与设置页的网页代理是两套配置。

```bash
HTTPS_PROXY=http://127.0.0.1:10822 \
HTTP_PROXY=http://127.0.0.1:10822 \
ELECTRON_GET_USE_PROXY=1 ./run.sh
```

镜像由你选择并验证可信性，例如通过 `ELECTRON_MIRROR` 指向已有镜像。`run.sh` 不修改全局 pip/npm 配置，也不执行远程安装 shell 脚本。

### ChatGPT 或其他网页超时

在模型配置的“代理”填入 `http://127.0.0.1:10822` 或 `socks5://127.0.0.1:10822`，保存后检查网页。代理留空使用系统代理配置。网页异常后，程序只在确认本轮错误及对应重试按钮时自动点击一次；Qwen3.8-Max 红色“服务访问量较大”卡片下方的无文字圆形箭头，也必须通过当前回合和唯一控件门控才会点击。无效则提示人工处理，并在有限窗口内等待本轮完整回答。不会重新填入整条提示。`recovery_timeout_seconds: 0` 会关闭重试窗口，但错误卡仍是失败而不是模型回答；默认要求全部来源成功，因此不会带着该错误卡进入融合。检查右侧是否登录、是否等待验证、额度是否耗尽；详见 [网页恢复说明](docs/RECOVERY_CONTEXT_AND_FORMATTING.md)。

### 登录提示不支持浏览器 / 验证码

可先在“设置 → 浏览器登录”尝试导入现有 Firefox/Chrome 登录，Cookie 以外的 localStorage 可通过随包扩展导出后导入。导入不保证站点接受另一浏览器中的会话；仍可在网页内使用正常登录方式。程序不会绕过验证码或设备绑定。详见 `docs/BROWSER_LOGIN.md`。

### 发送后一直等待 / 读取为空

网页升级、界面语言、A/B 测试会改变 DOM。设置页的“选择器”是按优先级排列的 CSS 数组：

- `input`：输入框或 contenteditable。
- `send`：发送按钮；程序补充模型已知控件及发送语义识别。Qwen、ChatGPT、GLM、Kimi 必须匹配发送控件，不会盲按 Enter；其他模型仅在没有歧义表单按钮时可尝试单次 Enter。不会重复点击后再重发。
- `assistant`：**仅助手回答**的容器；不要选整个 body、聊天区或同时包含用户提问的节点。
- `stop`：生成期间的停止按钮；正确匹配可以减少长思考时被提前采集的概率。
- `new_chat`：Qwen 用于站内新建空白对话；优先明确命名控件，阻止完整页面导航。其他模型当前仍通过配置首页 URL 建立干净上下文。

1.2.0 在匹配配置外补充 ChatGPT/Qwen 控件与回合识别，不覆盖已有账号、代理和自定义配置。发送使用绑定当前网页的 CDP 输入通道，不再派发网页可能忽略的 synthetic `KeyboardEvent`；不会为了发送或采集切换窗口到前台。点击/回车只投递一次，之后必须看到生成标识、新助手内容或匹配的新用户回合才确认网页已接收。未确认会报 `submission_unconfirmed`，保留现场供检查，不自动再次点击。若报 `debugger_unavailable`，关闭该模型网页的开发者工具后手动重试。

Qwen 会将同一助手回合内的多个 Markdown 片段合并采集，并去除已识别的用户消息、思考区和工具按钮；避免仅取最后一段或被旧回答的选择器遮挡。网站没有统一可靠的“回答完成”API。已识别的生成网络流未结束时不会提前完成；未识别时通过新回答检查、停止按钮和内容稳定时间推断完成；长时间思考、网页漏匹配停止按钮可能导致误判。可以增大 `stable_seconds`、`min_wait_seconds`、`timeout_seconds`。表格/代码转换经过本地测试；公式、脚注、引用组件或画布内容可能需要站点专项适配，不能保证恢复网站原生 Markdown 的每个字符。

若 Qwen 报 `send_not_ready`，1.3.1 会展示具体原因和输入长度。终端及 `logs/electron.log` 的 `adapter.send_state` 可以区分 `input_changed`、`send_missing`、`send_disabled`、`send_obscured` 等情况。Qwen 填充与发送仍各执行一次，通道错误后不重复输入，也不改为回车盲发。

### 关键日志与上传分析包

默认 `logs/electron.log` 记录实际发送正文、输入回读、发送按钮匹配和坐标、CDP 操作回执、网页接收证据、网页 HTTP 状态及最终 Markdown/JSON 提取方式（不记录剪贴板正文）。`logs/backend.log` 记录 API 请求和响应、排队、模型分发、融合和保存结果；`logs/launcher.log` 记录启动及原生错误。父应用日志继续同步输出终端。Agent 的 `.runtime/logs/agent.log` 记录任务、每次模型请求/原始回复、协议修正、工具参数/结果及核验状态，详细日志不再输出终端。各全局日志最多 5 MiB，保留 3 份轮转文件。Agent 另将同一条记录立即写入 `.runtime/runs/<run_id>/events.jsonl`，所有终态保存 `result.md`，终端只显示步骤、选择提示和格式化结果。

使用 `run_id`、`http_id`、`request_id`、`job_id` 关联各层记录。正文以 `payload` 分段记录，单份载荷最多 2,000,000 字符，超限明确标记 `truncated:true`；凭据脱敏。设置 `FUSION_LOG_CONTENT=0` 可只记录元数据。日志中的 CDP 回执表示操作已投递，`adapter.submission_accepted` 才表示观察到网页接收证据；网页回答完成仍依赖页面状态与内容稳定检查。

复现问题后执行：

```bash
./package.sh
# 排查模型 JSON、发送内容或工具结果，主动保留脱敏后的日志正文：
./package.sh --include-content
# 需要核对已经保存的 Markdown 内容时：
./package.sh --include-markdown
```

默认包位于 `diagnostics/`，包含代码、去除正文的脱敏日志、配置摘要、环境版本和文件清单；不自动上传。`--include-content` 保留日志正文，`--include-markdown` 额外收集父应用保存的 Markdown，两者可以同时使用。可用 `--agent-run-id <run_id>` 额外收集指定 Agent 任务的 `events.jsonl`；不会收集其完整会话记录、工作区、截图、浏览器配置或私有配置；不包含 Cookie、API key 文件和 SQLite 数据库。分享含正文的包前可先解压检查。若启动时设置了 `FUSION_DATA_DIR` / `FUSION_LOG_DIR`，打包时使用同样的变量。完整说明见 [日志和问题分析包](docs/DIAGNOSTICS.md)。

### Agent 出现 invalid_protocol 后 HTTP 502

1.6.0 修复 HTML 转 Markdown 给 JSON 数组添加反斜杠、给 URL 添加链接语法的问题。JSON 格式损坏先由本地 Python 确定性修复器处理；失败后依次使用开源 `json_repair` 和一次隔离的大模型修复。每个阶段都会写入日志，所有候选仍须通过本地协议与工具 schema 校验；失败时不自动重发原任务。详见 [JSON 修复流程](docs/AGENT_TERMINAL_AND_JSON.md)。HTTP 502 会保留服务端错误码、请求标识及具体来源错误，不会自动重试原请求。

### Electron 沙箱 / 图形环境

1.1.1 的 `./run.sh` 和 `npm start` 在 Linux 默认添加 `--disable-setuid-sandbox`，尝试不需要 root 的用户命名空间沙箱。无需修改 `chrome-sandbox` 的所有者或权限，也不修改系统参数。如果系统拒绝创建命名空间，仍会启动失败；启动器会保留错误并输出排障提示，不会自动重启或关闭全部沙箱。

系统禁止用户命名空间、且当前只需要临时验证应用时，可明确选择：

```bash
./run.sh --skip-install --no-sandbox
# 或已安装依赖时：
npm start -- --no-sandbox
```

`--no-sandbox` 无需 root，但关闭所有 Chromium 进程沙箱；网页漏洞可能影响当前用户能访问的文件和登录数据。Electron 官方将它列为测试用途，不建议长期用于登录真实网站。`nodeIntegration:false`、`contextIsolation:true` 和 `sandbox:true` 仍保留在网页配置中，但不能替代被这个参数关闭的进程沙箱。选项只对本次命令生效，不写入配置；下次普通启动继续尝试用户命名空间沙箱。

旧版 1.0.0/1.1.0 也可以运行 `./run.sh --skip-install -- --disable-setuid-sandbox`；若依然报 `No usable sandbox` 或命名空间权限错误，临时排障命令是 `./run.sh --skip-install -- --no-sandbox`。注意旧版需要中间的 `--`。

Wayland 显示异常可试 `./run.sh -- --ozone-platform=x11`；无图形桌面时无法完成网页登录。机制依据：[Chromium Linux 沙箱](https://chromium.googlesource.com/chromium/src/+/lkgr/sandbox/linux/)、[Electron 进程沙箱](https://www.electronjs.org/docs/latest/tutorial/sandbox)。

## 6. 源码结构

- `desktop-agent/`：独立 CLI Agent、28 个工具、任务记录、日志、三个 Skill 及扩展教程；启动方法见其 README。
- `electron/`：主进程、受限 preload IPC、网页生命周期与 DOM 适配。
- `scripts/launch-electron.cjs`：无需 root 的启动及原生诊断；`package.sh` / `scripts/package_diagnostics.py`：本地问题分析包。
- `backend/`：FastAPI、WebSocket 网页桥、请求调度、语义整合、SQLite。
- `ui/`：桌面聊天、模型标签、设置、历史及 Markdown 展示。
- `config.example.json`：七类模型的可修改默认配置。
- `tests/`：本地模拟站点/桥接、浏览器导入与后端接口测试。
- `browser-extension/`：Chrome 家族与 Firefox 的本站登录导出扩展。
- `browser-login-sites.json`：浏览器导入允许的模型站点范围。
- `requirements-browser.txt`：Chromium 解密可选依赖。
- `docs/TESTING.md`：本次验证范围、结果与未验证项。

设计依据：[Electron WebContentsView](https://www.electronjs.org/docs/latest/api/web-contents-view)、[Electron 安全建议](https://www.electronjs.org/docs/latest/tutorial/security)、[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create/)。网页使用独立隔离上下文并禁用 Node integration；API 令牌不注入模型网页。
