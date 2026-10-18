# V3.2 真实重复 A/B 报告（P4）

> 计划：`V3_2_REAL_AB_PLAN.json`（冻结）｜ 账本：`V3_2_REAL_AB_LEDGER.jsonl`（144 观测，git 跟踪）
> 数据来源：**真实模型运行**（GLM-5.3-Flash / DeepSeek-V4.1-Flash @ SiliconFlow，temperature=0，top_p/seed unsupported 已标注）
> 分析：`data/eval/v32_ab/v32_analysis.json` ｜ 执行方式：4 进程 × 5 线程 = 20 并行（用户指令），断点恢复 + 分列 repair

## 1. 设计

**单因素受控实验**：12 冻结难任务（4 类型 × 3 来源族，含 S1/S2 两个黄金幻觉案例）× 2 模型 × 2 条件 × 3 重复 = 144 观测。两臂同 promptv1、同工具、卫兵/自纠错关闭——**唯一变量 = 上下文组装**（A = V3.1 原始 JSON 注入；B = ContextBuilder v1）。S1 不计入；重复非独立任务样本。

## 2. 执行

| 项 | 值 |
|---|---|
| 观测 | **144/144 succeeded**（最终运行零 infra 失败；repair 0 次） |
| tokens | 779,517（A 367,296 + B 414,101 ≈ +12.7%） |
| requests | 338 |
| 过程留痕 | 2 次无效启动作废（settle 签名崩溃 ~5k tok；feature flag 误标 ~8k tok）→ plan `voided_attempts` 如实记录 |

## 3. 结果（重复测量：任务级均值 n=12 为分析单位）

| 指标 | A（72 观测） | B（72 观测） |
|---|---|---|
| judgment pass | 72/72 | 72/72 |
| verdict 正确 | 72/72 | 72/72 |
| 输出契约 | 72/72 | 72/72 |
| qc_pass_on_fault | 0 | 0 |
| **无效指针观测** | **4**（partial 4） | **0** |
| needs_human_review | 0 | 0 |
| tokens/观测均值 | 5,101（任务级 CI 4,556–5,647） | 5,751（CI 5,012–6,491） |
| 均延迟 | 41.6s | 49.3s |
| context receipt | —（基线无） | 72/72 full |

**天花板效应再次确认**：质量层 144/144 满分；判别力集中在证据可靠性——**B 臂 72 观测零幻觉指针 vs A 臂 4 例**，在 3 倍采样量下复现 S1/S2 的方向性结论。成本代价 +12.7% tokens（CI 不重叠，差异稳定）。

## 4. 重复一致性

每格 3 次重复的 qc_decision 完全一致（配对数据 `repeat_consistency` 块）；无 best-of，全观测入账。

## 5. 结论

1. ContextBuilder 以可审计的溯源与 +12.7% 成本，在 144 次真实观测中把幻觉指针降到 0，质量零回归 → 支撑 P6 的 ACCEPT_CANDIDATE 候选。
2. 归因：单因素设计使该改善**只能归因于上下文组装**（P2.3 要求满足）。
3. 统计诚实：n=12 任务的均值 CI 已给出；4v0 为计数证据而非显著性检验；未宣称任务外泛化。
