# CodeTrail 真实代码修复评测（20题 × 3次）

冻结指纹：`bff6a0b10537b8715e85265eae956ee90553c1d6e7a3d765b05305f2e8933b01`。独立任务 20 个，运行 60 次；每题3次，所有计划运行均纳入。

功能验收：**43/60（71.67%）**。完整流程：**42/60（70.00%）**。

功能验收要求冻结测试在独立干净目录通过、测试及非授权文件不变。完整流程额外要求Agent正常结束，最后一次修改后指定测试命令成功。

| 题型 | 独立任务 | 运行 | 功能通过 | 完整流程通过 |
|---|---:|---:|---:|---:|
| 单文件逻辑 | 5 | 15 | 15 | 15 |
| 跨文件契约 | 7 | 21 | 19 | 19 |
| 未知位置定位 | 4 | 12 | 5 | 4 |
| 回归与兼容 | 4 | 12 | 4 | 4 |

| 来源 | 独立任务 | 运行 | 功能通过 | 完整流程通过 |
|---|---:|---:|---:|---:|
| 自行设计 | 12 | 36 | 34 | 34 |
| 真实上游故障最小复现 | 8 | 24 | 9 | 8 |

## 每题结果（运行次序遵循冻结计划）

| 任务 | 功能通过/3 | 流程通过/3 |
|---|---:|---:|
| boltons_isoparse | 1/3 | 1/3 |
| boltons_multireplace | 3/3 | 3/3 |
| boltons_splitlines | 1/3 | 0/3 |
| boltons_xfrange | 0/3 | 0/3 |
| config_precedence | 3/3 | 3/3 |
| cursor_roundtrip | 3/3 | 3/3 |
| dependency_order | 3/3 | 3/3 |
| error_response_contract | 3/3 | 3/3 |
| humanize_fractional | 1/3 | 1/3 |
| humanize_list | 3/3 | 3/3 |
| humanize_metric | 0/3 | 0/3 |
| humanize_size | 0/3 | 0/3 |
| interval_union | 3/3 | 3/3 |
| inventory_reservation | 2/3 | 2/3 |
| money_minor_units | 3/3 | 3/3 |
| pagination_boundaries | 3/3 | 3/3 |
| retry_schedule | 3/3 | 3/3 |
| ttl_expiry | 3/3 | 3/3 |
| user_lookup_contract | 3/3 | 3/3 |
| utc_storage_contract | 2/3 | 2/3 |

## 过程开销与安全

- 工具调用总数 417，平均 6.95 次/运行（含拒绝与失败调用）。
- 运行总耗时 8344.265 秒，平均 139.071 秒；并发为3，批次墙钟时间 2874.599 秒。
- 可观测输入Token 977528，输出Token 294946；完整用量记录覆盖 45/60 次运行。失败请求未知用量未按0估计。
- 模型逻辑请求 481 次，其中失败或未返回 15 次。Provider内部网络重试未提供逐次计费信息。
- 原始Harness的Token完整性字段只检查已成功返回的响应；本审计额外检查全部请求，超时运行的用量标为不完整。原始记录保留不改。
- 被拦截的安全事件 0 个；工作区内最终未授权文件变更 0 次运行。阻止的尝试和已执行越界分别统计。本轮没有专门的对抗攻击任务，不能据此推断通用安全能力。

## 失败归因

- `model_or_runtime_error`：15次。
- `no_code_change`：1次。
- `test_regression_or_incomplete_fix`：1次。
- `missing_reverification_or_incomplete_workflow`：1次。

细分原因：
- `provider_request_timeout`：15次。
- `premature_final_without_code_change`：1次。
- `test_regression_or_incomplete_fix`：1次。
- `missing_post_edit_test`：1次。

工具事件计数（包含修复前预期失败的测试命令，不能直接当作运行失败次数）：
- `tool_failed`：67个事件。
- `invalid_arguments`：16个事件。

## 失败案例与证据

### 07-boltons_splitlines-r1

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：5。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/07-boltons_splitlines-r1/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/07-boltons_splitlines-r1/workspace/.pico/runs/run_20261024-205957-8706d2/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/07-boltons_splitlines-r1/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... ok

### 09-boltons_splitlines-r3

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：3。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/09-boltons_splitlines-r3/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/09-boltons_splitlines-r3/workspace/.pico/runs/run_20261024-210341-0ecbf3/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/09-boltons_splitlines-r3/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... ok

### 10-humanize_metric-r1

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：7。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/10-humanize_metric-r1/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/10-humanize_metric-r1/workspace/.pico/runs/run_20261024-210354-1b4a7f/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/10-humanize_metric-r1/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 11-humanize_size-r3

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：5。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/11-humanize_size-r3/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/11-humanize_size-r3/workspace/.pico/runs/run_20261024-210453-db1e8b/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/11-humanize_size-r3/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 22-inventory_reservation-r3

