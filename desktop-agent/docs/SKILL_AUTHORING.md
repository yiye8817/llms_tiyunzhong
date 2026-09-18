# 给 Desktop Agent 编写 Skill

适用版本：Desktop Agent **0.10.0** / 父应用 **1.14.0**。

这里的 Skill 是这个子项目读取的本地任务方法，不会安装到 Codex、ChatGPT 或其他程序的全局技能目录。它告诉模型何时采用一套流程、调用哪些现有工具、怎样核验结果。Python 工具实现负责真正执行动作；Markdown 不能创建工具或增加权限。

## 交互录制：先记录、再命名确认

运行 `./run.sh` 进入交互，用 `/skill create [描述]` 或单独输入 `创建skill` 开始录制。描述是可选主题，不算一个操作步骤。后续每次提交的内容作为一个步骤保存；顺序和文字保留，多行输入属于同一步。录制过程不调用这些步骤中的工具、CLI 或网页任务。

| 当前状态 | 输入 | 结果 |
| --- | --- | --- |
| 普通输入 | `/skill create [描述]`、独立的 `创建skill` | 保存新草稿，进入 `skill[录制 0 步]>` |
| 录制 | 普通文字、CLI 字符串、`/model`、`/retry`、`/new`、`/context` 等非录制控制输入 | 追加步骤并保存；不执行、不切换模型、不清空上下文 |
| 录制 | `/skill finish`、独立的 `结束创建skill` | 先保存待确认草稿，再请求当前模型建议名称、描述与可选标题 |
| 录制或待确认 | `/skill preview` | 查看已录入内容或生成的 Skill 草稿 |
| 待确认 | `/skill name NAME` | 验证并修改建议名称，不发布 |
| 待确认 | `/skill confirm [NAME]`、独立的 `确认创建skill` | 确认名称，原子创建技能，返回普通输入 |
| 录制或待确认 | `/skill cancel`、独立的 `取消创建skill` | 保留取消草稿，返回普通输入 |
| 录制或待确认 | `/quit`、Ctrl+D、输入处 Ctrl+C | 退出，保留未发布草稿 |

所有转换都由明确状态、完整命令、参数验证和确认决定，不使用分类器推断控制意图。`请不要把“结束创建skill”当成步骤` 这种普通句子，或包含该词的多行粘贴，并不会结束录制。录制时想调整模型，应先取消并在普通输入状态使用 `/model`；直接输入 `/model qwen` 只会记录这段文字。0.9.0 的上下文延续规则不改变录制门控：录制中的“继续”、`/new`、`/context` 仍作为步骤文字保存，不继续原任务、不建立新会话、不查询上下文。待确认阶段输入普通文字不会追加步骤或执行任务；需要改步骤时取消后重新录制。

完整示例，以下每行分别作为一次输入提交：

```text
/skill create 为工作区生成可核对的顶层文件报告
用 files.list 列出当前工作区顶层条目，检查 truncated，并记录名称、类型和字节数。
按观察到的类型统计数量，写入新的 workspace-count.md；同名文件存在时报告失败，不覆盖。
用 files.read 回读报告，核对统计和截断说明，再告诉用户保存结果及范围。
结束创建skill
```

到这里仅保存步骤并进入待确认。当前选中的模型会根据这些内容提供建议，例如 `workspace-count`；名称只是示例，实际应检查界面中的建议和草稿后决定。模型仅接收命名任务，不接入工具执行循环；回复只允许 `name`、`description` 和可选 `title`，不能返回或替换录制步骤。系统会检查严格 JSON 字段和命名规则，仅修正可确定的首部 BOM、整体 JSON 围栏和尾逗号等格式问题。

用户可以接受建议，也可以显式换名后确认：

```text
/skill preview
/skill name workspace-count
/skill confirm workspace-count
```

