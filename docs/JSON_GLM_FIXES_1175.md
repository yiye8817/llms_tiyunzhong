# MultiLLM Fusion 1.17.5 / Agent 0.16.1

## 1. 附件事件与版本核对

本次输入为 events3.jsonl。115 个外层 JSONL 记录可以读取，问题在 payload 内的模型动作 JSON；重组日志分片并去重后得到两个实际回复：合法的 files.read，以及含未转义 Python 双引号的 python.run。

异常动作在第 1 行第 99 列报 `Expecting ',' delimiter`。例如 `arguments.code` 内的 `file_path = "..."`、`open(..., "r", encoding="utf-8")` 和 f-string 的双引号没有作为外层 JSON 字符串的一部分转义。不能把这一错误说成 Python 已经执行并出错；日志显示动作没有执行。

此前有两个不同内容、相同版本号的 1.17.4 包：

- `multillm-fusion-1.17.4-json-unified-tools-config.tar.gz`：附件运行路径对应此包。对本次样本原样回放，代码引号修复失败。
- `multillm-fusion-1.17.4-json-tools-config.tar.gz`：后交付包已可处理本次简单引号模式，但缺少代码边界编译校验、GLM 首次会话绑定修复及旧配置别名兼容。

1.17.5 统一两个分支的相关能力，升级时应同时替换父应用和 desktop-agent，关闭仍在运行的旧进程，不能只更新一个同名目录。

## 2. 本地 JSON 修复

严格合法 JSON 优先原样解析。只有严格解析失败、明确位于 `python.run.arguments.code` 或 `local.run.arguments.fallback_python` 的内部引号才进入代码传输纠正。保留反斜杠、Unicode、换行、路径及代码含义，只补外层 JSON 所需的反斜杠。

Python 的字典、集合、列表、三引号及 f-string 也可能包含类似 JSON 字段边界的引号。对候选边界调用 compile(..., 'exec')，只生成后丢弃代码对象，不执行代码、不导入源码要求的模块、不请求服务器。编译通过不表示脚本安全或任务成功；真实执行仍走 schema、能力授权及原执行器。

边界检查限制为最多 64 次候选编译、每次最多 64 KiB 字符，引用修复最多 2048 处，保留结构深度及节点数量限制。不能确定、截断、重复键、多对象、未知工具、shell 命令歧义、Python 本身有语法错误时，不猜测补齐命令或语句。严格合法 JSON 中的 Python 语法错误仍交实际工具正常报告，不自动改写源码。

新的归档算法标识为 `python_deterministic_v4`，代码引号记录为 `unescaped_python_quotes`。本次实际附件：两份回复均解析通过，第二份修复 18 处引号；未调用服务器、未执行日志动作。解码源码 SHA-256 为 `55e02471d3385a2183035caa9bf8307498a3dfd18161a4531306c3559638c809`。

默认问题样本目录：

```text
desktop-agent/.runtime/runs/<run-id>/json-repair/<sample-id>/
  original.invalid.json   原文，不更改
  normalized.json         通过本地修复/校验后的 JSON
  decoded-tool.py         解码后的诊断源码；不是执行记录
  report.json             位置、修复类型、源码哈希、executed=false
```

启用正文归档时新增 decoded-tool.py，权限为 0600；metadata-only 不保存诊断源码。实际 Python 执行仍使用原来的 python-tools/tool.py 及 result.json。回放样本可能包含原始私人路径和聊天内容，应按私人数据保管。

离线复现（输出目录须不存在）：

```bash
./desktop-agent/run.sh repair-json /绝对路径/events3.jsonl \
  --events --output-dir ./events3-replay
```

模型系统提示新增 Python 数据通过 `arguments.input` 传入、代码使用 `sys.argv[1]` 读取的合法 JSON 编码示例；提示词不是服务器强制解码保证，本地校验不省略。网页回复提取对完整但语法错误的 Agent 原文采用 `json_raw_unvalidated`，避免 Markdown 转换再次污染引号或反斜杠。这只保留原文，不授权执行，不拼接缺失回答。

