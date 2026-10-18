# V3.1 Final Release Audit（发布前最终审计）

> 完成：2026-10-18 ｜ 审计对象：commit `7e330ab`（S2 正式实验完成态）
> 纪律：本轮 **零付费 API 调用**；冻结实验结果未修改；**未发布 promptv2**（active 保持 promptv1）。

## 1. P0：最终实验分析核对 — PASS

证据：`data/eval/s2_experiment/release_audit_p0.json`

### 双口径指标（如实分列）

| 口径 | 观测 | succeeded | judgment pass（graded 分母） | qc_pass_on_fault | 无效指针观测 | tokens | reqs |
|---|---:|---:|---:|---:|---:|---:|---:|
| **首次运行** | 120 | 119 | 119/119 | 0 | 1 | 672,830 | 302 |
| **repair 后有效** | 120 | 120 | 120/120 | 0 | 1 | 678,916 | 303 |

- 唯一差异格：`glm_B × NC_088771_1_CHLORO-ID-01`（首次 503 infra 失败 → repair succeeded/block）。
- **计费口径**：两次尝试全部入账（672,830 + 8,610 = **681,440 tok / 305 req**，见 `V3_S2_BUDGET_RECEIPT.json`）；有效口径为替换制（678,916）——两者均如实披露，不混淆。
- 协议合规：infra 失败零自动重试入账本 ✓；repair 有界单次、`repairs_original` 链接、原始不覆盖 ✓；配对质量分母 = 双臂均可评的 40 对 ✓；120 位置全在账本 ✓。

### 显著性语言扫描 — CLEAN

评测报告无 "统计显著/显著提升/significantly better/p≤0.0x" 表述；天花板效应已披露；结论语言为 "40 对 DEV 切片上的方向性证据（1 例差异），不构成显著性"。**ACCEPT_CANDIDATE 未把探索性结果写成统计显著提升。**

## 2. P1：补充回归测试 — 8 项新增，全部通过

提取可测模块：`src/genomeqc_agent/evolution/budget.py`（BudgetGuard + `from_ledger` 播种 + `ledger_billing`）、`src/genomeqc_agent/eval/safety.py`（cfp 契约 + 真实运行守卫）。`tests/unit/test_release_audit.py`：

| # | 契约 | 测试 |
|---|---|---|
| 1 | 跨进程恢复后预算从历史账本恢复（observation+repair 都计费；播种后仍守上限） | `test_budget_guard_restores_from_ledger`、`test_budget_guard_empty_ledger_is_zero` |
| 2 | cfp = qc_decision=pass on fault；judgment 永不作为输入 | `test_critical_false_pass_requires_qc_pass_on_fault_case`、`test_grader_judgment_pass_is_never_a_false_pass_input` |
| 3 | ScriptedPlanner 不可冒充真实运行（provider=fake 可机检；usage 空 ⇒ 非真实） | `test_scripted_planner_is_identifiable_and_not_a_real_run`、`test_real_llm_usage_is_recognised` |
| 4 | repair 独立 kind、`repairs_original` 链接、原始失败与计费保全、双次计费 | `test_repair_rows_are_separate_and_original_preserved`、`test_ledger_billing_counts_observation_and_repair` |
| 5 | 模拟审批（real=False）阻断发布；真实收据才解锁；active 不变 | `test_simulated_approval_cannot_trigger_publish` |

runner 重构：`run_s2_experiment.py` 改用共享 `BudgetGuard`（行为等价，消除双实现漂移风险）。

## 3. P2：发布状态机核查 — 9/9 PASS

证据：`data/eval/s2_experiment/release_audit_p2.json`

- promptv2 注册且 SHA256 与**全部 B 臂收据**一致 ✓
- active = promptv1（未动）✓；候选 status_cap=validated ✓；无 published 收据 ✓
- 全部审批收据 real=False（模拟）✓ → 发布门锁死
- S2 决策 = ACCEPT_CANDIDATE（待审批态）✓
- **已将 promptv2 候选在 manifest 中标记为 "DEV-verified, pending human approval"**（未晋级、未发布、active 未动）

## 4. P3：归档与版本冻结

- **S2 独立归档**：`data/archive/s2_experiment/`（248 文件，30.6 MB：账本/冻结计划/决策/预算收据/checkpoint/120 份原始报告/120 张评分卡）——gitignored 原始数据有目录级独立备份，非仅 Markdown 总结
- S1 归档此前已完成（41 文件），本轮按修正口径刷新评分审计副本
- [V3_1_RELEASE_CANDIDATE_MANIFEST.json](computer://d:/1_yanjiusheng/AssemblyGenomics-Copilot_V3/V3_1_RELEASE_CANDIDATE_MANIFEST.json)：RC commit、161 tests、ruff/secrets 状态、S1+S2 双归档清单、冻结交付物清单、策略状态、回滚方法

## 5. 最终检查

- pytest **161 collected / exit 0**（145 + 8 release-audit + 8 用户侧新增）
- ruff clean（src/tests/scripts/ui）· secrets CLEAN · git 工作树干净（本审计提交后）

## 6. GO / NO-GO

## **GO**（V3.1 Release Candidate 达成）

发布候选成立：真实实验闭环 + 冻结门槛判定 + 审计通过 + 独立归档 + 回归测试加固。

### 仍需用户人工批准的事项

| # | 事项 | 说明 |
|---|---|---|
| 1 | **promptv2 发布审批** | ACCEPT_CANDIDATE → 发布需用户真实身份 `record_approval(real=True)` 批准；**不批则 active 恒为 promptv1**。审批时请权衡：B 以 +14.9% tokens 换取幻觉指针 1→0（40 对方向性证据） |
| 2 | 飞轮 2 条候选人工评审 | 幻觉指针（glm_A）与 503 infra 候选待评审，评审后才晋级 formal_regression |
| 3 | 独立 holdout 泛化测试（可选） | 现有结论限 chloro DEV 40 案例；mito/新切片需另行授权与协议 |
| 4 | SiliconFlow key 轮换（用户已豁免，仍建议） | 免费豁免的持续有效性由用户判断 |

### 已知限制

1. DEV 切片天花板效应：judgment 层无判别空间，B 的价值证据集中在 evidence_validity 层且仅 1 例差异（方向性，非统计显著）。
2. S2 曾发生一次外部中断 → 断点恢复（零重复计算），相关路径已加回归测试。
3. 首版审计与分析脚本各有一处判定缺陷（FAULT_CASES 前缀错配、cfp 误用 judgment），均已修正并以回归测试锁定；历史结论经修正口径重跑后不变。
