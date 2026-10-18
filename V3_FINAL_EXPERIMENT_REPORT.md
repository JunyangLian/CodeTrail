# V3 FINAL EXPERIMENT REPORT — S1 Smoke + S2 正式实验（方案甲）

> 完成：2026-10-18 ｜ 提交链：`…89c96e8` → `54e6cbd`/`c3cce73`(S1) → 本轮
> 数据来源标注：S1 16 观测与 S2 120 观测均为 **真实模型运行**；评分全部出自冻结 grader
> （organelle_v2.rubric.deterministic_grader/v1，V2 venv 隔离进程）；**全程未启用 Self-Refinement / Data Flywheel flags**。

## 1. 累计消耗（权威账本）

| 阶段 | 观测 | tokens | HTTP 请求 |
|---|---:|---:|---:|
| S1 Smoke | 16/16 | 98,851 | 41 |
| S2 正式（方案甲） | 120/120 | 681,440（含 1 例分列 repair 8,610） | 305 |
| **项目累计** | **136** | **780,291 / 1,000,000（78.0%）** | 346 / 390+ |

- 计量口径：`usage.total_tokens` 逐成功调用加总；reasoning 计入 completion 一次；失败请求计 0 token；grader 子进程零模型消耗。
- 预算事件：S2 进程曾于观测 111 被外部中断杀死 → **断点恢复**（110 格从账本跳过，守卫从账本播种），零重复计算、零丢失。

## 2. S2 冻结矩阵执行情况

| 臂 | 配置 | 观测 | 完成 | 判定 |
|---|---|---:|---:|---|
| glm_A | GLM-5.3-Flash + promptv1 | 40 | 40 | pass 4 / block 36 |
| glm_B | GLM-5.3-Flash + promptv2+Guards | 40 | 39 + 1 分列 repair | pass 4 / block 36 |
| ds_A | DeepSeek-V4.1-Flash + promptv1（参考） | 40 | 40 | pass 4 / block 36 |

- 原始失败 1 例（glm_B × NC_088771_1_CHLORO-ID-01，provider 503 容量错误）按冻结协议**分列 repair**：重跑 succeeded（block, 8,610 tok），原始失败保留在账本，未覆盖。
- 分母：120/120 全部入账（含失败与 repair），预算跳过 0。

## 3. GLM A/B 配对分析（40 对，主指标）

| 指标（配对） | A | B | 仅 A 好 | 仅 B 好 | 结论 |
|---|---|---|---:|---:|---|
| judgment pass | 40/40 | 40/40 | 0 | 0 | 平（CI 同 [0.912,1.000]） |
| verdict 正确 | 40/40 | 40/40 | 0 | 0 | 平 |
| 输出契约正确 | 40/40 | 40/40 | 0 | 0 | 平 |
| defect_localization | 2.0（满） | 2.0（满） | 0 | 0 | 平 |
| **无效指针观测** | **1（partial 1）** | **0** | — | — | **B 优**（幻觉指针 0） |
| tokens/观测 | 5,175 | 5,944 | — | — | B +14.9% |
| 均延迟 | — | — | — | — | B 略高（reasoning 更长） |

- **天花板效应确认**：qc_decision 与 judgment 层 40/40 满分（G4 修复后基线强），判别力集中在证据可靠性与效率。
- **证据可靠性**：A 臂 1 例幻觉指针（`artifacts/genome.fa` 前缀缺失类）→ partial；B 臂同切片 **0 例**——promptv2 指针卫生规则在 40 例规模上复现了 S1 的方向性结论。
- **无证据删除**：两臂 findings 逐案例等价，B 未以删证据换通过。

## 4. DeepSeek 参考基线（非 A/B 臂）

- judgment pass 40/40、verdict 正确 40/40、契约 40/40、无效指针观测 1（S1 同源现象：`genome.fa:1` 类前缀幻觉）、tokens 217,751——与 GLM 臂同量级稳定性，仅作参考。

## 5. 证据验证表现（三概念分离）

