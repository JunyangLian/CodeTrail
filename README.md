# CodeTrail · 代码执行工作台

面向本地代码仓库的 Agent Harness 与网页工作台。把读取代码、执行工具、审批修改、运行测试和查看结果放在同一条可追踪的执行流程中。

![CodeTrail 工作台](assets/screenshots/codetrail-qa-desktop-20261024.png)

## 能力

- **Agent 循环**：模型请求、工具调用、结果回传与最终回答。
- **上下文管理**：分层组织历史、记忆与当前任务，按预算裁剪。
- **轻量记忆检索**：tag、关键词与中文双字片段召回；文件摘要使用内容哈希检查新鲜度。
- **操作治理**：参数和路径校验、写操作与命令审批、只读模式、重复调用拦截及日志脱敏。
- **过程可视化**：工具事件、文本差异、命令退出码、历史运行与会话继续。
- **结果判定**：区分模型回答结束、验证通过、验证失败和需要重测。

网页使用 Python 标准库 HTTP 服务与原生 HTML/CSS/JavaScript，无前端构建依赖。服务只绑定本机。

## 快速开始

需要 Python 3.10 或更高版本。在项目根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
```

在本地 `.env` 中填入模型服务的 API Key。默认示例使用硅基流动的 Chat Completions 接口；Key 不需要填写到网页中，也不应提交到仓库。

```dotenv
PICO_PROVIDER=openai
PICO_OPENAI_API_BASE=https://api.siliconflow.cn/v1
PICO_OPENAI_API_FORMAT=chat_completions
PICO_OPENAI_MODEL=zai-org/GLM-5.3-Flash
PICO_OPENAI_API_KEY=填写自己的Key
```

启动工作台：

```powershell
python -X utf8 -m pico.web --cwd examples/interview-demo --model-timeout 120 --open
```

Windows 也可以双击 `start-web.cmd`。默认网址为 **http://127.0.0.1:8765/**。修改 `.env` 后需要重启服务。

选择“逐步确认”，输入任务；写入文件或执行命令前会显示审批卡片。运行结束后，结合代码差异、测试输出和退出码检查结果。具体用法见[网页指南](docs/local-web-workbench.md)。

### 终端入口

```powershell
codetrail --cwd examples/interview-demo --approval ask
codetrail-web --cwd examples/interview-demo --model-timeout 120
```

未安装命令入口时，可使用 `python -m pico` 和 `python -m pico.web`。底层 Python 包名、`PICO_*` 环境变量与 `.pico/` 数据目录保留兼容性。

## 修复示例

| 示例 | 内容 | 验收 |
| --- | --- | --- |
| `examples/interview-demo` | 商品数量、折扣、运费与输入校验 | 订单 12 项，加法 3 项 |
| `examples/repair-demo-pagination_boundaries` | 分页起点、末页、向上取整与非法输入 | 8 项 |
| `examples/repair-demo-user_lookup_contract` | 跨文件参数顺序、ID 规范化与查询契约 | 8 项 |

订单示例保留已完成的修复结果。重复演示前运行：

```powershell
python examples/interview-demo/demo.py reset
```

它会备份当前实现，再恢复故意设置的缺陷。其他两个示例保留故障起点；任务要求见各自 README。

## 验证与证据

固定回归、受控消融和真实模型任务分开报告，不把单元测试数量当作独立任务数量。

- 订单演示：真实模型修复后 15/15 测试通过，独立重测通过，测试文件未修改。
- 两类新增修复任务，各运行两次：原始四次中功能验收 3/4、完整流程 2/4；参数反馈改进后的 1 次定向回归单独记录。
- 实际 Tabbit 浏览器完成 17 项检查，覆盖审批、刷新恢复、历史、错误状态及桌面／窄屏布局。交互使用隔离的脚本模型后端；真实模型修复另行验证。

这些是小规模工程验证，不代表复杂仓库的普遍修复成功率。

- [评测口径](docs/evaluation-scope.md)
- [真实修复与回归记录](docs/repair-validation.md)
- [失败分析](docs/failure-analysis.md)
- [网页验收](docs/workbench-browser-acceptance.md)
- [运行架构](docs/architecture/agent-harness-v1-overview.md)

本地执行相关自动化测试：

```powershell
python -m pip install pytest
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests/test_web.py tests/test_web_status.py tests/test_tool_executor.py tests/test_memory.py tests/test_verifier.py -q -p no:cacheprovider
```

真实模型实验脚本会调用自己的模型接口；固定单元与集成回归使用预设响应。原始私人会话、Key、缓存和生成工作区不随源码发布。

## 使用范围

这是本地单用户工具。命令审批与路径限制不等同于操作系统级沙箱，应在可信的示例或工作区中使用。记忆检索采用透明的词法规则，没有 Embedding、通用语义冲突消解或完整的记忆提示词注入防护。

## 来源与改进

CodeTrail 基于 [Pico](https://gitee.com/htxoffical/pico) 二次开发，保留其 Agent Harness 核心。新增和完善的内容包括本地网页工作台、硅基流动 Chat Completions 适配、中文词法召回、Windows 执行兼容、执行结果判定、可重复修复案例和运行证据整理。详见 [来源说明](UPSTREAM_NOTICE.md)。
