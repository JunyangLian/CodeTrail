# V3.2 ContextBuilder 报告（P1）

> commit 基线 `32fa173` ｜ 实现：`src/genomeqc_agent/context/builder.py`（contextv1）
> 接入：`LLMPlanner(context_builder=...)` + `node_plan`（feature flag `context_builder_v1`，默认 OFF）

## 1. 实现的五个能力

| 能力 | 实现 | 测试 |
|---|---|---|
| 上下文选择 | 块结构：task→denial→evidence_locators（protected）→facts→tool_history（bulk）；工具调用去重（repeat 计数）+ 最近 6 次详保留、更早摘要 | `test_tool_history_dedup_and_summary_provenance` |
| 关键证据保护 | protected 块永不压缩/删除（任务/清单/定位符/denial/未解析错误）；超预算时 `completeness=partial` 显式报告，内容仍全量保留 | `test_budget_overflow_reports_partial_and_never_drops_protected`、`test_protected_over_budget_reports_without_dropping` |
| Token 预算 | 估算器 chars/4 记录 est；与 provider 实际 usage **分开记录**（receipt.estimator 注明"estimate only"） | `test_provenance_receipt_complete` |
| 工具结果管理 | 去重 + 摘要带来源与 SHA256 + "summary is NOT raw evidence" 声明 | 同上 |
| 上下文溯源 | 每次 build 产出 receipt：版本/整体 SHA256/逐块 source+included+compressed+sha256/est/omitted 清单；调用时入 `last_usage["context"]` → 实验账本逐观测可查 | `test_deterministic_sha_for_same_inputs` |

## 2. 与 Attention 的关系（P1.2，如实声明）

仅做模型外部组织：关键信息**前置**（task/denial 首块）、证据块置于 bulk 之前（位置控制测试：`test_evidence_findable_at_any_position` 证明 locator 在大体积 facts 挤压下仍处头部）、防淹没（保护块独立于预算压力）。**未声称任何模型内部注意力优化**。

## 3. 兼容性（P1.3）

- flag 关闭/未传 builder 时，planner 路径与 V3.1 **逐字节一致**（`next_action` else 分支原样保留；`node_plan` 仅在 flag 开时附加 builder_inputs）。
- promptv1/v2 内容零改动；B 臂 receipt 72/72 full 已在真实 144 观测中验证（`V3_2_REAL_AB_REPORT.md`）。
- 敏感审计：context receipt 含任务文本与指针（公开脱敏材料），完整账本在本地 gitignored data/ 下；普通日志只打印 completeness 摘要。

## 4. 审计缺口的对应关闭

| P0 缺口 | P1 关闭方式 |
|---|---|
| 无 Token 预算（MISSING） | est/actual 分记 + partial 显式报告 |
| 工具返回盲区 | evidence/unresolved 块进入 planner 上下文（B 臂） |
| 无选择/去重/摘要 | 去重 + 详情窗口 + 摘要带溯源 |
| 无溯源 | 逐调用 receipt |
| 关键信息保护缺失 | protected 块机制 |