`/skill confirm` 不带参数，或独立输入 `确认创建skill`，确认的是当前显示的名称；不能在尚未结束录制时提前确认。命名须遵守下方 frontmatter 名称规则，还不能占用 `help`、`model`、`retry`、`new`、`context`、`skill`、`quit` 等现有交互命令名。确认后仅创建 `skills_dir/NAME/SKILL.md`，不会生成或执行步骤中提及的脚本、不会自动 `load`，也不会运行新技能。

名称建议需要当前模型与父应用可用。结束命令会在请求前保存草稿；若 API 出错、模型返回无效元数据或用户中断命名建议，录制内容保持不变，仍可 `/skill name NAME` 后 `/skill confirm NAME`，采用本地已保存的描述完成创建。不自动重试命名 POST，也不会因命名失败丢弃步骤。

每次已接受的录制修改都会先保存到 `.runtime/skill-drafts/<草稿编号>.json`；`--v` 或已开启的详细模式会显示准确路径。录制最多 128 个步骤，主题与步骤合计最多 48 KiB，生成的 `SKILL.md` 仍不得超过 64 KiB。超限或保存失败时明确拒绝本次修改。已知凭据会替换成脱敏标记；模型只建议元数据，不改写剩余步骤文字。

确认使用不覆盖已有条目的原子目录创建；名称冲突、路径符号链接或不支持原子发布时明确失败，可在待确认阶段改名再确认。取消或退出会留下未发布的草稿记录；当前没有跨进程恢复命令，重启不会自动恢复录制状态。

## 创建后运行、动态加载与测试

确认创建后无需重启，技能立即进入发现目录与输入候选。普通交互状态下，`/NAME [任务描述]` 只匹配当前真实存在、入口合法且没有占用内置命令名的技能。省略任务描述时，按该技能保存的目标和步骤执行；给出描述时，以该描述作为本轮要求，并预加载此技能。每次调用创建独立任务记录和日志，同时继承本次交互的原目标、模型回复、工具结果与未完成状态；全新目标可先使用 `/new`。运行时的参数、工作区、能力授权及结果核验规则照常生效。

```text
/skill test workspace-count
/workspace-count
/workspace-count 只统计当前工作区顶层条目，保存到新的 second-count.md 并回读核对
/skill load workspace-count
/skill reload
/skill unload workspace-count
```

`/NAME` 和 `/skill run NAME 任务描述` 为本轮增加该技能；`/retry` 保留上一提示的一次性技能选择，使用当前模型和工作目录，在现有上下文中重试上一提示并创建新记录，不自动重放已派发的修改动作。普通输入“继续”或补充要求也保留上下文；启动检查失败、尚未取得上下文时，“继续”会重新尝试原输入。`/skill load NAME` 为后续任务预加载，`unload` 移除该选择。每次任务重新读取当前 `SKILL.md`，不缓存过期正文；非法修改、读取失败或空正文会明确失败，`reload` 可提前检查当前选择。生成、确认、读取与结构检查本身均不运行录制步骤。

上下文延续不继承临时能力授权、浏览器或桌面句柄。技能中应要求重新获取本轮的快照或桌面观察，不沿用旧 `snapshot_id`、`tab_id`、`observation_id`，也不能把读取历史记录当成已核验当前状态。上一轮失败、结果不确定或等待核验的操作仍须处理；网页模型超时后的继续不是从第一步盲目重跑。普通状态的 `/context` 查看摘要，`/new` 清空当前内存上下文并建立新会话分组；`/history` 跨进程只读查询，不恢复执行。`./run.sh run ...` 单次命令行调用仍独立运行。

命令行同样可用：

```bash
./run.sh skill-test workspace-count
./run.sh run "按技能中的目标和步骤生成工作区报告，并检查实际结果" --skill workspace-count
```

`skill-test` / `/skill test` 静态检查目录、frontmatter、非空正文、引用资源及 Python AST 语法，不执行用户脚本、不连接模型，也不验证任务效果。其他脚本语言只报告结构检查及语法未检查。通过返回 `0`，失败返回 `2`；资源检查限制为 256 个条目、8 层目录、单文件 1 MiB、总计 4 MiB。“结构检查通过”之后，仍须在临时工作区实际运行，并核对输出、失败和核验行为。录制里提到了脚本或资源路径，不会自动生成它们；需要作者按实际方法补齐对应资源。

