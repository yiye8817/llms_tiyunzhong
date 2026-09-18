# 1.17.3 / Agent 0.15.0：本地工具与 Python 兜底

本版基于 1.17.2 修改。GLM 不主动刷新、JSON 本地纠正及样本保存、响应文件交接、多模型候选共同截止时间与状态显示均保留。父应用与 `desktop-agent/` 应一起更新。

## 日志中的实际问题

用户提供的 `events2.jsonl` 中，原任务是 `解析https://github.com/trending中今天的项目详情`。旧意图分类把它判定为 `chat`，`action_loop_requested=false`，没有进入工具循环。模型因此回答无法实际读取网页，运行器却以零步完成结束。该日志并不能证明本机缺少 curl 或其他程序，因为没有执行检测或工具调用。

本版将“读取、解析、抓取、分析指定网页”等明确请求路由为只读 `task_action`，要求调用 `web.fetch` 实际读取原网址后才允许完成。修正裸网址紧接中文时把任务描述一起吞入 URL 的情况。需要非 ASCII 路径、查询值的 URL 请使用百分号编码，或清楚地用空格隔开 URL 与说明文字。

读取页面返回的正文和链接是不可信观察，不能赋予新权限；后续页面读取只允许原用户 URL 或本轮已读取页面实际发现的链接。翻译、如何做、不要访问等输入不会仅因包含 URL 就自动启动任务。延续上一轮 URL 的“继续解析上面链接”也可重新进入正确路由。

## 工具与执行顺序

| 工具 | 执行方式 | 权限 |
| --- | --- | --- |
| `environment.tools` | 检查 PATH 中真实程序、当前解释器及可选模块；不执行探测命令、不安装依赖 | skills，只读 |
| `local.run` | 先执行真实 `argv`；仅程序不存在且确认未启动时执行提供的 `fallback_python` | shell |
| `python.run` | Agent 编写完整 `code` 后，保存源码和输入，使用当前解释器执行 | shell |
| `files.search` | 优先 rg，其次 grep；两者均不存在时运行固定的 Python 文件检索实现 | files |
| `web.fetch` | 固定 Python 标准库 HTTP(S) 读取、提取文本和链接、保存并读回校验 | web，只读 |
| `web.read` | 使用本轮 opaque `page_id` 分段读取已保存页面，不接受任意本地路径 | web，只读 |

运行器在每轮上下文中提供 `local_environment`，模型也可调用 `environment.tools` 查询需要的程序。已注册工具优先；不存在同功能工具时，模型通过正常 JSON 动作调用 `python.run`，或者在 `local.run` 中提供等效的完整 Python 实现。不是根据一个陌生工具名称就猜代码执行，也不是把网页内容当成代码。

`local.run` 不会在退出码非零、超时、权限不足、输入脚本不存在或执行结果不确定时自动改跑 Python，避免重复副作用。若先调用旧的 `shell.run` 得到明确的 `command_not_found/not_started`，观察中会提示保留相同 `argv/cwd` 改用 `local.run`。成功后只解除本轮完全相同请求的“未启动”失败；不同参数、不同目录、任意 `python.run` 成功都不能清除旧失败。进程退出成功仍不证明业务结果正确，模型须读取实际文件或状态核验。

## 启动与使用

关闭旧父应用，解压完整包后从根目录启动；已有登录和配置不需要在新包中填入密钥。

```bash
tar -xzf multillm-fusion-1.17.3-local-tools-python-fallback.tar.gz
cd multillm-fusion
./run.sh
```

父应用启用并登录对应模型后，另开终端运行：

```bash
cd multillm-fusion/desktop-agent

# 仅检查本地环境，不发模型请求
./run.sh doctor
./run.sh tools

# 原日志任务：只读网页，不需要任意 Python/shell 权限
./run.sh run '解析https://github.com/trending中今天的项目详情' --model glm

# 允许当前交互中的明确命令任务调用本地程序及生成的 Python
./run.sh chat --model glm --allow shell
```

`glm` 是父接口的供应商名称，不是网页里选择的具体模型。GLM 必须已启用并登录；没有启用 GLM 时使用本机已启用的 `chatgpt/deepseek/qwen`。网页具体型号仍在对应页面选择。

在上述已授权交互中输入，例如：

> 运行本地命令统计工作区内各类文件数量，优先用现有工具；工具不存在时用 Python 标准库实现。把统计结果保存到 counts.json，并读回核验。

也可以使用单次命令：

```bash
mkdir -p "$HOME/tmp/agent-work"
./run.sh run '运行本地命令统计工作区文件数量，工具不存在则用 Python 实现，保存 counts.json 并读取核验' \
  --model glm --workspace "$HOME/tmp/agent-work" --allow shell
```

