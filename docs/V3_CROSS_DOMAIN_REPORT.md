# V3_CROSS_DOMAIN_REPORT — 线粒体跨域验证（P1.6）

> 日期：2026-10-18 ｜ 运行：`data/eval/g5_mito20/`（scripted planner，离线零模型调用）
> 复现：`scripts/eval_offline.py --candidates <V2 candidates> --limit 20 --match mito`

## 1. 目的与范围

G0–G5 的全部能力验证都在叶绿体域（chloroplast）。本报告把同一套 V3 Agent
（沙箱、工具合同、graph、composer、隔离评分）原样切换到**线粒体域
（vertebrate_mitochondrion）**，验证域合同与缺陷族的可迁移性。
按用户指示不重复 chloro 40 案例、不增加第三模型；planner 采用
ScriptedPlanner（LLM 跨域对比属可选后续，需另行授权 token）。

## 2. 样本与分母

| 项 | 值 |
|---|---|
| 样本 | 20 条 mito candidates（按 `--limit 20` 顺序取样） |
| 来源覆盖 | **5 个独立 accession**：NC_002333.2、NC_004389.1、NC_008106.1、NC_009587.1、NC_025767.1 |
| 条件族 | MITO-NORMAL / MITO-CDS / MITO-ID / MITO-META（每来源各 1，5 来源轮转） |
| 分母 | cases_total=20；graded=20；skipped_phase_a=0；not_mappable=0；phase_b_errors=0 |
| 判定（V2 冻结评分器） | **pass 20 / fail 0** |

## 3. 跨域对照小结

| 维度 | chloro（G4/G5 基线） | mito（本报告） | 结论 |
|---|---|---|---|
| 分母完整性 | 40/40 graded | 20/20 graded | 域合同（task.md + artifacts 四件套）跨域一致 |
| 缺陷族覆盖 | NORMAL/CDS/ID/META | 同四族 | composer 四条规则全部适用，无需域特判 |
| 评分 | 40 pass（LLM）/40 pass（scripted） | 20 pass | 判定跨域稳定 |
| 沙箱/私有隔离 | FORBIDDEN_NAMES 拒绝 | 同（同一沙箱代码路径） | 隔离不随域变化 |

## 4. 诚实边界

- scripted planner：跨域结论针对 **V3 的规则与工具层**（graph 路由、工具事实、composer、指针核验），不声称 LLM 跨域规划能力；LLM mito 对比属可选后续。
- 20 条为本地 candidates 池前 20 条（5 来源轮转），非随机抽样、非 held-out。
- 判定基于 V2 冻结评分器（deterministic_grader/v1），evidence_support/action_quality 维度按 V2 设计留待人工。
