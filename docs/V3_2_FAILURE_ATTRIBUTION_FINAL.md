# V3.2 Failure Attribution Final（归因系统终验）

- 日期：2026-10-19（final round P2）
- 框架：Failure Attribution 2.0（`src/genomeqc_agent/evolution/attribution.py`，8 类 × 4 级证据）
- 产物：`data/eval/final_round/p2_attribution/attribution_final.json`（8 条结构化记录 + 消融附录）
- 新增真实实验：P2.2 消融 24 观测（4 焦点 (case, model) 对 × {C, D} 臂 × 3 重复）
- 基线 commit：`c30a4f3`

## 方法核验：不是关键词匹配

`attribute()` 的规则层只**播种假设**（`attribution_kind = "rule_seeded_hypothesis (NOT a causal verdict)"`），每条记录强制携带 12 字段证据链：observed_failure / first_layer_category / root_cause_hypothesis / supporting_evidence / alternative_explanations / confidence / intervention_verified / evidence_level / suggested_fix / regression_ref。升级到更高证据级必须经过 `apply_intervention()` 的**单因素受控实验**（factor+changed+outcome），多因素变更不得记为单组件因果。回归锁定：`tests/unit/test_attribution.py`（10 项，含 `test_intervention_experiment_upgrades_level_and_never_multifactor`）。

## 8 类历史失败的最终归因

| # | 失败类 | observed | 根因 | 干预证据 | attribution_status | 回归锁定 |
|---|---|---|---|---|---|---|
| 1 | SiliconFlow 503 | S2 观测 111 failed（`System is too busy`） | provider 端容量故障，无模型/代码责任 | 同格冻结协议 repair 重跑成功（原始失败保留） | **experimentally_supported**（infrastructure_error, high） | `test_budget_guard_restores_from_ledger`、`test_repair_rows_are_separate_and_original_preserved` |
| 2 | 无效证据指针 | A 臂 4/72 观测引用未解析指针（如 `artifacts/genome.fa` 无 locator）；上下文完整（receipt full） | 一层=model_capability（模型虚构指针）；干预证明上下文组织是可控因素 | V3.2 A/B 单因素：A=4/72 → B=0/72；**P2.2 消融：C=0/12 vs D=1/12（见下）** | **experimentally_supported**（context_missing_or_truncated per intervention upgrade） | `test_pointer_error_carries_alternatives`、`test_context_builder.py`×8 |
| 3 | 工具 Schema/参数错误 | **无真实实例**（S1/S2/V3.2 共 280 次付费观测 0 例 planner 契约违例） | 机制由 PlannedAction pydantic 校验 + policy gate 覆盖 | — | **no_field_instance_mechanism_tested** | `test_schema_error_has_single_factor_fix` |
| 4 | Checkpoint 恢复预算重置 | S2 断点恢复实测发现 guard 从零重算（可致预算失控） | agent_state_error：恢复路径未播种账本 | 修复=from_ledger 播种；真实 S2 恢复 + V3.2 恢复验证 | **confirmed**（代码级缺陷，确定性） | `test_budget_guard_restores_from_ledger`、`test_ledger_billing_counts_observation_and_repair` |
| 5 | ScriptedPlanner 替身混入 | S1 首跑 16 格零 token 假运行（runner 漏传 planner） | agent_state_error：harness 误用 | 全部 VOID 留痕 + 重跑；`is_real_model_run` 守卫 | **confirmed** | `test_scripted_planner_is_identifiable_and_not_a_real_run`、`docs/S1_SMOKE_RECEIPT.md` |
| 6 | Scorer false-pass 定义错误 | S1/S2 审计脚本 FAULT_CASES 前缀 bug（cfp 恒 0）+ judgment 与 qc_decision 混淆 | dataset_or_scorer_error：审计工具缺陷 | 定义修正（false pass ≡ qc_decision=pass on fault）+ 前缀修复 | **confirmed** | `test_critical_false_pass_requires_qc_pass_on_fault_case`、`test_grader_judgment_pass_is_never_a_false_pass_input` |
| 7 | Context 关键证据缺失 | node_build_packet 对缺失 packet_dir 抛 FileNotFoundError 崩溃（P3 注入测试发现） | agent_state_error：harness 未结构化失败 | 修复=OSError/KeyError → 结构化 safe fail；10 域注入回归 | **confirmed** | `tests/integration/test_harness_robustness.py` |
| 8 | 工具/模型返回内容异常 | GLM 强制 thinking 计入 max_tokens、enable_thinking=false 无效（2026-10-17 探针） | infrastructure/ provider 行为 | 网关配置单因素探针；网关检测截断显式报错 | **experimentally_supported** | `docs/G4_GLM_RECEIPT.md`、gateway.py |

