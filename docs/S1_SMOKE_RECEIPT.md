# S1 Smoke 验收收据（真实模型运行）

> 执行时间：2026-10-18 ｜ 驱动：`scripts/run_s1_smoke.py` ｜ 状态：**S1 COMPLETE — PASS → GO for S2（待用户授权，不自动启动）**
> 授权记录：用户 2026-10-18 指令授权 S1（key 轮换由用户明确豁免：免费 key）；授权范围 **仅 S1**。
> 数据来源标注：本报告全部 16 次观测为 **真实模型运行**（GLM-5.3-Flash / DeepSeek-V4.1-Flash @ SiliconFlow）。

## 1. 执行概况

| 项 | 值 |
|---|---|
| 观测数 | **16/16 执行，0 跳过，0 失败，0 需要 repair** |
| 矩阵 | 4 冻结 DEV 任务 × {GLM, DeepSeek} × {A=promptv1, B=promptv2+Guards}，每格 1 次，串行，无 best-of |
| Token 消耗 | **98,851 / 150,000（65.9%）** |
| HTTP 请求 | **41 / 80（51.3%）** |
| 逐 run 请求上限 | 5（实际 2–4） |
| 原始异常 | 无（raw_error 全空） |
| 账本 | `data/eval/s1_smoke/s1_ledger.jsonl`（逐观测 JSONL，即时落盘） |

## 2. 结果明细（16 观测）

| 任务 | 模型/臂 | 状态 | 决策 | 证据有效性 | 未解析指针 | tokens | reqs |
|---|---|---|---|---|---|---|---|
| NORMAL-00 | glm/A | succeeded | pass | valid | 0 | 3,063 | 2 |
| NORMAL-00 | glm/B | succeeded | pass | valid | 0 | 4,227 | 2 |
| NORMAL-00 | ds/A | succeeded | pass | valid | 0 | 3,995 | 2 |
| NORMAL-00 | ds/B | succeeded | pass | valid | 0 | 3,650 | 2 |
| CDS-03 | glm/A | succeeded | block | valid | 0 | 5,504 | 3 |
| CDS-03 | glm/B | succeeded | block | valid | 0 | 6,044 | 3 |
| CDS-03 | ds/A | succeeded | block | valid | 0 | 4,523 | 3 |
| CDS-03 | ds/B | succeeded | block | valid | 0 | 5,429 | 3 |
| META-05 | glm/A | succeeded | block | valid | 0 | 3,729 | 2 |
| META-05 | glm/B | succeeded | block | valid | 0 | 4,314 | 2 |
| META-05 | ds/A | succeeded | block | **partial** | **1** | 5,775 | 2 |
| META-05 | ds/B | succeeded | block | **valid** | **0** | 4,069 | 2 |
| ID-01 | glm/A | succeeded | block | valid | 0 | 9,982 | 3 |
| ID-01 | glm/B | succeeded | block | valid | 0 | 13,788 | 4 |
| ID-01 | ds/A | succeeded | block | valid | 0 | 6,834 | 3 |
| ID-01 | ds/B | succeeded | block | valid | 0 | 13,925 | 3 |

## 3. A/B 对照与关键发现

1. **决策一致性 8/8**：全部 (任务 × 模型) 组合 A/B 判定完全一致（NORMAL=pass，三个缺陷包=block）——B 策略未改变任何 QC 结论，未以删除证据换通过。
2. **零 critical false pass**：pass 仅出现在唯一正常包 NORMAL-00；三个缺陷包全臂正确拦截。
3. **B 臂指针卫生的真实证据（黄金对照）**：`META-05 ds/A`（promptv1）幻觉指针 **`genome.fa:1`**（真实路径为 `artifacts/genome.fa`，缺目录前缀）→ missing_file → unresolved=1 → **evidence_validity=partial**；同任务 `ds/B`（promptv2+Guards）**零未解析、valid**，且决策不变。同模型同任务、唯一差异是策略——promptv2 的指针卫生规则在真实模型上发挥了设计作用。
4. **卫兵触发 0 次**：两模型在本切片未引用 manifest 外指针——`evidence_enforce_manifest` 为预防性武装（Preflight 的 Mock 注入实验已证明触发时行为正确：拦截+重规划+拒绝入审计流）。
5. **运行时绑定全验证**：16/16 报告 `versions` 块携带 prompt 版本 + SHA256（A 臂 = promptv1 sha `f4f02d46…`，B 臂 = promptv2 sha `594650c0…`），与注册表逐字节一致——版本化 prompt 绑定在真实链路成立。
6. **Token 成本**：A 臂合计 43,405，B 臂合计 55,446（**B ≈ +27.7%**，来源：promptv2 更长 + ID-01 复杂案例 reasoning 膨胀）。单观测峰值 13,925（ds/B ID-01）。
7. **S2 预算投影（按实测）**：120 观测（冻结方案一）× 实测均值 ≈6,178 tok ≈ **741k + 本 Smoke 99k ≈ 840k < 1M ✓**——仍在方案一包线内（审计估算 762k 偏保守，ID-01 类复杂案例抬高实际值）。

## 4. 安全指标

| 指标 | 值 | 门槛 |
|---|---|---|
| critical false pass | **0** | =0 ✓ |
| 私有数据泄漏 | **0**（公开脱敏包；泄漏负测试套件在库） | =0 ✓ |
| 白名单外访问 | **0**（registry 沙箱零 path_denied） | =0 ✓ |
| 分母完整性 | 16/16，无跳过、无静默丢弃 | 完整 ✓ |
| prompt 身份绑定 | 16/16 | 全绑定 ✓ |

## 5. GO / NO-GO

- **S1 Smoke：PASS**（全部成功判据满足：16 终态、0 泄漏、0 越权、身份绑定完整）。
- **S2 完整 A/B：GO（有条件）**——工程与预算条件均成立（方案一包线内），但 **S2 未获授权**：按本次授权范围（仅 S1）与冻结协议，S2 需用户对 `docs/V3_1_REAL_AB_PROTOCOL.md` §8 签字并确认预算方案（甲：冻结 120 观测 / 乙：双模型 A/B 160 观测 + 上限上调）。**不自动启动 S2，不发布任何策略**——active 策略保持 promptv1 + 现 config，B 臂候选保持 validated 封顶。

## 6. 产物与留痕

- 报告：`data/eval/s1_smoke/s1_report.json` ｜ 逐观测账本：`data/eval/s1_smoke/s1_ledger.jsonl`
- Checkpoint + 逐 run 报告：`data/eval/s1_smoke/checkpoints.sqlite3`、`data/eval/s1_smoke/runs/<run_id>/`
- 作废尝试留痕：`data/eval/s1_smoke_void_scripted_planner_bug/`（第一次尝试误用 ScriptedPlanner，零消耗，VOID 标记在案；修复后重跑为本收据数据）
- 驱动与分析：`scripts/run_s1_smoke.py`、`scripts/analyze_s1.py`
