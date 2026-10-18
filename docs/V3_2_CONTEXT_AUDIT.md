# V3.2 P0 Context & Harness 代码审计

> 审计对象：commit `161af71`（V3.1 RC）｜ 方法：真实代码路径逐点核实 + 既有测试/实验证据
> 判定口径：DONE / PARTIAL / MISSING / NOT_VERIFIED（缺实现不靠文档补标）

## 1. LLM 每轮真实收到的消息（Q1/Q2/Q3）

**System**（`llm_planner.py`）：`LLMPlanner.next_action` 发送 `self.system_prompt`——经 `PromptRegistry` 按版本加载并 SHA256 校验（Phase 2 绑定），或回落模块 `SYSTEM_PROMPT`。**DONE**。

**User**（`llm_planner.next_action`，L73-80）：`json.dumps({"facts": facts, "tool_trace": context.get("tool_trace"), "previous_denial": context.get("denial"), "notes": 缺 FACT_KEYS 时提示})`。

- `facts` = `organelle_summary` 完整结果（全部 coordinate_violations/multipart/ID 不一致/statistics——大小随包复杂度无界）
- `tool_trace` = **仅工具调用参数**（`graph._call` 只 append arguments，不含工具返回值）
- `denial` = policy 拒绝 verdict（一次重规划机会）
- 工具返回内容**不进入 planner 消息**：`verify_evidence_pointer` 结果存 `state["evidence"]`，`lookup_feature` 结果仅存 `last_result` 供状态机——planner 对自己调用过的工具的**返回内容是盲的**（只知道自己调用过什么参数）。**Q3：DONE（JSON 序列化，ensure_ascii=False）；Q2：facts/tool_trace/denial 全部进入，strategy 不进入（仅入 state 供卫兵）。**

## 2. 截断 / 重复 / 遗漏（Q4）

- **无任何截断逻辑**：facts 与 tool_trace 原样全量注入 → 无遗漏，但**无界膨胀**（长包 facts 可达数十 KB；verify 50 指针的参数列表随重复调用线性增长）。
- **重复**：同一工具重复调用参数会重复出现在 tool_trace（无去重）。
- **结构性盲区**：planner 看不到工具返回值 ⇒ "verify 未通过"这类关键反馈只能经 denial（仅 policy 拒绝）或 facts 变化传导——**关键信息传导缺失 = MISSING 项**。
- 判定：**PARTIAL**（无丢失，但有膨胀、重复、返回值盲区三缺口）。

## 3. Context Token 预算（Q5）

全链路无任何 Token 估算或预算：`grep` 无 token estimator；gateway 仅上报 provider 返回的 usage（事后）。**MISSING**。

## 4. 关键信息保护（Q6）

- 文件路径/Locator：facts 内指针原样保留，**无静默压缩**（因无压缩机制）——"未被破坏"但原因是"没有该能力"，保护机制本身缺失。
- 错误反馈：`last_error` 进入 `runtime` 但**不进入 planner 消息**（除 denial 通道）。
- Tool Schema 关键参数：由 pydantic schema 强校验（registry），schema 哈希入 ToolVersionInfo。
- 判定：**PARTIAL**（无损坏风险存在，但保护机制缺失）。

## 5. 长任务多轮信息保持（Q7）

- 状态字段（facts/evidence/tool_trace/runtime）跨轮保留于 state，`node_plan` 每轮重新全量注入 → 无遗忘。
- 但 planner **输出侧无记忆管理**：reasoning 不跨轮留存（每轮独立调用）；planner 无法引用前轮自己的规划理由（除 tool_trace 参数间接体现）。
- 判定：**PARTIAL**。

## 6. Checkpoint 恢复一致性（Q8）

- State 全 JSON 可序列化；`SqliteSaver` + `thread_id=run_id`；跨进程恢复测试（进程 A 崩溃 → 新进程 resume 至终态）已存在并通过；strategy/facts/evidence 随 checkpoint 恢复 → 上下文一致。
- 实验证据：S2 观测 111 被中断后恢复，账本零重复。
- 判定：**DONE**。

## 7. ScriptedPlanner 冒充风险（Q9）

- `QCGraph(planner=None)` **默认构造 ScriptedPlanner**（`graph.py:60`）——S1 首误即此（16 次 Mock 假运行，VOID 留痕）。
- 现有缓解：`eval/safety.is_real_model_run`（事后审计判据）+ RC manifest 流程；**运行时无强制**（提交 llm 任务不会拒绝 scripted planner 实例）。
- 判定：**PARTIAL**（事后可检，事前无防）。

## 8. 推理配置记录完整性（Q10）

| 参数 | 发送 | 记录 | 判定 |
|---|---|---|---|
| temperature | ✓ 硬编码 0.0（gateway.py payload） | 未逐 run 记录（代码常量） | PARTIAL |
| top_p | ✗ 不发送 | ✗ | MISSING（unsupported 标注缺失） |
| max_tokens | ✓（planner 参数 8192） | ✓ 计入 usage 上限语义 | DONE |
| reasoning | GLM 强制 reasoning_content | ✓ reasoning_chars 入 last_usage；DeepSeek 无 | DONE（GLM 侧） |
| seed | ✗ 不支持 | ✗ 未标注 unsupported | MISSING |
| prompt SHA / context 版本 | ✓ prompt_sha256 入 last_usage+报告 | ✓ | DONE |

## 9. 其余组件快照

| 组件 | 判定 | 依据 |
|---|---|---|
| Tool Registry / Schema | DONE | pydantic 严格校验、error_code/retryable、schema_hash、白名单（既有 145+ 测试） |
| Evidence Verification | DONE | 机械解析 + unresolved→partial + needs_review 卫兵 |
| Self-Refinement | DONE（有界 2 轮、重验证、不删证据） | 测试 8 项；真实模型纠错率未测（P4 补） |
| Failure Intelligence | PARTIAL | 关键词分类仅产 observed 级假设；无根因假设/反证/置信度/干预验证字段 → P2 升级 |
| Evolution Controller | DONE | 状态机/硬门/真实审批接口（161 tests） |
| Scorer/Oracle | DONE | V2 venv 隔离、冻结 v2.1.1、历史收据零覆盖 |

## 10. 结论与 V3.2 缺口清单

**MISSING**：Context Token 预算（Q5）；top_p/seed 的显式 unsupported 标注。
**核心 PARTIAL**：工具返回值盲区（planner 不见 verify/lookup 结果）；无上下文选择/去重/摘要与溯源；关键信息保护机制缺失；ScriptedPlanner 运行时无防。
**DONE 基线**：消息结构、checkpoint 一致性、版本绑定、工具/证据/进化/评分组件。

→ P1（ContextBuilder + planner 接入 + flag）、P2（归因 2.0）、P3（注入测试）按此缺口实施；V3.1 原始路径整体保留为 baseline（flag 默认 OFF）。
