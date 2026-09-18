# MultiLLM Fusion 1.17.9 / Agent 0.17.0

## 1. runtime2：所有模型的 JSON 在本地修复

运行器统一使用 `python_deterministic_v5`。不按 qwen/glm/chatgpt/deepseek/kimi 区分修复逻辑；切换模型不会切换到服务器 JSON 重写。

附件中的错误包含两个不同层次：`arguments.programs` 和 `plan` 的数组括号被写成 `\[`、`\]`；工具名被 Markdown 化，例如 `"tool":"[environment.tools](https://environment.tools/)"`。旧版能处理数组，但对链接化工具名进入了再次请求模型的协议修复分支。本版恢复严格完整自链接的显示工具名，并对每一条解码失败本地停止，不再将 JSON 格式纠正请求发送给模型。

仅当链接标签逐字等于 URL 主机、无账号、端口、查询串、fragment 和额外路径时才接受；不会访问 URL，不做模糊工具匹配。工具存在性、参数 schema、能力授权仍在解析后校验。完整、语法正确但选择了未知工具的动作，可以请求模型重新选择工具，事件名是 `model.action_replan_requested`、请求类型是 `tool_resolution`，这不是 JSON 编码纠错，也不会执行缺失工具。

之前的裸换行、Markdown 转义、文本/Python 代码字段引号修复、重复键拒绝、截断检测及文件交接保留。无法安全确定原意时保存原文并停止，不能把残片补成假动作。

默认存档：

```text
desktop-agent/.runtime/runs/<run-id>/json-repair/<sample>/
  original.invalid.json
  normalized.json             # 成功时
  report.json
  decoded-tool.py              # 含 Python 代码且适用时；不是执行记录
```

离线回放，不请求模型、不执行工具：

```bash
./desktop-agent/run.sh repair-json /absolute/path/events.jsonl \
  --events --output-dir ./new-replay-directory
```

实际附件：3 份问题正文（2 份独立内容）均通过；全部已保存回复去重后 3 份，均通过。私人回放包单独交付，不放入源码包。

## 2. 内置技能与本地技能：全部模型共用

`AgentSkillLibrary` 合并程序内置 `desktop-agent/skills` 与配置项 `skills_dir` 指定的用户目录。切换工作目录、模型或用户技能目录不会使内置技能消失。目录仅展示有效 SKILL.md 技能，不把普通工具函数冒充技能，不执行技能脚本。

```bash
# 项目根目录；不需要启动模型或服务器
./desktop-agent/run.sh skill
./desktop-agent/run.sh skill list
./desktop-agent/run.sh skill read system-report
./desktop-agent/run.sh skills

# 指定其他配置
./desktop-agent/run.sh skill list --config /path/to/agent.config.json
```

Agent 交互内：

```text
skill
skill list
skill read system-report
/skill
/skill list
/skill read builtin:system-report
/skill load builtin:system-report
/skill run builtin:system-report 检查当前系统环境并报告真实结果
/skill unload builtin:system-report
```

当前包附带 browser-research、desktop-note、system-report 三个内置技能。返回 `source`、`id`、`base_path` 和名称；本地同名技能默认优先，但内置版本仍列为 `builtin:名称`，也支持 `local:名称`。读取和静态检查始终使用实际资源目录。已有 Skill 创建、录制、校验命令保持可用。

所有已启用的网页模型经 Desktop Agent 使用 `skills.list` / `skills.read`；不需要网站本身支持原生函数调用。运行器将技能作为方法参考，实际文件、shell、浏览器工具仍需原有能力授权。普通 Fusion 合并聊天不会被暗中改成有系统执行权限的 Agent。

## 3. Qwen 分级恢复

第 1 级：识别属于当前失败回合的重试控件，必要时滚动后重新检查，调用其页面 click 处理逻辑。不重填提示、不重放原始 HTTP 请求、不刷新页面。

第 2 级：第 1 级没有产生新的生成请求或再次失败时，获取该 BrowserView 的 `webContents.capturePage()` 截图。使用当前错误区域的 DOM 控件/文字与已学习的局部图像特征共同定位，经截图非空、唯一候选、同回合、遮挡和坐标复核后，通过 CDP 原生输入模拟点击。该层不是全页面任意图标猜测器：未知、模糊、多个候选、截图不可用、canvas 等无法确认的目标进入人工处理。无 OCR、无额外视觉模型 API 或收费依赖。

