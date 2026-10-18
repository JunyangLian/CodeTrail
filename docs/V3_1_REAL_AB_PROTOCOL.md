# V3_1_REAL_AB_PROTOCOL — 真实 DEV A/B 实验协议（已冻结，待授权启动）

> 冻结日期：2026-10-18 ｜ 状态：**FROZEN — 未启动**（启动需用户对 §8 签字 + 预算确认）
> 本协议一旦启动不得单方修改；任何变更 = 新协议版本 + 重新冻结。

## 1. 冻结的模型与网关

| 项 | 值 |
|---|---|
| 变体 A（基线） | `zai-org/GLM-5.3-Flash` @ siliconflow，promptv1 |
| 变体 B（候选） | `zai-org/GLM-5.3-Flash` @ siliconflow，**promptv2 pointer-guard**（sha256 见 `evolution_state/prompt_registry.v1.json`）+ `ACTIVE_CONFIG + {evidence_enforce_manifest, pre_verify_pointers, unresolved_requires_review}` |
| 对照参考 | `deepseek-ai/DeepSeek-V4.1-Flash`（promptv1，仅稳定性/一致性参考，非 A/B 臂） |
| 网关参数 | temperature=0；max_tokens=8192；timeout=120s；**零自动重试**（infra 失败按 failed 计入分母，可 repair 轮补跑并分列） |

## 2. 冻结的 DEV 切片与 source_group

| 项 | 值 |
|---|---|
| DEV 切片 | 本地 candidates 池 **chloro 40 全量**（两来源族：NC_013991_2 / NC_082429_1 × NORMAL/CDS/ID/META）——与 G4 收据完全同切片 |
| source_group 纪律 | 逐案例 `source_group_id` = accession 族；A/B 双臂同 case 同序执行；**不引入新样本、不重称 held-out** |
| mito 域 | 不在本协议内（LLM mito 为独立后续协议） |

## 3. 冻结的 Scorer 与 Rubric

- 评分器：`organelle_v2.rubric.deterministic_grader/v1`（V2 v2.1.1 冻结，只调用不修改；隔离 worker 进程执行）
- Rubric：v2.1.1 冻结集（evidence_support/action_quality 维度留人工，不自动评）
- 映射：V3 QCReport → V2 合同（`eval/adapter.py`，不可映射跳过计入分母）

## 4. 冻结的指标

| 类 | 指标 | 门槛 |
|---|---|---|
| 主指标 | judgment pass 率；decision_joint 得分；defect_localization 得分 | 变体 B ≥ 变体 A（不降） |
| 证据质量 | 指针解析率；`evidence_validity` 分布；幻觉指针拦截数 | 解析率 ≥ 基线；拦截不得以删除必要证据为代价 |
| 安全指标 | critical false pass = **0**；私有泄漏 = **0**；白名单外访问 = **0** | 任一违反 → 整个 A/B 判负并回滚 |
| 分母 | cases_total / graded / skipped（含原因） | 完整披露，跳过需理由 |

## 5. 硬预算（超限即熔断）

| 项 | 上限 |
|---|---|
| 每臂请求数 | ≤ 130 requests（40 案例 × ~3.25 规划上限） |
| 每臂 tokens | ≤ 400,000 tokens |
| 总上限（双臂 + repair） | ≤ 1,000,000 tokens |
| 超限处置 | 立即停止，已产生结果按分母如实记录，不推断未跑部分 |

## 6. 拒绝 / 回滚 / 发布门槛

1. 判定：主指标不降 **且** 安全指标全绿 → `accepted`（候选）；否则 `auto_rejected`。
2. `accepted` ≠ 生效：必须经人工审批（`record_approval`，真实身份）→ `approved → published`。
3. **拒绝即阻断**：rejected 候选进入注册表终态，active 策略（promptv1 + 现 config）逐字段不变。
4. **回滚**：published 后发现回归 → `git revert` 策略记录 + 注册表回滚引用（每候选自带 `rollback_ref`）→ active 恢复 promptv1。
5. 全程收据：每次转换写 receipt（simulation 标记去除，标记 `real_ab`）。

## 7. 执行清单（启动时）

1. 用户确认 §8 授权 + 预算。
2. `run_phase15.py` 的候选状态 `validated → shadow_tested`（解封 status_cap）。
3. 双臂执行 `eval_offline --planner llm`（B 臂加载 promptv2 + 运行时卫兵开关）。
4. 门槛判定 → 收据 → 审批 → 发布/拒绝。
5. 报告：`docs/G4_REAL_AB_RECEIPT.md`（分母/指标/失败/成本全披露）。

## 8. 启动授权（待用户签字）

- [ ] 确认本协议全部冻结项
- [ ] 确认 §5 预算
- [ ] 授权调用 GLM-5.3-Flash（SiliconFlow，现有 key）
- 签字/日期：____________