## 快速生成可运行的 Demo

```bash
# 在 desktop-agent 目录执行；不需要父服务器或模型
./run.sh skill-demo workspace-report-demo
./run.sh skills workspace-report-demo
./run.sh skill-test workspace-report-demo

# 使用默认文件工具完成，不需要 shell 权限
./run.sh run "生成工作区顶层文件清单，保存新的 workspace-report.md 并核验" \
  --skill workspace-report-demo
```

`skill-demo` 的名称可省略，默认 `workspace-report-demo`。交互界面使用 `/skill-demo workspace-report-demo`。生成位置取自当前 `skills_dir` 配置：

```text
skills/workspace-report-demo/SKILL.md
skills/workspace-report-demo/scripts/workspace_report.py
```

命令先构造并检查入口的名称和 frontmatter，再完整写入临时目录，最后通过 Linux 的 `renameat2(RENAME_NOREPLACE)` 一次发布。并发创建同名技能也只能成功一次；任何现有同名目录、文件或符号链接都不会被替换。路径组件不跟随符号链接，`..` 路径被拒绝。文件为 `0600`，新目录为 `0700`；系统或文件系统不支持原子发布时明确失败，不退回可能覆盖文件的方案。

Demo 适用于“工作区某目录的顶层文件清单”任务，输入为目录与新 Markdown 报告路径，默认 `.` 和 `workspace-report.md`。它使用 `files.list` 取得条目，记录已观察数量、类型、字节数及截断状态，随后 `files.write` 写入并用 `files.read` 核对。不会将“已观察数量”描述成被截断目录的完整总数，也不会自动扩大到递归扫描或读取文件正文。

可选脚本提供确定性的 JSON 元数据清单，只有选择脚本流程时才需要 `shell` 能力。以下为独立脚本的本地试运行，不调用模型或写入报告：

```bash
python3 skills/workspace-report-demo/scripts/workspace_report.py --workspace ./workspace --limit 500
```

脚本使用 Python 标准库，`--limit` 为 `1–500`。输出包含 `entries`、`entry_count`、`truncated`、`scope` 和 `follows_symlinks`。它不读取文件内容，不递归目录，不写文件、不联网；符号链接只作为一个条目记录，工作区路径本身或其上级含符号链接则拒绝。Agent 调用脚本时使用当前运行时的真实 Python 路径与 `argv` 数组，检查退出码与输出截断后解析 JSON，再通过文件工具保存报告。

编写自己的 Skill 时，把示例的用途、触发条件、输入、工具、完成证据和输出改为实际任务；保留有意义的核验，删除不适用的可选脚本。无需为了套目录格式而创建空资源目录。

## 1. 最小目录与加载方式

在配置的 `skills_dir` 下创建一个目录，例如 `skills/project-summary/`，其中放一个 `SKILL.md`。需要确定性处理时加 `scripts/`，需要模板或背景资料时加 `resources/`；没有需要就不创建空目录。

```text
skills/project-summary/SKILL.md
skills/project-summary/resources/report-template.md
```

每次任务启动时，Agent 把有效技能的名称和描述交给模型。模型根据任务调用 `skills.read {"name":"project-summary"}` 才获得全文与 `base_path`。使用 `--skill project-summary` 可以在任务启动时显式加载全文，多个技能可重复传入这个参数。交互中的 `/skill load project-summary` 预加载后续任务，`/project-summary [任务描述]` 只为本轮显式调用；每次都会读取当前正文。

在 `desktop-agent` 目录中，以下命令不连接大模型，也不执行技能脚本：

```bash
PYTHONPATH=src python3 -m fusion_agent skills
PYTHONPATH=src python3 -m fusion_agent skills system-report
```