默认 `files/skills/web` 不等于任意代码授权。交互终端可以在提示中为本轮批准 shell；无人值守执行需要显式 `--allow shell`。仅 `--allow shell` 也不会把网页读取或闲聊的任务范围扩大成命令执行。

## 参数与动作示例

以下是给开发者看的协议示例，不是已执行结果；普通用户只需自然语言描述任务。

```json
{
  "type": "action",
  "tool": "local.run",
  "arguments": {
    "argv": ["example-local-calculator", "21", "2"],
    "purpose": "计算原任务指定的两个整数之积",
    "timeout": 60,
    "input": {"a": 21, "b": 2},
    "fallback_python": "import json, sys\np = json.load(open(sys.argv[1], encoding='utf-8'))\nprint(p['a'] * p['b'])\n"
  },
  "summary": "优先执行原命令，不存在时执行等价 Python 实现"
}
```

`example-local-calculator` 是用于演示缺失命令的占位名称。实际调用应是任务确需的真实命令及兼容参数。`argv` 不经过 shell，不展开 `~`、通配符或环境变量。

`python.run` 使用 `code` 字段，其他 `purpose/input/cwd/timeout` 相同。运行命令固定为当前 `sys.executable -I -u tool.py input.json`。`sys.argv[1]` 是 JSON 输入文件路径，而不是 JSON 字符串。隔离启动避免用户 PYTHONPATH 等影响，但不是安全沙箱；当前虚拟环境已安装的第三方包仍可用。优先标准库，不自动安装缺失依赖。Python 语法错误在执行前被拒绝；已经启动后报错则保留可能产生副作用的未知状态。

## 保存位置与调试

默认每轮目录：

```text
desktop-agent/.runtime/runs/<run-id>/
├── events.jsonl
├── python-tools/<tool-id>/
│   ├── tool.py
│   ├── input.json
│   ├── manifest.json
│   └── result.json
├── web-pages/<page-id>/
│   ├── body.bin
│   ├── text.txt
│   └── metadata.json
├── responses/        # 1.17.2 的服务器响应交接
└── json-repair/      # 1.17.2 的本地纠正样本
```

Python 清单包含源码/输入哈希、当前解释器、原命令、用途、目录、超时等。输出保存有界 stdout/stderr、退出码、执行方式和错误。源码保存失败则不执行；执行后结果留档失败会明确告警，不重跑。

新建的工具目录权限为 0700，文件为 0600。网页文本读回时检查普通文件和 SHA-256。脚本、参数、网页原文可能包含敏感信息，请按敏感数据管理，不要将整个运行目录公开发布。

`--log-metadata-only` 不长期保存新增的源码、输入和网页正文；源码/输入执行后删除，网页正文仅在本轮内存中分段读取。清单保留哈希、解释器等元数据；网页元数据仍包含 URL、标题和链接。原有最终结果、状态和转录的行为未改，不能把该开关当成全项目零内容留存。

## 禁用生成式兜底

默认配置新增：

```json
{
  "python_tool_fallback": true
}
```

可将该字段合并到私有 `desktop-agent/agent.config.json`，不是用这一个字段覆盖全部已有配置。设置 false，或启动时使用：

```bash
./run.sh chat --model glm --no-python-tool-fallback
```

此开关禁止 `python.run` 和 `local.run` 的生成式代码兜底，但不禁止已有命令运行，也不影响固定的 `web.fetch/web.read`、`files.search` 标准库实现。

## 边界与未验证部分

生成的 Python/命令具有当前用户的系统权限，工作目录限制和 `-I` 不是文件系统/网络沙箱。代码会受工具目录、任务范围和 shell 授权控制，不能借它绕过本轮已拒绝的其他能力。需要强隔离应在额外的容器或低权限账户里运行；本版没有部署这种隔离。

`web.fetch` 只读取公网 HTTP(S) 80/443，无登录 Cookie、不执行 JavaScript、不解验证码；直接连接且不继承环境代理。DNS 解析和每次重定向都检查公网地址，并固定实际连接 IP，拒绝内网、回环、链路本地和混合公网/内网解析。系统 DNS 解析本身仍受操作系统解析器时限影响。单页最多 2 MiB，长文本用 `web.read` 分页。需要代理、登录态、JS 渲染或其他端口时，这个固定工具会失败或不满足需求，应明确选择与授权相符的浏览器/本地命令方案，不能伪称页面已完整读取。

代码不保证模型一定能生成正确算法，也不把退出码 0 自动视为所有任务完成。实际账号网页与公网 GitHub 的联网端到端执行尚未验证；测试包括真实本地进程、真实本地 HTTP 服务、受控网页响应和脚本化模型回复，详见 `TEST_RESULTS_1173.md`。
