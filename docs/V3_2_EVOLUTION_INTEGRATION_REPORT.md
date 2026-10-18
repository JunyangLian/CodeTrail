# V3.2 进化系统整合报告（P6）

> 整合执行：`scripts/integrate_v32.py` ｜ 结果：`data/eval/v32_ab/evolution_integration.json`

## 1. 真实结果 → 进化候选

基于 P4 真实 A/B（144 观测：无效指针 A=4 vs B=0，质量全平），生成上下文策略候选：

| 项 | 值 |
|---|---|
| candidate_id | `CAND-contextv1-pointer-guard` |
| kind | context_strategy（新类别：上下文选择规则调整） |
| config_delta | `{"context_builder_v1": true}` |
| prompt | **promptv1 不变**（单因素纪律） |
| 证据 | 真实 A/B 账本（144 观测）+ 分析 JSON 引用 |
| rollback_ref | `{"config_delta": {}, "features": {}}` |
| 状态 | **validated**（status_cap=validated 强制） |

## 2. 发布门实测

- 模拟审批（real=False）→ `transition(→approved)` **raise**，发布被阻断（状态断言）✓
- active strategy 保持 promptv1 ✓
- 收据：`evolution_state/receipts/v32_context_candidate/`（proposed/validated.json）

## 3. 与既有机制的整合点

- **候选类型扩展**：`context_strategy` 加入进化候选语汇（此前仅 config_delta 与 behavior_policy）——候选内容可以是 Context 选择规则/Token 预算/证据优先级/摘要策略/Tool Schema 描述/Prompt，全部走同一状态机与硬门。
- **归因升级喂给候选**：P2 的 experimentally_supported 证据（单因素干预）作为候选的 failure_refs，满足"有明确错误证据 + Diff + DEV 对照"三要件。
- **有界不变**：Self-Refinement 仍 ≤2 轮；Data Flywheel 仍人工审核 + 来源隔离（V3.2 无新增自动入池）。

## 4. 发布路径（未执行）

`validated → accepted（需真实 A/B 门禁复核）→ approved（record_approval real=True 真人）→ published`。本轮停在 validated + 发布阻断实证；**active 策略保持 promptv1**。
