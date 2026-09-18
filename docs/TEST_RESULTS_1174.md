# 1.17.4 / Agent 0.16.0 实测记录

环境：Linux 容器，Python 3.13.5、Node.js v22.16.0。只使用本地测试服务、受控模型回复和临时文件；没有登录真实 GLM 账号或执行附件中的用户任务。

## 最终执行结果

| 检查 | 结果 |
| --- | --- |
| `test_upgrade1174` 新增专项 | **48 / 48 通过**，包含在 Agent 全集中 |
| Agent 全集 | **739 / 740 通过**；1 项原包同样失败，见下 |
| 父应用 Python 全集 | **126 / 126 通过** |
| 选定 Node 测试 | **127 / 127 通过**，没有 skip |
| `npm run check` | 通过 |
| Python compileall、启动脚本 bash -n | 通过 |
| runtime1 十份不同的已保存助手正文 | **9 份解析通过、1 份不完整**；零模型请求、零动作执行 |
| 三份问题 JSON 样本 | 2 份完整修复、1 份不完整诊断；与十份正文有重叠，不重复计数 |

新增 48 项覆盖文本内引号、已有转义不变、组合换行/Markdown/尾逗号、严格结构/重复键拒绝、Python 源码和文件文本字段、截断原文及不可执行片段、文件交接校验、host/显式 workspace 目录范围、系统根只读检查、符号链接删除语义、真实 shell/Python 的外部 cwd、rg/grep 缺失时固定 Python 检索、统一工具循环、能力拒绝、配置字段读回、并发会话键合并、原子写入失败、配置符号链接拒绝、离线配置、模型和工作目录切换、持久技能选择和命令名保护。

## 旧测试的迁移

用户明确要求取消意图判断，原来的“chat 禁止工具”“未命中目标关键词不得执行”“根据拒绝措辞自动插入搜索”等断言不再是本版合同。第一次直接运行未迁移的旧 692 项测试出现 31 个失败、10 个错误；其中有已删除字段/门控的预期差异，也发现了提示兼容性和可选 workspace 恢复事件的回归，后者已修正。

原 `test_intent_runtime.py`、`test_runtime_intent_hard_gates.py` 的门控用例改为统一循环、真实执行证据、当前授权、延续上下文及失败关联测试，数量未减少，未使用 skip 隐藏。`test_session_context` 继续实际测试五次文件写入、HTTP 502 后继续读取且不重放；只是把旧 `intent_route` 断言换为新 `execution_mode`。涉及其他能力的 verify 可执行，但仍不能解除 browser 的待核验状态。普通源码回归和各工具权限测试继续执行。

因此“最终 739/740”是**迁移后的当前需求测试集**，不是声称旧意图门控测试原封不动全部通过。新的 `completion_evidence` 只记录实际工具证据，不通过自然语言或模型自称完成构造证明。

## 唯一未通过项与基线复核

```text
test_parent_service.ParentServiceTests.
    test_single_instance_handoff_waits_for_existing_desktop_bridge
```

该用例期待进度消息包含“继续等待现有父应用”，实际未出现，断言失败。已在从原始 1.17.3 源码包独立解压的未修改副本中重新运行，得到相同断言失败。本版没有修改 `parent_service.py`，也没有删除/跳过此测试。不能报告 Agent 全集全绿。

## 复现

在项目根目录：

```bash
PYTHONPATH=desktop-agent/src:desktop-agent/tests python3 -m unittest -v test_upgrade1174
PYTHONPATH=desktop-agent/src:desktop-agent/tests python3 -m unittest discover -s desktop-agent/tests -p 'test_*.py'
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_glm_no_reload.cjs tests/test_launcher.cjs \
  tests/test_provider_layout.cjs tests/test_browser_import.cjs \
  tests/test_node_diagnostics.cjs tests/test_recovery_access.cjs \
  tests/test_progress_view.cjs tests/test_verification_detection.cjs
npm run check
python3 -m compileall -q desktop-agent/src backend
bash -n run.sh desktop-agent/run.sh desktop-agent/setup.sh
```

基线独立复核命令，在未修改 1.17.3 根目录执行：

```bash
PYTHONPATH=desktop-agent/src:desktop-agent/tests python3 -m unittest -v \
  test_parent_service.ParentServiceTests.test_single_instance_handoff_waits_for_existing_desktop_bridge
```

## 尚未验证

没有执行真实网页账号、GLM 实时问答、原生 Electron 图形或完整 JSDOM 测试；容器未安装项目 npm 依赖，本次未尝试安装。没有验证用户本机磁盘权限、代理、网页 DOM 或所有文件系统组合。固定 Web 工具的私网保护未改，取消文件工作区限制不等于取消 Web URL 限制。浏览器验证码仍需要人工处理。

完整源码包不含用户 runtime1、真实配置、虚拟环境、node_modules、缓存；原始问题样本只存在独立回放包。逐文件补丁应用、压缩包校验及离线 CLI smoke 记录在交付核验文件 `DELIVERY_VERIFICATION_1174.json` 中；修改文件清单为 `MODIFIED_FILES_1174.txt`。
