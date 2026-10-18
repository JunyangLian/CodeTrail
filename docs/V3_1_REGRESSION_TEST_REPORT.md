# V3.1 回归测试报告（发布前加固）

> 完成：2026-10-18 ｜ 新增：`tests/unit/test_release_audit.py`（8 项，全过）
> 新增可测模块：`src/genomeqc_agent/evolution/budget.py`、`src/genomeqc_agent/eval/safety.py`
> runner 重构：`scripts/run_s2_experiment.py` 改用共享 BudgetGuard（消除双实现漂移风险）

## 1. 五项契约与测试映射

| # | 用户要求的契约 | 实现 | 测试（全过） |
|---|---|---|---|
| 1 | 跨进程恢复后全局预算从历史账本恢复 | `BudgetGuard.from_ledger`（observation+repair 全计费；播种后 reserve 仍守上限） | `test_budget_guard_restores_from_ledger`<br>`test_budget_guard_empty_ledger_is_zero` |
| 2 | cfp = 缺陷任务 qc_decision=pass；不得误用 judgment=pass | `eval.safety.critical_false_pass(qc_decision, case_id)` —— **函数签名不含 judgment**，结构上无法误用 | `test_critical_false_pass_requires_qc_pass_on_fault_case`<br>`test_grader_judgment_pass_is_never_a_false_pass_input` |
| 3 | ScriptedPlanner 不得冒充真实运行 | `eval.safety.is_real_model_run(model_usage)`：usage 空或全 0 token ⇒ 非真实；provider=fake 可机检 | `test_scripted_planner_is_identifiable_and_not_a_real_run`<br>`test_real_llm_usage_is_recognised` |
| 4 | Repair 不覆盖原始失败与原始计费 | 账本契约：repair 为独立 kind + `repairs_original` 链接；`ledger_billing` 对两类行都计费 | `test_repair_rows_are_separate_and_original_preserved`<br>`test_ledger_billing_counts_observation_and_repair` |
| 5 | 模拟审批不得触发正式发布 | 状态机硬门：approved 需 `real=True` 收据；模拟收据 raise | `test_simulated_approval_cannot_trigger_publish` |

## 2. 每项契约的回归价值（对应真实发现）

1. **预算恢复**：S2 观测 111 被外部中断杀死，重启进程曾出现守卫从零重算（cum 4600 而非 624,432）——当场修复；本测试将该修复永久锁死。
2. **cfp 定义**：首版分析曾把 grader `judgment=pass` on 正确 block 的缺陷包误计为 false pass（S1 审计与 S2 分析各一处）——修正后本测试以"签名不含 judgment"防止回归。
3. **ScriptedPlanner**：S1 第一次尝试因漏传 planner 参数导致 16 次 Mock 假运行（tok=0 暴露，VOID 留痕）——本测试提供机检身份与 usage 判据，防再犯。
4. **repair 契约**：S2 的 1 例 503 repair 实践了"分列保留"；本测试锁定账本结构（原失败行、原计费、repair 链接、双次计费）。
5. **审批门**：Phase 2 预检建立的 `real=True` 硬门；本测试以完整状态机路径（accepted → 模拟审批 → 阻断 → 真实审批 → 解锁）重申。

## 3. 测试执行

- `pytest tests/unit/test_release_audit.py`：**8 passed**
- 全量：**161 collected / exit 0**（145 + 8 release-audit + 8 用户侧新增）
- ruff clean；与既有 145 项零冲突（历史收据零覆盖）