| 概念 | S2 结果 |
|---|---|
| `qc_decision`（工具事实层） | 40 案例：4 NORMAL=pass、36 缺陷=block，**三臂全部正确** |
| `judgment`（冻结评测层） | 120/120 全 pass（报告契约+定位+证据合格），0 grader error |
| `evidence_validity`（机械解析层） | 119 valid + 1 partial（glm_A 幻觉指针）；needs_human_review 0（guard3 未开臂无未解析） |

## 6. 进化决策（冻结门槛自动判定）

> **ACCEPT_CANDIDATE**（详见 `V3_S2_EVOLUTION_DECISION.json`）

- 主指标门槛 B ≥ A：judgment ✓ / verdict ✓ / 契约 ✓ / 定位 ✓ / 无效指针 B≤A ✓
- 安全门槛：critical false pass = 0 ✓（修正口径：模型对缺陷包正确 block，judgment=pass 是报告合格，非漏报）、泄漏 0 ✓、越权 0 ✓
- 分母完整 ✓
- **ACCEPT_CANDIDATE 仅代表达到待审批条件，不等于批准发布**。active strategy 保持 **promptv1**，未执行任何发布路径；B 候选状态进入待审批，发布需用户 `record_approval(real=True)` 真实身份批准。

## 7. Self-Improvement 真实验证结论

1. **策略管线真实成立**：真实失败 → 归因 → 受限候选 → 版本化 prompt（SHA256 绑定逐 run 可验证）→ 真实 A/B → 冻结门槛 → 状态机裁决，全链在真实模型上跑通。
2. **候选的价值是真实的且被正确度量**：B 臂以 +14.9% tokens 换取幻觉指针 1→0（40 例规模）与同层持平的全部质量指标。
3. **安全机制全程有效**：零越权、零泄漏、503 零自动重试、失败如实入账。
4. **发布决定权保留在人**：本轮未发布、未升级 active。

## 8. Data Flywheel 可纳入的新增错误候选（已入待审队列，candidate 态）

| 候选 | 来源 | 机制 | 状态 |
|---|---|---|---|
| `FW-83c767ea…` | s2_real_ab_trace（glm_A × NC_088757_1） | 幻觉/缺失前缀指针 → partial evidence | pending_review（candidate） |
| `FW-609b473a…` | s2_real_ab_trace（glm_B × NC_088771_1） | provider 503 容量失败 | pending_review（candidate） |

均带 source_group、run_id、input 哈希位、prompt 版本；**无用户上传数据**；未经人工评审不晋级。

## 9. 剩余风险与未完成项

1. **B 发布未决**：ACCEPT_CANDIDATE 待用户真实审批；不批则 active 恒为 promptv1。
2. **+14.9% token 成本**：B 臂换证据卫生的代价，发布决策需用户权衡。
3. **单切片单域**：全部结论限于 chloro DEV 40 案例；mito/独立 holdout 泛化未测（不得宣称）。
4. **天花板效应**：judgment 层无判别空间，B 的价值证据集中在 evidence_validity 层（1 例差异，统计功效有限——40 对中仅 1 例差异，不构成显著性，仅方向性证据）。
5. 飞轮 2 条候选待人工评审。
6. SiliconFlow key 未轮换（用户已豁免）。

## 10. 完整性检查

- pytest **145 passed** / ruff clean（src/tests/scripts/ui）/ secrets CLEAN / git 工作树干净（本报告提交后）
- 原始数据全保留：S1（含 VOID 首误）+ S2 checkpoint/账本/评分卡；V2 v2.1.1 冻结零覆盖
- 八项交付物：`V3_S1_FINAL_SCORE_AUDIT.md`、`V3_S1_ARCHIVE_MANIFEST.json`、`V3_S2_FROZEN_PLAN.json`、`V3_S2_RUN_LEDGER.jsonl`、`V3_S2_EVALUATION_REPORT.md`、`V3_S2_EVOLUTION_DECISION.json`、`V3_S2_BUDGET_RECEIPT.json`、本报告