## 3. GLM 发出即报错

附件 parent.checked 记录了 GLM 缓存状态 `网页已切换会话或出现其他用户输入`，但未附完整 Electron 当轮 DOM / 网络日志。因此不能据此断定远端 GLM 服务故障，也不能宣称已经用该账号验证修复。

通过生产 pageAction 代码在真实 Chromium DOM 中复现了两条本地错误判断路径：

1. 网页先在起始地址显示当前用户消息，再异步更新为首次会话 URL。旧代码提前绑定起始地址，正常首次 URL 更新被当作换会话，适配器抛错并停止采集。
2. 多段输入渲染成 p/br 节点，用户消息还带复制按钮时，textContent 丢失换行、混入控件文字，造成错误的用户消息不匹配。

修复后，空白新会话只保留一个同源首次路由候选；与本轮精确用户消息匹配后绑定真正会话，不提前绑定起始页。经过已验证的新建会话操作、旧消息已清空但旧路由尚未更新，也支持此首次绑定。用户消息签名保留段落及 br 换行，并排除复制等按钮。

不会因此允许任意换会话。第二次路由变化、其他用户输入、旧消息历史变化、不同草稿、跨域跳转继续停止采集；不会刷新页面或自动重发来掩盖错误。关闭网页错误重试也不关闭会话隔离检查。现有 `glm-5.3-flash` 选择保留、多模型等待、生成超时、真实网络错误及人工验证码流程不变。

新增 `adapter.context_check` 仅在诊断状态变化时记录，含 reason、initialConversation、boundConversation、awaitingFirstConversation、用户数量与匹配布尔值，不新增完整提示正文。错误中会显示 `recovery_conversation_changed`、`recovery_user_changed` 等具体原因，方便与 `adapter.network` / `adapter.complete` 一起定位。旧 provider 状态可能继续显示缓存错误；首次新版成功请求后刷新状态，不应将旧状态当作新版请求结果。

## 4. 配置兼容

兼容较早 1.17.4 的 `file_access=unrestricted/workspace` → `filesystem_scope=host/workspace`，`persist_settings` → `auto_save_config`。旧 `strict_json_protocol` 字段可读取，本地严格解析始终启用。新旧同义字段冲突会要求明确修正，不静默扩大权限。

加载时不写文件；后续已有保存操作沿用锁、字段合并、原子替换和 0600 权限，写回同一配置路径。模型、工作目录、超时、授权列表等无关设置保持。绝对路径仍按原配置保留，迁移安装目录后需自行确认工作目录、runtime_dir、skills_dir 和密钥文件路径是否指向预期位置。

## 5. 启动 / 验收

建议解压到新目录，先关闭旧父应用和 Agent。可将自己的 agent.config.json 复制到新 desktop-agent 目录，不能用示例配置覆盖个人配置。父应用账号数据目录不在交付源码中。

```bash
mkdir -p fusion-1175
tar -xzf multillm-fusion-1.17.5-json-glm-fix.tar.gz -C fusion-1175
cd fusion-1175/multillm-fusion
./desktop-agent/run.sh --version  # 应为 0.16.1
./desktop-agent/run.sh doctor     # 检查 agent_source 和 python_deterministic_v4
./run.sh
```

父应用启动后，在设置中确认 GLM 已启用，打开已登录的 GLM 页面，选择所需模型。先在对话模式只选 GLM 发送一条短问题，再验证 GLM 加另一模型的双模型等待与整合。发生真正会话切换或验证码时先处理页面状态，不要重复快速发送。

本版变更清单见 MODIFIED_FILES_1175.txt；测试与限制见 TEST_RESULTS_1175.md。完整 JSDOM / Electron / 真实 GLM 账号链路尚未验证；这里的 Chromium 场景是本地 DOM 及模拟 URL 状态，不是联网网站测试。