- 功能验收：False；失败归因：`no_code_change`；工具调用：3。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/22-inventory_reservation-r3/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/22-inventory_reservation-r3/workspace/.pico/runs/run_20261024-211349-efd391/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/22-inventory_reservation-r3/changes.diff`。
- Model returned ordinary final text before repairing code; completed runtime state rejected by independent tests: 我先读取测试文件确认具体契约预期，再进行修复。
- test_01 (test_acceptance.RepairTests) ... FAIL

### 24-humanize_metric-r2

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：7。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/24-humanize_metric-r2/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/24-humanize_metric-r2/workspace/.pico/runs/run_20261024-211425-5ac7a2/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/24-humanize_metric-r2/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 25-boltons_xfrange-r1

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：3。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/25-boltons_xfrange-r1/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/25-boltons_xfrange-r1/workspace/.pico/runs/run_20261024-211431-a866d4/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/25-boltons_xfrange-r1/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 26-boltons_xfrange-r3

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：6。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/26-boltons_xfrange-r3/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/26-boltons_xfrange-r3/workspace/.pico/runs/run_20261024-211501-eac8ef/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/26-boltons_xfrange-r3/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 32-humanize_fractional-r1

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：7。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/32-humanize_fractional-r1/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/32-humanize_fractional-r1/workspace/.pico/runs/run_20261024-211942-590305/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/32-humanize_fractional-r1/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 35-utc_storage_contract-r2

- 功能验收：False；失败归因：`test_regression_or_incomplete_fix`；工具调用：9。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/35-utc_storage_contract-r2/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/35-utc_storage_contract-r2/workspace/.pico/runs/run_20261024-212309-7d0aae/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/35-utc_storage_contract-r2/changes.diff`。
- test_01 (test_acceptance.RepairTests) ... ok

### 36-humanize_metric-r3

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：8。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/36-humanize_metric-r3/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/36-humanize_metric-r3/workspace/.pico/runs/run_20261024-212320-a2c89f/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/36-humanize_metric-r3/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 41-humanize_size-r2

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：5。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/41-humanize_size-r2/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/41-humanize_size-r2/workspace/.pico/runs/run_20261024-212728-817c1d/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/41-humanize_size-r2/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 44-boltons_isoparse-r2

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：6。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/44-boltons_isoparse-r2/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/44-boltons_isoparse-r2/workspace/.pico/runs/run_20261024-212955-ffccfe/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/44-boltons_isoparse-r2/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 47-boltons_splitlines-r2

- 功能验收：True；失败归因：`missing_reverification_or_incomplete_workflow`；工具调用：7。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/47-boltons_splitlines-r2/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/47-boltons_splitlines-r2/workspace/.pico/runs/run_20261024-213234-99f4aa/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/47-boltons_splitlines-r2/changes.diff`。
- no_successful_test_after_last_write

### 52-boltons_xfrange-r2

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：4。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/52-boltons_xfrange-r2/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/52-boltons_xfrange-r2/workspace/.pico/runs/run_20261024-213652-a0a9bd/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/52-boltons_xfrange-r2/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 53-boltons_isoparse-r1

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：7。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/53-boltons_isoparse-r1/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/53-boltons_isoparse-r1/workspace/.pico/runs/run_20261024-213735-f3c13d/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/53-boltons_isoparse-r1/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 56-humanize_size-r1

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：5。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/56-humanize_size-r1/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/56-humanize_size-r1/workspace/.pico/runs/run_20261024-214038-bb020a/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/56-humanize_size-r1/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

### 60-humanize_fractional-r2

- 功能验收：False；失败归因：`model_or_runtime_error`；工具调用：5。
- 原始记录：`artifacts/repair-suite-v1-2026-10-24/runs/60-humanize_fractional-r2/result.json`；轨迹：`artifacts/repair-suite-v1-2026-10-24/runs/60-humanize_fractional-r2/workspace/.pico/runs/run_20261024-214259-59dbc5/trace.jsonl`；差异：`artifacts/repair-suite-v1-2026-10-24/runs/60-humanize_fractional-r2/changes.diff`。
- TimeoutError: The read operation timed out
- test_01 (test_acceptance.RepairTests) ... FAIL

## 边界

- 20 unique tasks, 60 dependent repeated runs; do not treat as 60 independent tasks.
- 12 author-created tasks, including 2 previously used; 8 extracted real upstream faults from only 2 projects.
- Visible acceptance tests and minimal fixtures; no hidden holdout or full repository benchmark.
- External tests use fresh frozen copies; reference code never installed in an agent fixture.
- Tool approval and file hashes are engineering guards, not an OS-level sandbox.
- Provider may be nondeterministic; replay of saved evidence is exact, new model runs need not reproduce the score.
- Token totals are provider-reported observations, excluding unknown usage of transport failures; no fabricated zeroes.

## 复现

运行 `python scripts/run_repair_suite.py --check` 核对冻结资产。
使用新的输出目录执行 `python scripts/run_repair_suite.py --output artifacts/repair-suite-v1-reproduction`，并在启动前设置 `GIT_CEILING_DIRECTORIES` 为本项目 artifacts 的绝对路径。
用 `python scripts/audit_repair_suite.py --output <批次目录> --audit-output <新审计目录>` 重新验证保存结果。
原始模型轨迹和工作区保存在本地 artifacts，默认不进Git；审计JSON包含所有证据文件的SHA-256。
