# 1.17.9 / Agent 0.17.0 实测记录

测试在当前容器实际执行，不含真实 Qwen、GLM 登录账号。没有把静态检查、模拟服务或本地浏览器测试标作线上端到端成功。

| 检查 | 结果 | 范围 |
|---|---:|---|
| Agent 全集，系统 Python 3.13 | 789/789 | 21 项新增 JSON/技能专项已包含；其中五个模型 ID 通过真实本地 HTTP 服务和共同技能注册表验证 |
| 父应用 Python 全集 | 134/134 | 4 项新增分级重试配置测试已包含；其余覆盖原有接口和完整输入 |
| 选定 Node 回归 | 172/172 | 12 个无需 npm 图形依赖的测试文件，包含 21 项新增重试、截图特征、私有存档和计时测试 |
| Chromium DOM 回归 | 96/96 | GLM 18、Qwen 50、1177 输入 15、1178 输入 13 |
| 生产适配器 + Chromium/CDP 集成 | 10/10 | 本次 Qwen 分级恢复 7 场景；长输入与双模型发送回归 3 场景 |
| runtime2 离线问题 JSON | 3/3 | 去重后 2 份；全部已保存回复去重后 3 份也全部通过 |
| JavaScript 语法检查 | 通过 | `npm run check` |
| Python / shell 语法 | 通过 | 118 个 Python 文件，两个启动脚本 |

## 新增浏览器场景

页面脚本正常响应 DOM 点击；页面忽略非可信点击后截图+真实 CDP 点击成功；前两级无新请求后人工可信点击成功；未知控件先人工学习；下一次用已学习描述与截图特征自动点击；没有人工操作按设定时限退出；Qwen 显示性 model/utm 参数不被误判成新会话。

浏览器输入、截图像素及 DOM 是实际 Chromium（144.0.7559.96）。网络状态通过受控 Network 通知与模拟 Qwen 页面提供，人工点击由测试驱动模拟。生产使用 Electron NativeImage；测试使用真实 Chromium PNG/RGBA 实现相同裁剪/缩放接口。不是已登录网站或 Electron GUI 验证。

长输入回归确认 Qwen 55,300 个 UTF-16 代码单元通过 14 次原生写入完整输入、一次原始发送，错误后一次页面重试；GLM 在 Qwen 回答完成前完成发送。测试不会把重试当作重新填写问题。

20 秒默认人工等待使用可控时钟做确定性测试；真实浏览器超时场景使用 1 秒配置以避免不必要等待。检测到新生成后继续等待完整答案，而不是等待满 20 秒才继续。

## 已知限制及测试调整

容器 Python 虚拟环境在旧的 `test_single_instance_handoff_waits_for_existing_desktop_bridge` 进度提示时序断言上失败；已在未修改的 1.17.8 原包中复现。本次没有修改或跳过该用例。最终使用系统 Python 全集 789 项通过。两种环境的解释器路径不同，进度时序不能当作网络性能测量。

`test_protocol_compat.py` 的含糊 URL 用例根据“JSON 仅本地处理”要求改为验证停止且不请求模型纠正；`test_upgrade1175.py` 更新修复器版本断言；`test_backend.py` 更新发布版本断言。原 `test_qwen_network_retry.cjs` 和 `test_web_recovery.cjs` 明确设置 `qwen_retry_stages=false` 继续验证旧配置策略，新增测试覆盖默认分级策略。没有将行为变化用跳过断言掩盖。

npm 测试依赖安装尝试失败：`EAI_AGAIN registry.npmjs.org`。完整 Electron/JSDOM 测试没有执行，不能计为通过；没有真实账号联网验证。本地截图定位依赖当前错误区域的语义控件或已成功学习的图像/控件描述；没有可靠匹配时必须人工处理，不宣称可识别所有网站变更或 canvas UI。

## 复验

```bash
PYTHONPATH=desktop-agent/src /usr/bin/python3 -m unittest discover -s desktop-agent/tests
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_qwen_retry1179.cjs
node tests/browser_retry1179_adapter.cjs /tmp/retry1179-report.json
node tests/browser_send1178_adapter.cjs /tmp/send1178-report.json
npm run check
```

浏览器专项需 Python Playwright、Pillow、系统 `/usr/bin/chromium`，不自动安装、不登录网站。完整命令与逐场景结果见 `TEST_EVIDENCE_1179.json`。源码包不包含 runtime2 原始日志或测试生成的屏幕截图；私人回放另行打包。

## 发布校验

本版 39 个新增或修改路径，完整源码 260 个文件。发布脚本在全新 1.17.8 基线实际应用补丁，逐文件比较内容、相对路径及权限，再解压最终 tar.gz 再次比较。完整文件清单见 MODIFIED_FILES_1179.txt。