第 3 级：前两级无法恢复时，显示“处理网页重试”，默认等待 **20 秒用户操作**，逐秒记录剩余时间。点击原 Qwen 会话里的重试后，只要观察到新请求，就继续等待完整回答；不要求回答在 20 秒内生成完。没有操作、新请求未出现或再次失败则明确结束。所有阶段仍受当前任务总时限约束：剩余时间小于 20 秒时显示实际剩余时间，不突破多模型候选默认 60 秒总时限。

每个任务的自动页面触发、自动原生点击各最多一次。用户或站点已经重新生成时不补点；点击回执不确定时不继续自动重复。进入恢复前保留网络计数，避免用户在切换页面/等待租约时已重试，却因为重新取基准而被漏掉。仍在生成的原请求可以继续采集，不必重发。

上下文比较对 Qwen 的显示性 model/lang/utm 等查询参数做规范化；真正的会话 ID 变化、新用户输入、模态框、验证码等仍阻止自动点击。空白已确认会话允许本轮用户消息对应的首次会话地址生成。不取消会话隔离。

## 4. 人工点击学习和隐私

仅在短暂人工等待窗口，对当前失败回合捕获可信浏览器点击。记录目标 tag/id/testid/aria/title/role/class/label、点击坐标、控件矩形、来源与时间；不捕获输入框内容或任意其他页面点击。

只有“可信点击 → 新生成请求 → 本轮完整回答”链路成功才升级为学习记录。失败点击不会成为下次自动规则。图像特征只在点击控件与预截图中的目标、矩形和视口一致时保存；无法关联时只保存已确认的控件描述。下次仍需同源、同一错误回合、唯一可见目标和原生点击前复核，不按旧绝对坐标盲点。

```text
logs/retry/qwen/<timestamp-job-hash>/
  attempts.jsonl
  02-visual.png                 # 第 2 级且截图成功时
  03-before-manual.png          # 人工接管且截图成功时

~/.local/share/multillm-fusion/retry-learning/qwen.json
```

尊重 `FUSION_LOG_DIR` / `FUSION_DATA_DIR`；也可用 `FUSION_RETRY_DIR` / `FUSION_RETRY_LEARNING_DIR` 显式设置。文件权限 0600，新建目录 0700；写入失败会记录诊断，不伪称已保存。日志中的每级开始、跳过、目标、截图、点击、请求确认、HTTP/网络错误、人工倒计时、人工点击、学习保存及清理均可回放排障。

截图可能含完整会话内容，请按敏感数据保管。父应用启动时使用 `FUSION_LOG_CONTENT=0 ./run.sh` 可不保存 PNG，但仍有过程元数据；关闭 `qwen_retry_learning` 可不保存/复用学习记录。删除上述 qwen.json 可清除学习结果，无需删除账号登录数据。

## 5. 可保存设置

位置：设置 → 等待与完成判断 → Qwen 分级重试。

```json
{
  "generation": {
    "qwen_retry_stages": true,
    "qwen_retry_screenshot": true,
    "qwen_retry_learning": true,
    "qwen_retry_trigger_wait_seconds": 3,
    "qwen_manual_retry_wait_seconds": 20
  }
}
```

自动触发观察范围 0.5–15 秒，人工操作范围 1–120 秒。默认无需改配置。`qwen_retry_stages=false` 使用旧单次重试策略；`recovery_timeout_seconds=0` 完全关闭网页异常恢复。所有旧的代理、模型、登录数据、输入分段、GLM 不主动刷新和多模型完整回答后合并设置保留。

## 6. 更新与诊断

```bash
tar -xzf multillm-fusion-1.17.9-local-json-skills-qwen-retry.tar.gz
cd multillm-fusion
node -p 'require("./package.json").version'  # 1.17.9
./desktop-agent/run.sh --version            # 0.17.0
./desktop-agent/run.sh doctor               # python_deterministic_v5，检查实际代码路径
./run.sh
```

必须退出旧父应用和 Agent，再启动新目录。已有配置可以迁移，核对其中的绝对路径；无需清除登录数据。新版同时涉及父应用和 Agent，只更新其中一个不足以覆盖本次功能。

附件某次 HTTP 502 是父服务将 `recovery_context_changed / recovery_conversation_changed` 包装为 `candidate_generation_failed`，不是足以确认 Qwen 上游返回 502 的证据。新版保留网络归因与细化上下文日志。真实 Qwen 502 或网络环境本身不会被本地代码“修好”；重试仍可能失败，程序必须报告事实。测试使用生产代码、真实本地进程/HTTP/Chromium，但不含登录 Qwen/GLM 的线上端到端验证。
