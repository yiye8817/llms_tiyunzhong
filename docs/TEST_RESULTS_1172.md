# 1.17.2 / Agent 0.14.0 交付验证

验证环境：Linux 容器，Python 3.13.5、Node.js 22.16.0。以下为实际运行结果，不包含真实账号网页端到端测试。

## 实际结果

| 检查 | 结果 | 范围 |
|---|---:|---|
| 后端 Python 全集 | 126 / 126 通过 | 原有后端用例及新增共同截止时间、两路全部完成才整合、超时取消、原文保留、显式部分结果、单模型不受影响 |
| 无外部 npm 依赖的 Node 用例 | 127 / 127 通过 | 以下命令列出的八个测试文件；包含 GLM 38 项适配器用例、8 项验证控件检测及4项进度界面逻辑用例 |
| JavaScript 语法检查 | 通过 | `npm run check` |
| Agent Python 全集 | 638 / 640 通过 | 包含本地协议、文件交接、客户端、运行器、意图权限、终端和既有工具测试；两项未通过见下 |
| 新增 JSON / 响应文件 / 回放专项 | 21 / 21 通过 | 已包含在 Agent 全集中；真实本地 HTTP→落盘→Agent读回→本地文件动作→下一轮回答、篡改/写入失败不执行、回放等 |
| 用户 events1.jsonl 回放 | 1 / 1 通过 | 分片重组并去重得到1份助手正文，修复4处非法 Markdown 转义；无缺片警告，0个模型请求、0个动作执行 |

日志回放原始助手正文 SHA-256：

```text
69f5f8ce0b9968625c05f95a6763def1207ad5dccffc31916d45dafbbb1542f7
```

源码压缩包不包含用户事件日志和回复内容，单独提供该次回放结果包。

## 两个未通过的 Agent 用例

1. `test_parent_service.ParentServiceTests.test_single_instance_handoff_waits_for_existing_desktop_bridge`：断言必须出现“继续等待现有父应用”提示失败。已在**未修改的 1.17.1 原包**中独立运行并复现；修改版独立运行也失败。它涉及子进程退出与已存在父应用桥接就绪的提示时序，相关实现及测试本版未改动。
2. `test_web_search.ProductionProcessTests.test_bounded_process_timeout_kills_descendant_process_group`：整套测试时，0.5秒超时前子进程未写出预期的 `.parent` 文件。**原包和修改版分别独立运行该用例均通过**，完整修改版套件中仍失败；相关进程执行实现和测试未改动。不能据此声称全套通过，保留为时序/环境敏感问题，未修改断言来掩盖它。

另修正后端请求日志的一处兼容问题：读取 `scope.path` 而不是提前构造 `request.url`，避免非法 Host 在进入已有校验前触发异常；后端对应非法 Host 用例现在通过。

## 运行命令

在项目根目录运行：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'

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

PYTHONPATH=desktop-agent/src python3 -m unittest discover \
  -s desktop-agent/tests -p 'test_*.py'

PYTHONPATH=desktop-agent/src:desktop-agent/tests python3 -m unittest \
  -v test_response_files
```

复核两个时序用例：

```bash
PYTHONPATH=desktop-agent/src:desktop-agent/tests python3 -m unittest -v \
  test_parent_service.ParentServiceTests.test_single_instance_handoff_waits_for_existing_desktop_bridge \
  test_web_search.ProductionProcessTests.test_bounded_process_timeout_kills_descendant_process_group
```

## 未验证范围

当前容器缺少完整 npm 依赖，尝试安装时无法解析 npm registry 域名；未运行需要 JSDOM 的完整 `npm test`，也未运行真实 Electron 图形界面 smoke 测试。上述 Node 用例针对生产代码，使用各自提供的网页/CDP/界面最小测试替身，不代表真实站点验证。

没有使用真实 GLM 账号验证登录、`glm-5.3-flash` 选择保留或不同验证码形态；没有运行真实双模型联网对话。验证码只实现检测、暂停和人工恢复，不实现绕过或自动滑动求解。网页 DOM 更新可能需要进一步调整选择器。

在有图形桌面、网络和依赖的本机可以先运行 `./run.sh --check`；它覆盖父应用 Python、完整 Node 及语法检查，不包含 Agent 测试，Agent 仍按上面的命令运行。
