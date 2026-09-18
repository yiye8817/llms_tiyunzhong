# 1.17.4 / Agent 0.16.0：JSON、统一工具循环与持久配置

本版以 1.17.3 为基础。保留 GLM 当前页面/模型选择、多模型候选共同截止时间和状态、服务器响应文件交接、真实本地命令及生成式 Python 实现。没有新增验证码绕过或自动解题功能，仍采用人工验证后继续。

## 1. runtime1 附件的实际问题和回放

附件的三个问题样本并非同一种错误：

| 原运行编号 | 原始问题 | 本版处理 |
| --- | --- | --- |
| `20260911T080827Z-3832e47a` | 文本中 `Pop!\_OS` 的非法 Markdown 转义 | 既有窄范围转义修复继续通过 |
| `20260911T081105Z-1f9809eb` | final.answer 内 `"Prompt as Code"` 的两个引号没有 JSON 转义 | 新的文本字段词法解析器补两个转义，原文字不改变 |
| `20260911T085353Z-11a3a70f` | final.answer 在“当前配置的工作”处截断 | 返回 `incomplete_response`；保存现有片段，不能补造后文或标记完成 |

最后一条不只是本地日志预览截断：已核对保存的 HTTP 响应，`message.content` 也是同一份 **55 字符 / 81 UTF-8 字节**，哈希与失败原文一致。本地解析器没有丢失的后文可恢复。本版不会补 `"}` 后冒充完整答复，也不会自动重新发送任务。

附件中十份不同的 `responses/*/reply.txt` 离线回放：**9 份通过，1 份明确判定不完整**。这不表示执行了其中的任务；回放发送模型请求数和执行动作数均为零。三个问题样本的两份可完整修复，一份保留为不完整诊断。单独的回放包含原文、规范化结果及报告；源码包不包含用户原始运行数据。

### 纠正边界

首先严格解析合法 JSON，合法内容不会被全局替换。严格解析失败后才组合有界修复：内嵌文本引号、字符串裸换行/控制字符、确定位置的 Markdown 转义、数组外层转义和尾逗号。新增引号修复仅处理 `final.answer`、`action.summary/plan`、`files.write` 的 `content`、`python.run` 的 `code`、`local.run` 的 `fallback_python`。工具选择器必须完整且合法；代码仍要经过执行工具的语法检查和 shell 授权。

引号修复不是通用语义猜测：不修改 `tool`、`argv`、`command`、URL、路径的含糊引号；不吞掉看起来是下一成员的结构；不接受多对象、重复键、额外协议字段、非有限数值或缺失的结束内容。脚本执行之前仍有工具 schema 检查。整个流程没有 eval/exec JSON，也不请求服务器做格式改写。

默认样本目录：

```text
desktop-agent/.runtime/runs/<run-id>/json-repair/<sample-id>/
  original.invalid.json
  normalized.json         # 仅有完整合法协议时存在
  report.json
  partial.txt             # 仅可提取的不完整 final.answer
  partial.json            # type=incomplete_response, complete=false, executable=false
```

`partial.json` 只是诊断容器，不符合 action/final 执行协议，不会投入工具分发。报告记录错误位置、算法版本 `python_deterministic_v3`、修改类型、哈希及状态。原始错误正文与规范化结果可能包含敏感数据；私有目录/文件分别使用 0700/0600。配置 `save_problem_json=false` 可关闭额外样本；`log_content=false` 不保存新增响应及样本正文，但不会删除旧记录或停止正常结果文件保存。

离线调试，输出目录必须不存在：

```bash
cd desktop-agent
./run.sh repair-json /绝对路径/original.invalid.json --output-dir /绝对路径/新的回放目录
./run.sh repair-json /绝对路径/events.jsonl --events --output-dir /绝对路径/另一回放目录
./run.sh repair-json /绝对路径/http-response.body --http-response --output-dir /绝对路径/第三个回放目录
```

