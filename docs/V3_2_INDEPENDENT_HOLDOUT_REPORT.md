# V3.2 Independent Holdout Report（新来源独立泛化验证）

- 日期：2026-10-19（final round P3）
- 数据集：`data/eval/final_round/holdout_dataset/DATASET_MANIFEST.json`
- 运行账本：`data/eval/final_round/holdout_run/holdout_ledger.jsonl`（96 观测）
- 指标：`data/eval/final_round/holdout_run/holdout_metrics.json`
- 冻结计划：`V3_2_HOLDOUT_PLAN.json`（先于任何运行写入）
- 基线 commit：`c30a4f3`

## P3.1 来源获取与隔离

**这是 V3 项目第一次使用与 DEV 完全无关的来源构建评测集。**此前 V3.2 P5 的 holdout=PARTIAL（10 个来源族全部参与过 DEV 调参，未凑数）。本轮：

- NCBI eutils 检索（`chloroplast[Title] AND "complete genome"[Title] AND srcdb_refseq[PROP] AND Viridiplantae[Organism]`，2026-10-18），候选池 12,659 条，确定性选取 14 条候选（每属 1 条、长度 90–260 kb、排除 DEV 黑名单），下载 14 份 `.gbff`（原始字节 SHA256 + 下载时间入 `holdout_sources/sources_metadata.json`）。
- V2 冻结代码转换+生成（`genbank_to_packet/v1` → `factory.generator/v1`，domain=chloroplast）：**14/14 来源通过 baseline Oracle 校验与 chloroplast 域门，56 个候选任务，0 隔离，0 跳过模板**。
- **冻结选取规则**（先于运行声明）：按 accession 排序取前 8 来源；每来源 NORMAL-00 + CDS-03 +（偶数位 ID-01 / 奇数位 META-05）→ 8 正常 + 8 CDS + 4 ID + 4 META = **24 任务**。

### 隔离校验（全部 PASS）

| 检查 | 结果 |
|---|---|
| accession 不在 DEV 黑名单（10 来源） | PASS |
| 基因组**序列**不与任何 DEV 记录重复（216 份 DEV genome.fa，32 条不同序列） | PASS |
| accession 不在 V2 candidates/imported 池 | PASS |
| accession 不出现在 E2 回归池 / V3 failure pool 任何文本 | PASS |
| source_group id 无碰撞 | PASS |
| 属级多样性 | PASS：**8 个不同属**（Abies, Abrotanella, Acer, Acicarpha, Acronychia, Actaea, Actinodaphne, Adonis） |

每来源留存：accession 版本、organism、taxid、NCBI uid、gbff SHA256、下载时间、序列 SHA256（manifest `per_source`）。

## P3.3 冻结（运行前）

`DATASET_MANIFEST.json`：24 任务的逐文件 SHA256（公开 5 文件 + private 三件套）+ expected 标签（task_type/root_cause）+ 隔离记录 + `planned_observations=96`。运行器启动时逐文件重验哈希（`verify_manifest()`，任何不匹配即中止）。同批冻结：`V3_2_HOLDOUT_PLAN.json`（推理配置与 V3.2 P4 完全一致：temperature=0、max_tokens=8192、promptv1 sha256、工具契约 v1、deterministic_grader/v1、max_model_requests=5）+ 指标清单 + 分母规则。

## P3.4 运行

- **24 任务 × 2 模型（GLM-5.3-Flash / DeepSeek-V4.1-Flash）× 2 条件（A raw-json / B ContextBuilder v1）× 1 重复 = 96 次初始观测**。
- **执行序平衡修复**：V3.2 P1 审计披露 24/24 A-first；本轮按 case 奇偶交替，实测 **12 A-first / 12 B-first**。
- 窗口 2026-10-18T17:10–17:28 UTC（18 分钟，4 线程）；**96/96 succeeded，0 基础设施失败，0 repair 行**；总消耗 494,037 tokens。
- 结果查看后未做任何策略调整或重评。

## P3.5 结果（frozen metric list）

| 指标 | A（n=48） | B（n=48） |
|---|---|---|
| QC verdict 正确率 | **48/48 (1.0)** | **48/48 (1.0)** |
| 输出契约正确率 | 48/48 (1.0) | 48/48 (1.0) |
| judgment pass | 48/48 (1.0) | 48/48 (1.0) |
| 缺陷定位（32 fault 观测/臂） | **全 2 分（32/32 满分）** | **全 2 分（32/32 满分）** |
| 无效证据指针率 | **0/48**（Wilson 95% CI 0–7.4%） | **0/48**（Wilson 95% CI 0–7.4%） |
| critical false pass（fault 上 dec=pass） | **0** | **0** |
| 不可映射报告 | 0 | 0 |
| 均值 tokens | 4,891 | 5,401（+10.4%） |
| 均值 latency | 33.6 s | ~35 s |
| 均值模型请求 | 2.5 | 2.6 |
| B receipt completeness | — | 全部 full（无截断路径） |

分模型：glm 与 ds 各 24/24/双臂满分、0 无效指针。分来源组：8 组 × 6 观测全部 verdict 正确（两组别 tokens 见 metrics JSON）。

## 结论（区分四类声明）

| 声明类别 | 本轮证据 |
|---|---|
| **new-source holdout performance** | Agent（双臂）在 8 个全新属、24 个新任务上质量层满分（verdict/contract/judgment/定位全对），0 critical false pass，0 契约违约 |
| **evidence of generalization** | **成立（能力层）**：任务从未参与任何开发/调参/失败池/回归训练；生成器、Oracle、域门与 DEV 完全同一套受控流程，标签非人工写入 |
| **DEV improvement 的可迁移性** | **无法在本轮判别**：A 臂 DEV 无效指针率 5.6%（4/72）→ holdout 0/48，两 Wilson CI 重叠（0–7.4% vs 2.2–13.4%），且 B 在 DEV 的优势事件本身是低概率随机事件——**holdout 双臂双零，不能作为 ContextBuilder 优势的泛化证据，只能作为"无回归"证据** |
| **insufficient evidence** | 定位/契约/verdict 全饱和（天花板效应），holdout 无法区分双臂任何质量维度；臂间判别证据仍以 DEV 144 观测 + P2.2 消融为准 |

**样本量与不确定性**：n=48/臂、单次重复；按 DEV A 臂失败率 5.6% 计，48 观测观测到 ≥1 次失败的概率约 93%——A 臂 0/48 与 DEV 率不相容性中等偏弱（p≈0.065），不应过度解读为"A 在新来源上不再幻觉"；更可能的解释是失败率本身低且事件随机。

## 与 P5 决策的接口

- Holdout 为 **Agent 能力泛化**提供了此前缺失的独立证据（V3.2 P5 holdout=PARTIAL → 本轮 **ACHIEVED，24 任务 8 新属**）。
- ContextBuilder 的发布判据仍以 DEV 实验为准；holdout 贡献 = 独立来源上零回归 + 零安全事件。