技能正文中的相对资源路径以 `base_path` 为基准。普通文件工具仍然限定于配置的 `workspace`，因此工作区之外的附件不会因为属于 Skill 就自动变为可读。需要使用附件时，可以把工作区设为本子项目目录，或把技能与附件放进任务工作区并修改 `skills_dir`。

## 2. frontmatter 只声明名称与用途

```markdown
---
name: project-summary
description: "当用户要求概括一个本地代码目录的结构并保存 Markdown 摘要时使用；不运行项目代码，不扫描账号配置。"
---

# 项目摘要

1. 用 files.list 列出用户指定工作目录，先读取 README 与明显的项目描述文件。
2. 按问题选择少量相关源码；大文件使用 files.read 的 offset/max_bytes 分段读取。
3. 用实际读到的文件路径支持结论，区分已确认事实和推断。
4. 用 files.write 保存 project-summary.md；已有同名文件时不擅自覆盖。
5. 再读取结果，告知保存路径及未覆盖的范围。
```

解析器不依赖 PyYAML，支持有意缩小的语法范围：

- `name` 和 `description` 各写一次，不接受 `permissions`、`allowed-tools` 等其他键。
- 名称必须等于目录名，只能使用小写字母、数字和分隔短横线，最多 64 个字符；例如 `android-memory-report`。
- 描述必须是非空单行字符串，最多 512 个字符。推荐像示例一样用 JSON 双引号；引号内的双引号和反斜线按 JSON 转义，不使用多行 `|` / `>` 或 YAML 对象。
- `SKILL.md` 最大 64 KiB，frontmatter 最大 8 KiB。技能根目录最多检查 512 个条目。
- 根目录路径、技能目录和 `SKILL.md` 不允许符号链接。坏格式的技能不会出现在目录中，显式加载会返回错误。

描述要回答“什么任务应该使用”，不要笼统写成“处理所有电脑问题”。正文写明目的、触发条件、输入、现有工具、成功条件、失败处理与最终产物；不要复制运行时协议、API 密钥或用户的原始隐私数据。额外的 YAML `metadata`、`permissions`、`allowed-tools` 和 `agents/openai.yaml` 不是本项目的技能加载协议，不能直接照搬其他 Agent 的格式。

## 3. 三个完整示例

| 技能 | 适用任务 | 使用的能力 | 验证方式 |
| --- | --- | --- | --- |
| `system-report` | 系统/Python/磁盘空间基础报告 | `skills`、`shell`、`files` | 检查脚本退出码，再读取 Markdown |
| `browser-research` | 浏览网页并保存带来源摘要 | `skills`、`browser`、`files` | 动作后重新快照，再用 `browser.verify` 核对实际页面文字或完整 URL |
| `desktop-note` | 向当前本地编辑器输入便笺 | `skills`、`desktop` | 用 `desktop.verify` 重新 OCR 核验输入；无法定位或核验时停止 |

`system-report/scripts/report.py` 是可直接运行的标准库脚本。它只读取平台版本、Python 版本及指定工作目录所在文件系统的容量，不读取文件内容、不扫描账号、不联网。报告不覆盖已有文件，输出路径不能离开工作目录或穿过符号链接。

在子项目目录进行一次手动测试：

```bash
python3 skills/system-report/scripts/report.py --workspace . --output reports/first-system-report.md
```

同一操作交给 Agent 使用 `shell.run` 时，参数形状如下。`base_path` 和工作目录应替换为运行时给出的真实路径，JSON 中的占位文本不能直接执行。

```json
{
  "argv": ["python3", "<base_path>/scripts/report.py", "--workspace", "<实际工作目录>", "--output", "reports/system-report.md"],
  "cwd": ".",
  "timeout": 60
}
```