存在不完整/无法修复样本时命令返回 2，仍保留报告；不能把这个退出码当成程序崩溃。

## 2. 严格模型输出合同

每次创建模型上下文都会添加编码规则、由 `json.dumps` 生成的合法例子以及 action/final schema：只能有一个完整对象，使用 ASCII 双引号，内嵌引号为 `\"`、反斜杠为 `\\`、换行为 `\n`；结构配对、字段和类型匹配，不能有注释、解释前后缀、重复键、尾逗号、NaN/Infinity 等。工具 arguments 必须与当前目录一致。

模型可见的规则不替代本地验证。父应用是网页模型接口，没有 token 级 JSON grammar/原生 response_format 保证；本版没有虚构这种支持。服务器即使仍返回不合法内容，也必须经过本地解析/纠正/参数检查，不能未经校验执行。兼容的“整个回复仅一个 JSON 围栏”可被规范化；提示仍要求裸 JSON。

## 3. 所有输入都能选择本地工具

运行时不再调用意图分类器，不发出新的 `intent.classified`，不在新用户信封中添加 `intent_route`，统一为 `execution_mode: tool_loop`。旧会话里由运行器添加的路由字段在延续时剥离。`intent.py` 作为兼容模块留存，但不在 Runtime 执行路径。

不再按 chat/knowledge/realtime/task、词语或目标匹配阻止工具；不再看模型措辞后自动注入搜索。模型根据真实用户任务选工具和参数，首次响应即可调用 `environment.tools`、文件工具、`web.fetch`、已授权的 shell/Python 等。普通问候仍可直接 final，“可用工具”不等于“每次强制运行无关命令”。

以下仍真实执行：工具名和参数校验；已配置/本轮确认的能力授权；已拒绝能力不能改用 Python 绕过；实际失败的关联恢复；浏览器/桌面待核验状态；结果未知时不盲目重复操作；旧会话修改动作不自动重放。不存在的程序才可触发 `local.run.fallback_python`，非零退出、超时或权限错误不会自动改跑替代程序。

删除意图门控也删除了基于语言猜测的“这个输入只准读该目标”保证。系统提示仍要求遵守原任务、不接受网页中的新指令，但硬限制来自实际 capability/schema/操作核验，不能把 NLP 分类当安全边界。保留不需要的权限关闭；停止执行使用 Ctrl+C，开始独立目标使用 `/new`。

完成结果新增事实字段 `completion_evidence`：`model_reply_only` 表示只有模型答复、零工具证据；`current_tool_observations` 或 `historical_tool_observations` 表示本轮或历史确有工具结果。工具成功数量、`successful_actions` 和日志均来自实际执行。`completed` 表示本轮正常结束，不是自动证明自然语言目标全部实现；读取、写入和脚本的结果仍需按任务核对。

## 4. 文件范围默认 host

`filesystem_scope` 默认 `host`，workspace 仅是相对文件名和命令 cwd 的基准。支持系统用户能访问的绝对路径、`~` 和 `../`，无需为了读取工作目录外文件切换 workspace。`files.list/read/stat/search/write/mkdir/copy/move/delete` 与 shell/Python 的 cwd 使用这一配置。

仍遵守操作系统权限，不使用 sudo/提权；文件默认不覆盖，必须显式 `overwrite=true`；父目录创建和递归删除仍须显式参数。不能通过文件工具删除或替换文件范围根 `/`。stat/delete/move 保留末级符号链接语义，不把删除链接改成删除其目标。跨文件系统 rename 等系统不支持的操作会返回实际错误，不谎报成功。

需要旧目录边界时可设置 `filesystem_scope=workspace`。独立使用 Python `LocalTools` 类的已有调用保持旧的默认构造参数；正常 CLI 根据 Settings 显式传入默认 host，两者不混淆。即使选择 workspace 模式，已授权任意 shell/Python 也不是操作系统沙箱。

