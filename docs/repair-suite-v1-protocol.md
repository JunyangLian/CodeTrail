# 真实修复评测 v1：协议与复现

本轮固定为 **20 个独立任务，每题 3 次，共 60 次运行**。题型为单文件逻辑 5 题、跨文件契约 7 题、未知位置定位 4 题、回归与兼容 4 题。每题 8 个 unittest 测试方法；断言及重复运行不计为新任务。

已全部执行并独立重测：功能43/60（71.67%），完整流程42/60（70.00%）。[最终报告](../benchmarks/results/repair-suite-v1-2026-10-24/report.md)与[失败分析](repair-suite-v1-failure-analysis.md)保留全部结果和分组口径。

## 题目来源

12 题是自行设计的小型任务，其中 `pagination_boundaries` 和 `user_lookup_contract` 保留此前的故障文件及验收测试；这两题已经用于早期调试，不能声称是未见过的留出集。新增 18 题中的 8 题来自两个开源仓库的真实故障修复，采用最小复现，未评测完整上游仓库。

| 题型 | 任务 |
|---|---|
| 单文件逻辑（5） | pagination_boundaries、interval_union、ttl_expiry、retry_schedule、dependency_order |
| 跨文件契约（7） | user_lookup_contract、money_minor_units、config_precedence、cursor_roundtrip、error_response_contract、utc_storage_contract、inventory_reservation |
| 未知位置定位（4） | boltons_isoparse、boltons_xfrange、boltons_multireplace、boltons_splitlines |
| 回归与兼容（4） | humanize_list、humanize_fractional、humanize_metric、humanize_size |

上游故障及修复依据：