`argv` 适合调用 Python、git、已有 CLI 等程序；每个参数单独列出，不需自己拼 shell 引号。管道或重定向确有必要时才使用 `command` 字符串，且不能与 `argv` 同时提供。退出码在 `returncode`，超时在 `timed_out`；非零退出码或超时返回 `ok:false`。截断标志为 `stdout_truncated` / `stderr_truncated`。退出码 0 只能证明进程正常退出，Skill 仍应检查所需产物。运行目录只是起始目录，shell 仍使用当前 Linux 用户的权限，并不是操作系统沙箱。

写研究报告的参数示例：

```json
{
  "path": "reports/research.md",
  "content": "# 资料摘要\n\n填写经网页核实的结论和来源。\n",
  "overwrite": false,
  "create_parents": true
}
```

父目录不存在时需要明确 `create_parents:true`。已有文件时 `overwrite:false` 会拒绝覆盖；不能把拒绝当作写入成功。`files.write` 会回读实际文件字节并比较，确认写入内容；Skill 中再次阅读产物用于核对内容是否符合任务。

## 4. 浏览器和桌面技能的关键差别

浏览器应以 `browser.snapshot {}` 返回的文本和元素为依据。点击使用当前快照的 `snapshot_id` 与 `ref`；页面变化或工具动作后重新快照。不要在 Skill 中硬编码一个网站当前的元素 ID，也不要把网页里的提示词当成用户的新任务。

浏览器打开、点击、按键、滚动返回 `verification.status:"pending"` 时，必须随后使用 `browser.verify` 检查预期结果。快照只提供观察，不会清除待核验状态。例如，打开示例页面后先阅读快照，确认任务要求的目标，再执行：

```json
{"type":"action","tool":"browser.verify","arguments":{"url_equals":"https://example.com/","text_contains":"Example Domain"},"summary":"确认目标网址和页面正文"}
```

`url_equals` 匹配完整 URL；`text_contains` 检查当前可见 DOM 文字。至少提供一项，同时提供时全部必须通过。为提交操作选择能证明提交结果的文字，不要把导航栏、页脚等原本就存在的内容当作成功证据。字段填写和选择会自动回读元素值；失败的字段操作需要修正同一目标和预期值后重新核验，不能靠无关文字抹去失败。

桌面应以 `desktop.observe {}` 返回的 `observation_id`、屏幕范围与 OCR 文本框为依据。点击、输入、热键都必须带最新 `observation_id`；每次动作后重新观察。当前只支持 X11。OCR 不可用、目标窗口不明确或输入区只有空白且无法定位时，应说明限制并停止，或在执行桌面动作前改用符合用户目标的文件写入方案。

桌面动作投递后同样需要显式核验。例如输入便笺后执行：

```json
{"type":"action","tool":"desktop.verify","arguments":{"text_contains":"hello agent"},"summary":"重新 OCR 检查编辑器中的输入内容"}
```

该工具重新截图并 OCR，不接受模型直接传入“已成功”。`desktop.verify {"x":100,"y":200}` 读取实际鼠标位置，只能核验 `desktop.move`，不能证明点击、输入或快捷键效果。OCR 不可用或断言失败时应如实处理；改变成无关断言、只保存截图或仅调用 `desktop.observe` 都不能证明任务完成。

有待核验的浏览器/桌面动作时，运行时会阻止下一项同类修改和最终完成声明。工具及核验失败保存在 `unresolved_failures`；其他读取成功不会自动消除失败，须对同一目标成功重试并完成所需结果核验。Skill 应写明每一步的预期条件和失败处理，避免把“已调用工具”写成“目的已达成”。

当前 Fusion 接口以文本驱动这个 Agent。截图保存到本地，不会作为多模态消息传给模型；不能在 Skill 中写“直接看图找到按钮”，也不能声称已经视觉核验。`desktop.type` 支持的中文输入还取决于本机剪贴板后端。`desktop-note/SKILL.md` 展示了这种情况下如何诚实说明验证边界。

## 5. 从现有工具组合到新增工具