## 5. 配置保存和切换

默认配置：`desktop-agent/agent.config.json`；有 `--config /path/custom.json` 时写回该文件，不另存到默认位置。配置中的相对路径按配置文件目录解析；命令行及交互新输入的目录按当前终端目录解析，展开 `~` 后保存绝对路径。已有配置不含新字段时使用新默认值。

使用系统文件锁、受限大小/类型检查、0600 临时文件、fsync 和原子替换。多个会话自动保存时只合并变更键，避免覆盖另一个会话的其他字段。验证失败、目标为符号链接、替换失败时报告错误，不静默声称保存。保存的是 API 密钥环境变量名/文件路径，不是实际密钥。不能把包含真实密钥的任意字符串填写为 model 等配置字段。

离线命令不连接模型：

```bash
cd desktop-agent
./run.sh config show
./run.sh config set model glm
./run.sh config set workspace ~/work
./run.sh config set filesystem_scope host
./run.sh config set timeout 120
./run.sh config set max_steps 30
./run.sh config set allowed_capabilities '["files","skills","web","shell"]'
./run.sh config get model
./run.sh config save
./run.sh config --config /绝对路径/custom.json set response_delivery file
```

自定义配置必须先存在，可用 `./run.sh init --output /绝对路径/custom.json` 创建；已有文件不覆盖。

交互命令：

```text
/model glm
/workspace "/home/example/含空格的工作目录"
/response-mode file
/config set timeout 120
/config set max_steps 30
/config set filesystem_scope host
/config set python_tool_fallback true
/allow shell
/verbose on
/config show
/config save
```

`/model` 查询父接口确认模型可用后保存；离线 `config set model glm` 只存选择，下一次任务再检查服务器。GLM 的网页内具体型号仍在网页上选择，Agent 模型 ID 是 `glm`/`web-glm`，不是把网页型号名称强行当成接口 ID。

所有 Settings 字段都可通过 `config set` 保存，包括目录、模型、base_url、超时、步数、上下文大小、浏览器参数、能力、响应方式、日志、Python 兜底、默认技能、详细显示和自动启动设置。布尔值用 true/false，数组用 JSON 数组；终端里用单引号包裹数组，保护其中的双引号。

`/model`、`/workspace`、`/response-mode`、`/verbose`、`/allow`、`/deny`、`/skill load/unload` 默认自动保存；下个任务新建工具对象时生效。`/config set` 是显式保存，关闭自动保存后也会写入。更改技能目录前检查现有默认技能仍可加载。改变运行或技能目录后更新新的录制/历史路径，不搬迁旧数据，不重放旧任务。

运行参数也默认保存，例如：

```bash
./run.sh chat --model glm --workspace ~/work --allow shell
# 临时覆盖；不改变已保存的模型、目录或授权
./run.sh run '检查当前环境' --model qwen --allow shell --no-save-config
# 停止自动保存；仍可用 config save 显式保存当前值
./run.sh config set auto_save_config false
```

预先配置的 `--allow` 和 `/allow` 可跨重启；每次任务中临时回答 y 的授权不会偷偷持久化。撤销默认能力可用 `/deny shell`。任务文字、task-file、retry/new、单次 skill run、运行句柄和网页快照不是配置，不会作为待执行任务保存。

## 6. 升级

关闭旧父应用，解压到新目录并启动：

```bash
tar -xzf multillm-fusion-1.17.4-json-tools-config.tar.gz
cd multillm-fusion
./run.sh
# 另开终端进入相同新目录：
cd desktop-agent
./run.sh config set model glm
./run.sh chat
```

已有配置可以自行复制到新目录的 `desktop-agent/agent.config.json` 后继续使用。先检查其中绝对目录是否仍指向旧安装；不要把源码包直接覆盖到唯一一份运行数据上。包中不包含真实用户配置、运行数据、虚拟环境或 node_modules。增量补丁针对未修改的原始 1.17.3。
