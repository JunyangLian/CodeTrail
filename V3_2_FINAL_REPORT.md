# V3.2 FINAL REPORT — Agent Harness 深度增强与真实评测

> 完成：2026-10-18 ｜ RC commit：见 `V3_1_RELEASE_CANDIDATE_MANIFEST.json` 之后链（`32fa173` P0-P2 / `b1ec4fa` P3 / `27677c5` P4 / 本报告提交）
> 数据来源三态标注贯穿：P4 144 观测 = **真实模型运行**；归因回归 = **真实历史故障重放**；Builder/卫兵测试 = **Mock Simulation**

## 1. 总判定

| 目标 | 判定 |
|---|---|
| Agent 能力深度（上下文管理/预算/保护/溯源） | **DONE**（ContextBuilder v1 + 7 测试；真实 72 B 臂观测 receipt 100% full） |
| 评测归因粒度（8 类/4 级证据/干预实验） | **DONE**（归因 2.0 + 8 个真实故障诊断回归 + 单因素干预设计落地） |
| Harness 稳健性（10 领域故障注入） | **DONE**（含 1 个真实缺陷修复：缺失包目录崩溃 → 安全失败） |
| 真实重复 A/B | **DONE**（144/144 真实观测，20 并行执行，账本完整） |
| 独立来源验证 | **PARTIAL**（无合格 holdout 来源，未凑数） |
| 进化系统整合 | **DONE**（真实证据 → context 候选 validated，发布阻断实证） |
| 自动发布新策略 | **未发生**（active 保持 promptv1） |

## 2. 核心结果

1. **P0 审计**：planner 对工具返回值盲、无 Token 预算、无溯源、无替身防护——四缺口全部在 P1/P3 关闭；消息结构/检查点/版本绑定/评分器为 DONE 基线。
2. **ContextBuilder（contextv1）**：protected 块永不压缩、超预算显式 partial、摘要带溯源且"非原始证据"、关键信息前置、est/actual 分记、逐调用 receipt（SHA256）。flag 默认 OFF，V3.1 基线逐字节保留。
3. **归因 2.0**：规则只产假设（suspected），单因素干预才可升级；8 个真实故障（503/幻觉指针/预算重置/ScriptedPlanner 替身/cfp 语义/Locator/Schema/上下文压缩）全部有结构化诊断回归。模型不被无证据顶罪。
4. **真实 A/B**：144 观测全 succeeded；**质量层天花板（72/72 双臂）**；**无效指针 A=4 vs B=0**（3 倍采样复现 S1/S2 方向性结论）；B 成本 +12.7%（任务级 CI 不重叠）；重复一致性 100%；两次无效启动作废留痕（~13k tok）。
5. **稳健性**：注入测试发现并修复真实崩溃缺陷（缺失包目录），全负例安全失败 + 全轨迹。

## 3. 十份交付物

| 文件 | 状态 |
|---|---|
| `V3_2_CONTEXT_AUDIT.md` | DONE |
| `V3_2_CONTEXT_BUILDER_REPORT.md` | DONE |
| `V3_2_FAILURE_ATTRIBUTION_REPORT.md` | DONE |
| `V3_2_HARNESS_ROBUSTNESS_REPORT.md` | DONE |
| `V3_2_REAL_AB_PLAN.json` | DONE（冻结） |
| `V3_2_REAL_AB_LEDGER.jsonl` | DONE（144 观测 + shard 留痕） |
| `V3_2_REAL_AB_REPORT.md` | DONE |
| `V3_2_SOURCE_HOLDOUT_REPORT.md` | PARTIAL（无合格来源，未凑数） |
| `V3_2_EVOLUTION_INTEGRATION_REPORT.md` | DONE |
| `V3_2_FINAL_REPORT.md` | 本文档 |

## 4. 检查与归档

- pytest **169 collected / exit 0**（161 + 8 attribution/builder + robustness 批次）· ruff clean · secrets CLEAN · 历史收据零覆盖
- 原始数据 SHA256 归档：`data/archive/v32_ab/`（账本/计划/checkpoint/120+ 评分卡/分析 JSON）+ S1/S2 既有归档
- 账本一致性：144 观测 (case, model, condition, repeat) 键唯一，合并去重留痕（3-way→main 合并 +50）

## 5. 剩余事项（需用户决策）

1. **promptv2 发布审批**（V3.1 遗留，仍待批）
2. **contextv1 候选发布审批**（新：ACCEPT 级证据 = 幻觉指针 4→0 + 质量零回归 + +12.7% 成本）
3. holdout 来源构建（新 accession 数据工程）
4. 统计功效提升（可选：扩至 5 重复或增任务）

## 6. 诚实性声明

- 无 Mock 冒充真实（ctx receipt 逐观测可查；ScriptedPlanner 有机检判据 + 回归测试）
- 无重复冒充独立样本（任务级均值分析）
- 无单案例宣称稳定提升（144 观测 + 3 重复 + 方向性语言）
- 无规则分类冒充因果归因（evidence_level 四级 + 干预验证字段）
- 无自动发布（两条发布路径均停在待审批）
