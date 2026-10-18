# V3.1 A/B 执行计划（Smoke → 完整 A/B，含人工门槛）

> 日期：2026-10-18 ｜ 状态：**待用户确认 Smoke 启动**（本轮零 API）
> 上游：`docs/V3_1_REAL_AB_PROTOCOL.md`（FROZEN）、`docs/V3_1_AB_PREFLIGHT_REPORT.md`（GO）
> 纪律：未经用户书面授权不调用任何付费模型 API；每个阶段结束强制人工门槛。

## 1. 阶段总览

```
[已完成] Phase 2 预检（零 API）
   │  mock A/B 链 4/4 终态 · 三开关行为差异实测 · 绑定证明 · 129 tests
   ▼
[S1] Smoke —— 16 runs（4 任务 × 2 模型 × 2 策略）        ← 需用户确认启动
   │  硬上限 150k tok / 80 req · 超限即熔断
   ▼
⛔ 人工门槛 1：审阅 smoke 收据 → GO / NO-GO 完整 A/B
   ▼
[S2] 完整 DEV A/B —— 2 臂 × 40 案例（GLM-5.3-Flash）     ← 需协议 §8 签字 + 1M 预算
   │  硬上限 400k tok/臂 · 总 1M tok（含 repair）
   ▼
[S3] 门槛判定 → 状态机流转 → 审批（真实身份）→ 发布/拒绝
   ▼
[S4]（未来，另行授权）独立 holdout 测试 —— 与 40 案例不相交的新切片
```

## 2. S1：Smoke（16 runs）

### 2.1 冻结矩阵

| 维度 | 值 |
|---|---|
| 任务（4） | `chloro_C1_NC_013991_2_CHLORO-NORMAL-00`、`chloro_C1_NC_013991_2_CHLORO-CDS-03`、`chloro_C1_NC_013991_2_CHLORO-META-05`、`chloro_C1_NC_082429_1_CHLORO-ID-01`（均 NC_013991_2/NC_082429_1 来源族；**全部为已见 DEV 案例，仅管线演练，不计分**） |
| 模型（2） | `zai-org/GLM-5.3-Flash`、`deepseek-ai/DeepSeek-V4.1-Flash`（SiliconFlow，现有 key） |
| 策略（2） | A = promptv1 + 无卫兵；B = promptv2 + {evidence_enforce_manifest, pre_verify_pointers, unresolved_requires_review} |
| 网关 | temperature=0；max_tokens=8192；timeout=120s；零自动重试 |
| 执行顺序 | 同任务双臂同序背靠背；逐 run 落盘 checkpoint + 报告 + 收据 |

### 2.2 预算与熔断

| 项 | 值 | 依据 |
|---|---|---|
| 估算 | ≈86k tok（16 × 5,374，G4 实测最差每案例） | `g4_ds_chloro40/aggregate.json` |
| **硬上限** | **150,000 tok / 80 请求** | ×1.5 裕度；5 req/run = 均值 2.6 + 一次卫兵拒绝重规划 |
| 超限处置 | 立即停止；已产生结果按分母如实记录；不推断未跑部分 | 冻结协议 §5 同款 |

### 2.3 成功判据（全过才算 Smoke 通过）

1. 16/16 到达终态（任何终态：succeeded / failed / partial 系 / budget_exhausted，如实记录）；
2. 0 私有泄漏、0 白名单外访问（逐 run `check_secrets` 思路 + registry 拒绝计数）；
3. 每个 `report.versions` 记录 prompt 版本 + SHA256 + policy_guards 指纹；逐请求 prompt 身份与所属臂一致；
4. B 臂 guard 拒绝事件全部入审计流（有拒绝不是失败，无记录才是失败）。

### 2.4 人工门槛 1（强制）

Smoke 结束后驱动器**停止**，输出 smoke 收据（逐 run 状态/决策/token/拒绝事件）。用户审阅后明确选择：
- **继续 S2**（需同时完成 §8 签字 + 预算确认）；
- **停止**（候选保持 validated 封顶，active 策略不变）。

无自动继续路径（`no_auto_continue: true`）。

## 3. S2：完整 DEV A/B（需 §8 签字）

- 双臂 2 × 40 案例，同序执行；`eval_offline --planner llm`：
  - A 臂 planner = `LLMPlanner(gateway, prompt_registry, "promptv1")`；
  - B 臂 planner = `LLMPlanner(gateway, prompt_registry, "promptv2")` + strategy 卫兵全开；
- 预算：≤130 req/臂、≤400k tok/臂、总量 ≤1M（含 repair 轮，repair 分列不混入主分母）；
- 判定：主指标不降 **且** 安全指标全绿 → `accepted`；否则 `auto_rejected`；
- 归因：只宣称系统级（prompt+卫兵）改进；不得拆分归因到 prompt 文本。

## 4. S3：门槛判定与发布

1. `transition(validated → shadow_tested → accepted)`（真实 A/B 证据解锁 status_cap，`run_phase15` 候选通道）；
2. `accepted` ≠ 生效：**必须** `record_approval(真实身份, real=True)` → `approved → published`；
3. 拒绝即阻断：rejected 终态，active（promptv1 + 现 config）逐字段不变；
4. 回滚：published 后发现回归 → 注册表 `rollback_ref` + git revert 策略记录 → active 恢复 promptv1；
5. 收据：每次转换落盘（`real_ab` 标记，simulation 标记去除）。

## 5. S4：独立 holdout（未来另行授权）

- 候选切片：mito 域或新 chloro candidates（**与 40 案例不相交**，逐案例 `source_group_id` 核验）；
- 本阶段**不选样、不运行、不宣称**；选定即新协议版本。

## 6. 失败判据（任一命中即整轮判负 + 回滚）

| 类 | 判据 |
|---|---|
| 安全 | critical false pass > 0；私有金标泄漏 > 0；白名单外访问 > 0 |
| 主指标 | judgment pass 率或 decision_joint 低于 A 臂 |
| 证据质量 | 指针解析率低于基线；B 臂以删除必要证据换通过（findings/facts 一致性检查不过） |
| 分母 | 分母不完整或跳过无理由 |
| 预算 | 任何硬上限击穿 |

## 7. 执行清单（启动 S1 时逐步打钩）

- [ ] 用户确认 Smoke 硬上限（150k tok / 80 req）
- [ ] 用户确认启动 S1（16 runs）
- [ ] 运行 smoke 驱动（零人工干预，逐 run 落盘）
- [ ] 输出 smoke 收据 → **人工门槛 1**
- [ ] （若继续）§8 签字 + 1M 预算确认 → S2
