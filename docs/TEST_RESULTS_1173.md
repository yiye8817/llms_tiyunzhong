# 1.17.3 / Agent 0.15.0 实测记录

## 测试范围与结果

本次在 Linux 容器中实际执行；没有使用用户的真实账号或对公网模型服务发出任务。Python、Node 的具体版本见下方环境记录。

| 检查 | 实际结果 |
| --- | --- |
| 新增专项测试 | **52 / 52 通过**（已包含在 Agent 全集中，不重复计算总数） |
| Agent 全集 | **691 / 692 通过**，1 个父应用交接时序断言失败，原包复现情况见下 |
| 父应用 Python 全集 | **126 / 126 通过** |
| 选定的原生 Node 测试 | **127 / 127 通过** |
| `npm run check` | 通过 |
| CLI 本地 smoke | `doctor`、`tools`、`run --help` 正常返回 |

新增 52 项包括：本地执行 16 项、Python 文件检索 6 项、网页读取 15 项、运行器整合 15 项。重点覆盖真实命令优先、真实缺失命令的 Python 实现、参数文件读入、源码权限及哈希、禁止非零/超时/拒绝后的自动兜底、失败副作用标记、关闭生成式代码、rg/grep 均缺失、恶意正则超时、工作区和符号链接限制、网页重定向与公网地址检查、网页文本落盘读回和篡改拒绝、指定页面意图、零步骤错误完成防护、缺失命令失败记录的严格恢复关联。

网页测试使用两种方式：受控 `_get` 响应，以及真实本地 HTTP 服务。后者仅在测试中替换解析与固定连接地址；生产代码仍禁止回环/内网目标。本地 HTTP 用例实际经过 socket、HTTP 客户端、读取、解析与文件保存；不是公网 GitHub、HTTPS/账号登录或真实模型端到端验证。

## 未通过用例与基线复核

失败用例：

```text
test_parent_service.ParentServiceTests.
    test_single_instance_handoff_waits_for_existing_desktop_bridge
```

其断言要求进度文本出现“继续等待现有父应用”。完整运行执行 692 项，仅此断言未通过。首次运行 687 项时也遇到该断言；之后增加了 5 项新测试。

在从原始 `multillm-fusion-1.17.2-json-files-chat-sync.tar.gz` 单独解压、未修改的副本上运行同一测试，仍在同一个断言失败；新版本独立运行也失败。本次没有修改该父应用交接实现或删除/跳过该用例，不能报告 Agent 全集全绿。

## 复现命令

从项目根目录执行：

```bash
PYTHONPATH=desktop-agent/src:desktop-agent/tests python3 -m unittest -v \
  test_local_execution test_python_search test_web_fetch test_runtime_local_execution

PYTHONPATH=desktop-agent/src:desktop-agent/tests python3 -m unittest discover \
  -s desktop-agent/tests -p 'test_*.py' -v

python3 -m unittest discover -s tests -p 'test_*.py' -v

node --test \
  tests/test_glm_no_reload.cjs \
  tests/test_launcher.cjs \
  tests/test_provider_layout.cjs \
  tests/test_browser_import.cjs \
  tests/test_node_diagnostics.cjs \
  tests/test_recovery_access.cjs \
  tests/test_progress_view.cjs \
  tests/test_verification_detection.cjs

npm run check

PYTHONPATH=desktop-agent/src python3 -m fusion_agent doctor
PYTHONPATH=desktop-agent/src python3 -m fusion_agent tools
PYTHONPATH=desktop-agent/src python3 -m fusion_agent run --help
```

基线失败用例独立复核：

```bash
PYTHONPATH=desktop-agent/src:desktop-agent/tests python3 -m unittest -v \
  test_parent_service.ParentServiceTests.test_single_instance_handoff_waits_for_existing_desktop_bridge
```

## 明确未做的验证

本环境未安装完整 npm 依赖，完整 JSDOM/Electron 图形测试未执行。本次没有尝试安装这些依赖，也没有验证用户本机网络、代理、GLM 登录或具体网页 DOM；没有读取公网 GitHub 来冒充用户任务的执行结果。新增源码编译检查和 shell 脚本语法检查、发布补丁应用与文件哈希核对的结果记录在下方交付核验中。

## 环境

Python：3.13.5；Node.js：v22.16.0。

## 交付核验

Python 源码 `compileall`、两个 `run.sh` 的 `bash -n` 检查通过。增量补丁已在从原始 1.17.2 包重新解压的独立目录实际应用，无需手工干预；补丁应用后的全部 211 个文件与源码逐一 SHA-256 相符。完整 tar.gz 的全部文件也逐一与源码比对。26 个新增或修改文件的清单在 `MODIFIED_FILES_1173.txt`。包内不含运行目录、用户原始日志、虚拟环境、node_modules 或 Python 缓存。

`events2-routing-replay-1173.json` 是输入日志的离线意图分类对照，不是已经执行完成 GitHub 项目解析的结果。
