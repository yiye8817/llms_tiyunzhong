# 1.17.5 实际测试记录

日期：2026-09-11。环境：Linux，Python 3.13.5（/opt/pyvenv），Node v22.16.0；Chromium 144.0.7559.96。所有下列执行均在本次会话实际完成。

| 检查 | 结果 |
|---|---|
| Agent 全集 | 768 / 768 通过，0 失败，0 跳过；54.164 秒 |
| 新增 Agent 修复/文件执行链/配置专项 | 28 / 28 通过，已包含在 768 中 |
| 父应用 Python 全集 | 126 / 126 通过；4.206 秒 |
| 选定无需 npm 依赖的 Node 测试 | 140 / 140 通过，0 跳过 |
| 新增 Node 原文提取专项 | 11 / 11，包含在 140 中 |
| GLM adapter 整体测试 | 40 / 40，包含在 140 中；其中新增 2 项 |
| Chromium 生产 DOM 场景 | 新版 18 / 18 通过；相同场景旧 json-tools-config 包 12 / 18 通过 |
| events3 离线回放 | 2 份去重回复，2 通过，1 修复 18 个引号，0 失败；0 请求、0 动作 |
| npm run check、compileall、bash -n | 通过 |
| Agent CLI --version / doctor | 0.16.1 / python_deterministic_v4 / 新代码路径，未访问 API |

## 可重复命令

在项目根目录执行：

```bash
PYTHONPATH=desktop-agent/src:desktop-agent/tests python -m unittest discover -s desktop-agent/tests -p 'test_*.py'
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_glm_no_reload.cjs tests/test_launcher.cjs tests/test_provider_layout.cjs tests/test_browser_import.cjs tests/test_node_diagnostics.cjs tests/test_recovery_access.cjs tests/test_progress_view.cjs tests/test_verification_detection.cjs tests/test_upgrade1175.cjs
npm run check
python -m compileall -q desktop-agent/src backend
bash -n run.sh desktop-agent/run.sh desktop-agent/setup.sh
```

可选 Chromium 场景需要 Python playwright 和系统 Chromium，不是项目新增强制运行依赖：

```bash
python tests/browser_glm1175_smoke.py --output /tmp/glm1175-results.json
```

浏览器场景使用真实 Chromium 的 DOM、选择器、innerText 与生产 pageAction 代码，用 page.set_content 构造页面、注入模拟的 location；没有访问 GLM、没有真实登录或服务器推理，也没有测试 Electron 窗口/CDP 的真实提交链。场景包含乐观用户消息先于 URL、URL 先于消息、首次绑定后再换会话、重复路由变更、其他用户消息、草稿变化、现有会话、已确认新建但路由滞后、跨域、p/br/复制按钮、相同消息 DOM 重挂载、正常回答、服务错误、缺用户容器和多次起始页轮询。

新增 Agent 集成测试读取临时 JSONL：异常回复落盘并读回→本地纠正→有 shell 授权时真实启动 Python 子进程一次，未授权时不执行。临时脚本只读取自建测试文件。用户 events3 中的代码只作编译检查和离线回放，没有执行。

## 旧版对照与迁移测试

对实际 events3：较早 `json-unified-tools-config` 在第99列报错；较后 `json-tools-config` 可完成简单18处文本引号修复；1.17.5 在此基础上新增专用代码边界编译校验/哈希、复杂字典三引号测试和独立诊断源码。

既有 tests/test_reply_extraction.cjs 中一项“畸形但完整原文必须丢弃”的断言更新为“以 json_raw_unvalidated 原样交给本地解析器”。这是本次预期行为变化，不把 JSON 当成已验证动作。该 JSDOM 测试文件因依赖不可用未运行；等价模块级原文保留/拒绝场景由实际执行的 test_upgrade1175.cjs 覆盖。

## 未验证项与限制

npm 安装尝试因 registry.npmjs.org DNS EAI_AGAIN 失败。完整 npm test（依赖 jsdom、turndown 等）、Electron 图形端到端、真实 GLM 账号和公网模型双路整合没有执行，不能把本地 DOM 场景称为线上验证。本次 Agent 全集没有失败或跳过；历史版本报告的时序失败不是本次测试结果。

交付阶段将对两种原始 1.17.4 分别实际应用补丁，并校验所有文件字节与新源码包一致；结果记录在 DELIVERY_VERIFICATION_1175.json 和补丁包 README。