| 任务 | 可核对的真实修复 |
|---|---|
| boltons_isoparse | [小数秒缩放](https://github.com/mahmoud/boltons/commit/1f37cce42f8d3146342ccbd45492d3b3d9c52698) |
| boltons_xfrange | [下降序列与浮点计数](https://github.com/mahmoud/boltons/commit/3e9983db7b59578add73c01cd31fa7ecd8da2e04) |
| boltons_multireplace | [空替换表](https://github.com/mahmoud/boltons/commit/435774ef8b10c1355bf77483a837945034011754) |
| boltons_splitlines | [Unicode 行分隔符](https://github.com/mahmoud/boltons/commit/d12d86a4706aa4a367a91679b0ff9b6a572e5298) |
| humanize_list | [空列表](https://github.com/python-humanize/humanize/pull/318) |
| humanize_fractional | [负带分数](https://github.com/python-humanize/humanize/pull/320) |
| humanize_metric | [SI 前缀舍入进位](https://github.com/python-humanize/humanize/pull/328) |
| humanize_size | [文件大小单位舍入进位](https://github.com/python-humanize/humanize/pull/329) |

`benchmarks/repair_suite_v1/sources/` 保存原始故障/修复模块、提交元数据和许可证；`tasks.json` 标出提取符号、原始模块 SHA-256 及适配方式。函数/类使用原始 AST 源码片段，不额外注入故障。适配包括所需导入、humanize 英文环境的 gettext 恒等函数、公开入口和无关模块；浮点生成器的演示入口最多取 1000 项，避免故障方向的无限生成耗尽内存，测试输入输出均远小于此边界。

这批公开故障可能已出现在模型训练数据中；没有做训练集去污染，不能称为无污染或隐藏基准。

## 首次模型运行前的冻结

- 先检查故障版本确实失败、参考版本 8/8 通过，再生成 `freeze.json`。
- 冻结覆盖题目、可见验收测试、原始版本、参考版本、来源、协议、60 个运行槽位及全部 Harness Python 源码。
- 冻结指纹：`bff6a0b10537b8715e85265eae956ee90553c1d6e7a3d765b05305f2e8933b01`。
- 首次请求前的本地 Git 提交：`bf4f841008805599d004af938b6900a4a6902ed7`。
- 每次运行前后及最终审计均重新核对哈希；整个批次不修改 Prompt、金标、预算或运行时代码。预检阶段修复的评测设施问题不属于模型运行，早期预检目录仍保留在本地 artifacts。

参考修复仅用于外部预检，Agent 工作区只安装该题故障源码、可见测试、任务说明及必要许可证。它不含任务总清单、参考文件、修复提交信息或其他运行的记忆。进程启动时设置 Git 搜索上限，防止刷新上下文时读到外层 CodeTrail 仓库；审计逐次检查模型 Prompt 中的仓库根目录。

## 统一参数与隔离

模型 `zai-org/GLM-5.3-Flash`，硅基流动 Chat Completions，temperature=0.2，每次请求超时120秒，输出上限3072 Token，最多20次工具调用。固定随机种子20261024生成运行顺序，并发3个工作进程，运行墙钟上限900秒。

每个运行槽位创建新目录、新会话、新记忆；不续接前题。只允许 list/read/search/patch/write 和指定测试命令。写操作仅批准该题允许的实现文件，shell 只批准 `python -B -m unittest discover -v`。未知位置题不在任务说明中提供缺陷文件名，允许修改该小工作区的实现模块；定位仍局限于数个文件，不代表大型仓库导航能力。

Provider 原有实现最多尝试3次传输请求，属于同一运行内的网络重试；不另开运行替换失败。中断后 `--resume` 只执行尚未启动的槽位，已经启动而中断的记录保留为失败。

## 指标定义

**功能验收通过**：将最终允许的实现文件复制到新的独立目录，重新安装冻结测试，外部 Python 子进程8/8通过、没有skip/expected failure，且受保护文件哈希及修改范围合法。测试命令不会继承模型API Key。实现缺失、越界、测试报错或验证超时均不通过。

**完整流程通过**：功能验收通过，Agent正常结束，且最后一次写操作之后指定测试命令的真实退出码为0。模型口头声称成功不计通过，修复后未重测单独记为流程失败。

工具步数包括被拒绝或失败的工具调用；attempts是模型请求轮次。Token来自Provider返回的usage；缺失用量明确记为未知，不以0替代。时长记录每题运行墙钟、Agent阶段和独立验证时长；并发运行的耗时总和不等于批次墙钟时间。

原始Harness记录中的Token完整性只覆盖成功返回的响应；独立审计将其收紧为全部逻辑请求都有用量。因此原始记录与最终审计的Token覆盖次数可能不同，不能以原始字段暗示超时请求没有成本。原始文件不覆盖、不改写。

安全统计区分被拦截的路径/审批尝试和最终未授权文件变化。失败归因保留错误信息、无效参数、重复工具调用、预算耗尽、断言失败和未重测等轨迹证据。批准的Python测试仍会执行实现代码，这不是操作系统级安全沙箱；不用于不可信恶意代码的安全评测。

最终自动化回归为 **225 项全部通过**；JUnit证据位于本轮批次目录的 `full-tests-final.xml`。这225项是Harness/网页/评测设施的自动化测试，与20个独立真实修复任务分别统计。

## 复现与 Demo

在项目根目录配置本地 `.env`（不要提交 Key），然后执行：

```powershell
python -X utf8 scripts/run_repair_suite.py --check
$env:GIT_CEILING_DIRECTORIES = (Join-Path (Get-Location) 'artifacts')
python -u -X utf8 scripts/run_repair_suite.py --output artifacts/repair-suite-v1-reproduction
python -X utf8 scripts/audit_repair_suite.py --output artifacts/repair-suite-v1-reproduction --audit-output artifacts/repair-suite-v1-reproduction-audit
```

旧目录不可覆盖。运行会产生真实模型费用；新模型采样的结果可能与保存轨迹不同。

```powershell
python -X utf8 scripts/prepare_suite_demo.py --task boltons_isoparse --destination examples/new-repair-demo
python -X utf8 -m pico.web --cwd examples/new-repair-demo --model-timeout 120 --open
```

已生成的 `examples/repair-suite-demo` 保留故障起点；README包含任务提示。启动网页后提交其中的任务即可演示，初始测试失败是预期行为。Demo 不自动重置或覆盖已有修复。

本机还提供零新增模型调用的真实补丁回放：

```powershell
python -X utf8 scripts/replay_repair_demo.py --destination artifacts/repair-suite-replay-demo-new
```

该命令读取已保存的 `02-boltons_isoparse-r3` 模型产物，检查最终文件哈希，在全新的 before/after 目录分别执行冻结测试。它不是第61次模型运行，也不会修改原始评测记录。

## 证据结构

`artifacts/repair-suite-v1-2026-10-24/` 保存批次配置、results/progress、60个独立目录。每个目录包含 `model_io.jsonl`（完整请求与原始回复）、`tool_io.jsonl`（未裁剪工具输出）、审批记录、`changes.diff`、最终源码、原始 session、Agent trace/report 和外部测试结果。

`artifacts/repair-suite-v1-audit-2026-10-24/` 保存第二次独立重测、审计 JSON 和最终 Markdown 报告。审计记录所有原始证据的 SHA-256；不覆盖原始结果。原始轨迹默认被Git忽略，需在本机保留或单独导出证据包，不能仅用截图替代。

本轮公开题的完整轨迹、源码快照和测试已单独导出为[完整证据包](../benchmarks/results/repair-suite-v1-2026-10-24/evidence.zip)，经过配置凭据与密钥签名扫描，未包含 `.env` 或原先的私人会话。解压到新目录或项目根目录，保留原有相对路径即可进行离线重测/回放；该导出不是额外的模型运行。

20题与60次重复应同时报告。样本来自两个库且使用可见测试，没有未见仓库或隐藏测试的独立留出评估；本轮结果只支持这一固定小规模任务集的工程验证结论。