诚实标注：#2 的一层分类是 model_capability_error（上下文完整时模型虚构指针），intervention 升级把**可控因素**定为 context 组织——不宣称"模型能力问题已解决"。#3 必须如实报告为"无真实实例"，不能拿合成测试冒充实战归因。

## P2.2 消融实验（区分信息缺失 / 组织方式 / 更多 Token / 随机波动）

**设计**（冻结于 `V3_2_P2_ABLATION_PLAN.json`，先于运行）：4 个焦点 (case, model) 对 = V3.2 A 臂全部 4 个无效指针事件所在格；两新臂，promptv1/工具/预算/评分器与 V3.2 P4 完全一致：

- **C 臂**（12 观测）：baseline raw-JSON 上下文 + ContextBuilder 的 protected `evidence_locators` 块逐字追加——**只加关键证据块，无任务头/无去重/无压缩/无位置控制** → 检验"证据块在场"是否足够。
- **D 臂**（12 观测）：baseline raw-JSON + 中性填充至 B 臂上下文尺寸（冻结目标 est：750/727/749/743）——**不含任何任务相关信息** → 检验"纯 token 体积"对照。

**结果**（`data/eval/final_round/p2_ablation/ablation_ledger.jsonl`，24/24 succeeded，0 repair）：

| 臂 | 内容 | 无效指针 | qc/judgment |
|---|---|---|---|
| A（历史，同 4 格） | raw-json | **4/12** | 72/72 满分（V3.2） |
| **C** | raw-json + 证据块 | **0/12** | 全部 correct |
| **D** | raw-json + token 等量填充 | **1/12** | 全部 correct |
| B（历史，同 4 格） | 完整 ContextBuilder | 0/12 | 全部 correct |

**判定**（按冻结决策表 + 诚实降级）：

- **方向**：C=0 vs D=1 支持"证据块在场"优于"纯 token 体积"；D 的 1 例说明填充不消除幻觉风险。
- **限制**：焦点格按 A 臂失败选出（选择偏倚），n=12/臂下 C vs D 的 0 vs 1 差异不具统计结论性；A 的 4 例本身是 1/3 重复率的随机事件（repeat 一致性 44/48）。
- **最终标注**：机制 = **evidence_block_presence_is_causal_directional**；attribution_status 保持 **experimentally_supported**（大规模 B 臂 0/72 为主要证据）；细粒度机制（证据块 vs token 体积）在 n=12/臂下**不宣称已独立区分**——若强行二选一须扩大样本，本轮不做。

## 回答总控问题

- 归因系统是否只是关键词匹配？**否**——规则只产 suspected，升级需单因素干预，替代解释强制携带，回归引用逐条落地（上表 7 条真实回归测试）。
- 无效指针的根因被证实了吗？**干预层面证实**（上下文组织可控地消除它：B 0/72、C 0/12），**细粒度机制方向性支持**（证据块 > token 体积），不宣称 confirmed。
- 消融是否扩大成新大型实验？**否**——24 观测，与冻结 V3.2 实验同配置，判定表先于运行冻结。
