# V3 S2 评测报告（方案甲 · 真实模型运行）

> 完成：2026-10-18T11:54:35+00:00 ｜ 数据来源：**真实模型运行**（120 观测账本 V3_S2_RUN_LEDGER.jsonl）｜ 评分：organelle_v2.rubric.deterministic_grader/v1（冻结，V2 venv 隔离）

## 1. 分母

- 计划 120 / 实际入账 120（glm_A 40，glm_B 40，ds_A 40）；预算跳过 0；**分母完整**

## 2. 三臂指标总表

| 臂 | 观测 | 完成 | judgment pass | verdict 正确 | 契约正确 | 定位均值 | 无效指针观测 | partial | critical false pass | tokens (均值) | reqs | 均延迟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| GLM A (promptv1) | 40 | 40 | 40/1.000 | 40/1.000 | 40/1.000 | 2.0 (n=30) | 1 (0.025) | 1 | 0 | 206999 (5175/obs) | 102 | 49.4s |
| GLM B (promptv2+guards) | 40 | 40 | 40/1.000 | 40/1.000 | 40/1.000 | 2.0 (n=30) | 0 (0.000) | 0 | 0 | 237759 (5944/obs) | 99 | 59.9s |
| DeepSeek A (参考) | 40 | 40 | 40/1.000 | 40/1.000 | 40/1.000 | 2.0 (n=30) | 0 (0.000) | 0 | 0 | 234158 (5854/obs) | 102 | 38.9s |

## 3. GLM A/B 配对分析（同案例同模型）

- judgment_pass：A 40/40 (1.000, CI 0.912-1.000) → B 40/40 (1.000, CI 0.912-1.000)；仅 B 好 0，仅 A 好 0
- verdict_correct：A 40/40 (1.000, CI 0.912-1.000) → B 40/40 (1.000, CI 0.912-1.000)；仅 B 好 0，仅 A 好 0
- output_contract_ok：A 40/40 (1.000, CI 0.912-1.000) → B 40/40 (1.000, CI 0.912-1.000)；仅 B 好 0，仅 A 好 0
- no_invalid_pointer：A 39/40 (0.975, CI 0.871-0.996) → B 40/40 (1.000, CI 0.912-1.000)；仅 B 好 1，仅 A 好 0
- tokens/obs：A 5175 → B 5944 (Δ +769)

## 4. 进化决策

- **ACCEPT_CANDIDATE**（ACCEPT = 达到待审批条件，**不等于批准发布**；active 策略保持 promptv1）

- 主指标门槛（B ≥ A）：

  - judgment_pass_rate_B_ge_A: PASS
  - verdict_correct_rate_B_ge_A: PASS
  - contract_rate_B_ge_A: PASS
  - loc_mean_B_ge_A: PASS
  - invalid_pointer_ratio_B_le_A: PASS
- 安全门槛：

  - critical_false_pass_zero: PASS
  - no_grader_errors: PASS

## 5. DeepSeek 参考基线（非 A/B 臂）

- judgment pass 40/40；无效指针 0 观测；tokens 234158——仅作稳定性/一致性参考

## 6. 天花板效应说明

- 本 DEV 切片 qc_decision 与 judgment pass 均接近满分（40 案例 G4 修复后基线强），判别力集中在**证据可靠性（无效指针比例）、执行效率（tokens/延迟）与定位质量**上。

详细逐观测：`V3_S2_RUN_LEDGER.jsonl`；决策：`V3_S2_EVOLUTION_DECISION.json`；预算：`V3_S2_BUDGET_RECEIPT.json`。