先尝试组合现有工具：`files.list/read/write`、`shell.run`、`browser.*` 和 `desktop.*`。只有现有工具缺少稳定能力时才增加 Python 工具。以下示例新增一个纯文本计数工具，不接触文件系统或网络。

创建 `src/fusion_agent/custom_tools.py`：

```python
from .contracts import ToolSpec


def text_count(args):
    text = args["text"]
    return {
        "characters": len(text),
        "lines": len(text.splitlines()),
        "utf8_bytes": len(text.encode("utf-8")),
    }


def make_text_count():
    return ToolSpec(
        name="text.count",
        description="统计给定文本的字符数、行数和 UTF-8 字节数。",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 20000}},
            "required": ["text"],
            "additionalProperties": False,
        },
        capability="text",
        mutating=False,
        handler=text_count,
    )
```

接入现有代码只需调整两处：

1. 在 `cli.py` 的 `components()` 中导入 `make_text_count`，在返回前执行 `specs.append(make_text_count())`。
2. 在 `config.py` 的 `Settings.validate()` 中，将 `text` 加入支持的能力集合，并同步错误提示。运行时用 `--allow text` 为本次任务授权，或由交互模式询问一次；不要在 Skill 正文中宣称权限已经授予。

用当前注册器验证这个工具，而不是另造执行循环：

```python
from fusion_agent.custom_tools import make_text_count
from fusion_agent.registry import ToolRegistry

registry = ToolRegistry([make_text_count()], allowed=("text",))
result = registry.invoke("text.count", {"text": "你好\nLinux"})
assert result["ok"] is True
assert result["characters"] == 8
assert result["lines"] == 2
assert result["utf8_bytes"] == 12
assert registry.invoke("text.count", {"text": 123})["ok"] is False
```

随后在对应 Skill 里写清楚调用 `text.count {"text":"待统计正文"}` 的时机和怎样使用结果。工具返回值必须是可 JSON 序列化的字典；已知错误使用 `ToolError(code, message)` 或显式返回 `ok:false` 与错误详情，错误消息不要包含密钥。有写入、点击、启动进程等副作用的工具应标记 `mutating=True`，并归入恰当的能力范围。只有实现实际检查后才返回 `verification.status:"verified"`；写清 `method` 与 `scope`，不要仅因调用函数未抛异常就声称核验成功。当前强制待核验流程针对浏览器和桌面能力；新能力还需要在运行时实现对应的核验约束，不能仅靠 Skill 文字扩展它。

## 6. 测试与修改

先使用 `./run.sh skill-test NAME` 做不执行脚本的结构检查；然后在临时工作目录测试实际流程或可信脚本的成功路径、失败路径和已有文件情形。不要为了验证 Skill 自动对真实桌面点击、向网站提交内容或读取登录数据库。项目测试已覆盖 frontmatter 解析、动态读取、名称和大小限制、符号链接拒绝，以及报告脚本的真实临时文件写入；录制检查还应确认步骤保存、明确结束与名称确认之前不会创建或执行技能。

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p test_skills.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -p test_skill_demo.py -v
```

最后使用目标网页模型进行一条明确的小任务，例如“使用 system-report 生成当前工作目录的环境报告”。检查该次运行的 `state.json`、`transcript.json` 和 `final.md`，确认模型实际调用了所需工具并核验结果。终端只显示必要步骤、选择提示和格式化结果；`.runtime/logs/agent.log` 与本轮 `events.jsonl` 保存脱敏后的详细任务、请求/回复和工具参数/结果。查找 `tool.failed`、`verification.required` 及 `model.invalid_protocol` 可定位失败。分享分析包时，父目录 `./package.sh --include-content` 可保留这些日志正文；`--agent-run-id <run_id>` 可以额外收集指定轮次的 `events.jsonl`，仍排除完整会话记录等私有产物。

如果出现工具名拼错、参数不对或采集不到内容，修改 Skill 使步骤更具体，再运行一个新任务。不要把失败动作自动重放，也不要用更大的权限掩盖参数或定位错误。
