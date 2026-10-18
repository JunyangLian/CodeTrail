# V3 S1 最终评分审计（P0 · 冻结 grader 全量重评）

> 执行：2026-10-18 ｜ 脚本：`scripts/audit_s1_scores.py` ｜ 数据：`data/eval/s1_score_audit/s1_score_audit.json`
> 评分器：`organelle_v2.rubric.deterministic_grader/v1`（冻结，V2 venv 隔离子进程，私有金标不出 V2）
> 数据来源：S1 的 16 次观测均为**真实模型运行**；本审计为**历史记录重评**。

## 1. 审计结论

**PASS**（无 grader 错误、无账本不一致、无 Oracle 泄漏症状、A/B 配对完整 8/8）→ S2 启动条件满足。

## 2. 重评结果（16/16 graded，0 不可映射）

| 审计项 | 结果 |
|---|---|
| 输出格式契约 | **16/16 OK** |
| verdict 正确性（verdict_score） | **16/16 = 1**（NORMAL 正确 pass，缺陷包正确 block，与私有金标一致） |
| 缺陷定位（defect_localization） | 12 个适用观测全部 **2/2**（"every declared token and file located"）；4 个 NORMAL 不适用 |
| 证据指针解析（grader 所见） | 全部可解析（findings 引用均为 facts 派生） |
| critical false pass | **0**（修正口径：缺陷包上 judgment=pass 且 qc=block = 报告合格，非漏报；漏报定义为 qc=pass on fault，实测 0） |
| A/B 完整性 | 8 对 (案例 × 模型) × 2 臂全在 |
| Prompt 版本 + SHA256 | 16/16 与账本、注册表一致（promptv1 `f4f02d46…` / promptv2 `594650c0…`） |
| Token/HTTP 账本一致性 | 16/16 checkpoint 重算与账本相等 |

## 3. 概念三分（用户要求的明确区分）

| 概念 | 含义 | S1 分布 |
|---|---|---|
| `qc_decision` | V3 工具事实层结论 | pass 4（全在 NORMAL）/ block 12 |
| `judgment` | 冻结评测层（报告契约+定位+证据质量 vs 私有金标） | 16/16 pass |
| `evidence_validity` | 机械指针解析 | 15 valid + 1 partial（ds/A META-05） |

三概念独立成立：ds/A META-05 同一观测 qc=block、ev=partial、judgment=pass——工具层正确拦截缺陷、指针层存在幻觉、评测层报告合格，互不混同。

## 4. META-05 DeepSeek A/B 专审

| | ds/A（promptv1） | ds/B（promptv2+guards） |
|---|---|---|
| qc_decision | block | block |
| evidence_validity | **partial** | **valid** |
| judgment | pass | pass |
| verdict_score | 1 | 1 |
| defect_localization | 2/2 | 2/2 |
| 幻觉指针 | **`genome.fa:1`**（真实路径 `artifacts/genome.fa`，缺 `artifacts/` 前缀 → missing_file） | 无 |

**审计结论**：A/B 差异真实存在于机械指针解析层（promptv2 指针卫生的直接效果），不影响评测层判定（grader 所见 evidence refs 全部来自工具事实，幻觉指针未进入 findings 引用）——这正是"指针可解析 ≠ 结论正确"与"证据可靠性独立价值"的实证。

## 5. 审计中修正的检查缺陷（如实记录）

- 首版审计脚本的 `FAULT_CASES` 集合带了 `CHLORO-` 前缀而匹配用后缀 → critical false pass 恒 0（检查失效）。已修正为无前缀匹配 + 正确定义（cfp = qc_decision=pass on fault）。修正后重跑：cfp 仍为 0（结论不变，逻辑已可证）。
- 同一口径修正同步应用于 S2 分析脚本（`scripts/analyze_s2.py`）。
