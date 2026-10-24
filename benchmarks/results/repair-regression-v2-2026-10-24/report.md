# CodeTrail 运行问题修复：单独回归报告

本轮6道已知问题题目，每题2次，共12次。功能 **5/12**，完整流程 **5/12**。全部新运行保留并独立重测。

原始20题60次基线保持功能43/60、流程42/60，未覆盖或合并。本次选择已知问题题目，不是新的总体成功率或独立留出评测。
相同6题在旧基线中的18次记录：功能5/18，流程4/18；样本次数、运行时与超时均有变化，不能作单因素因果推断。

| 任务 | 功能通过/2 | 流程通过/2 |
|---|---:|---:|
| boltons_splitlines | 1/2 | 1/2 |
| boltons_xfrange | 0/2 | 0/2 |
| humanize_metric | 0/2 | 0/2 |
| humanize_size | 0/2 | 0/2 |
| inventory_reservation | 2/2 | 2/2 |
| utc_storage_contract | 2/2 | 2/2 |

## 修复与配置

- 普通进度文本不能作为最终回答。显式验证模式在最近一次修改后必须有指定命令的真实退出码0，失败/未测/过期时继续修复，预算耗尽或验证被拒绝时记录未完成。
- 校验命令仍通过正常工具调用及审批执行；没有自动批准命令或自动改测试。只读解释任务不强制执行shell。
- OpenAI兼容后端捕获读取超时、断连和不完整响应；最多3次传输尝试，重试前检查2倍配置超时的总预算，单次socket超时受剩余额度限制。401不重试，429使用有上限的等待。
- 每次传输的错误类别、次数、耗时、是否重试及可用request_id进入轨迹。失败传输用量保持未知。
- 请求超时由旧120秒改为180秒；模型、temperature0.2、20步预算和3072输出上限保持一致，单次任务上限1200秒。

冻结指纹：`85b3669931dda1baf99beb40445b88f11beac19ea65cfbc2dd8cc7b495731fc3`。原始任务和验收测试哈希保持不变。

## 观察到的机制行为

- 拦截不满足验证的最终回答0次，其中0次运行随后完成修复和验证。
- 传输尝试113次，失败尝试15次；涉及8次运行。未将失败传输的未知Token计为0。
- 1次运行在传输失败后重试并最终完成；7次最终因读取超时失败。有限重试不能保证外部服务可用。
- 本轮没有触发completion_rejected事件；门槛拦截行为由软件回归测试验证。
- 可观测输入/输出Token：212665/35860；全请求用量完整覆盖4/12次运行。
- 平均工具调用7.75次，平均执行及外部验证314.151秒。

## 逐次证据

- `01-humanize_size-r1`：功能False，流程False，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/01-humanize_size-r1/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/01-humanize_size-r1/workspace/.pico/runs/run_20261024-234450-83e3cd/trace.jsonl`。
- `02-humanize_metric-r2`：功能False，流程False，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/02-humanize_metric-r2/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/02-humanize_metric-r2/workspace/.pico/runs/run_20261024-234450-3768df/trace.jsonl`。
- `03-humanize_metric-r1`：功能False，流程False，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/03-humanize_metric-r1/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/03-humanize_metric-r1/workspace/.pico/runs/run_20261024-234450-3c0b57/trace.jsonl`。
- `04-utc_storage_contract-r1`：功能True，流程True，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/04-utc_storage_contract-r1/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/04-utc_storage_contract-r1/workspace/.pico/runs/run_20261024-235122-4763a2/trace.jsonl`。
- `05-boltons_splitlines-r1`：功能True，流程True，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/05-boltons_splitlines-r1/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/05-boltons_splitlines-r1/workspace/.pico/runs/run_20261024-235131-d50d73/trace.jsonl`。
- `06-boltons_splitlines-r2`：功能False，流程False，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/06-boltons_splitlines-r2/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/06-boltons_splitlines-r2/workspace/.pico/runs/run_20261024-235141-89ade9/trace.jsonl`。
- `07-inventory_reservation-r1`：功能True，流程True，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/07-inventory_reservation-r1/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/07-inventory_reservation-r1/workspace/.pico/runs/run_20261024-235300-211829/trace.jsonl`。
- `08-utc_storage_contract-r2`：功能True，流程True，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/08-utc_storage_contract-r2/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/08-utc_storage_contract-r2/workspace/.pico/runs/run_20261024-235359-8857a1/trace.jsonl`。
- `09-boltons_xfrange-r2`：功能False，流程False，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/09-boltons_xfrange-r2/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/09-boltons_xfrange-r2/workspace/.pico/runs/run_20261024-235517-e2dd68/trace.jsonl`。
- `10-boltons_xfrange-r1`：功能False，流程False，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/10-boltons_xfrange-r1/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/10-boltons_xfrange-r1/workspace/.pico/runs/run_20261024-235824-6b9501/trace.jsonl`。
- `11-humanize_size-r2`：功能False，流程False，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/11-humanize_size-r2/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/11-humanize_size-r2/workspace/.pico/runs/run_20261025-000142-153fda/trace.jsonl`。
- `12-inventory_reservation-r2`：功能True，流程True，拒绝结束0次；原始记录 `artifacts/repair-regression-v2-2026-10-24/runs/12-inventory_reservation-r2/result.json`；轨迹 `artifacts/repair-regression-v2-2026-10-24/runs/12-inventory_reservation-r2/workspace/.pico/runs/run_20261025-000254-f7dafd/trace.jsonl`。

## 限制与复现

Six previously observed problem tasks, two new runs each, combined runtime+timeout change. Not independent holdout, not a rerun of all20 tasks; do not replace the original60 or infer a causal effect from rates.

超时恢复是有限重试机制，不能保证服务可用；若外部服务持续失败，任务仍会明确失败。重试可能增加等待和计费，失败请求用量未知。
全量软件测试结果另见同目录JUnit。复现新批次使用 `python scripts/run_repair_regression_v2.py --output artifacts/recovery-v2-new`，输出目录不可覆盖。
