# V3_AGENT_TRACE_REPORT — Agent 能力六维 Trace 审计（P1.4）

> 日期：2026-10-18 ｜ 数据源：GLM-5.3-Flash 与 DeepSeek-V4.1-Flash 的 chloro-40 全量运行
> （checkpoint 终态经官方 `graph.get_state` API 提取；脚本 `scripts/audit_traces.py`，
> 机读汇总 `docs/trace_audit.json`）。审计为**事后离线**进行，不触任何模型 API。

## 0. 概念澄清（P1.5）：QC verdict=pass ≠ eval success

| 概念 | 含义 | 出处 |
|---|---|---|
| `qc_decision=pass` | V3 报告的 QC 结论：工具事实显示四类检查全部无异常（坐标/多段结构/跨文件 ID/统计一致性），证据指针全部可解析 | V3 QCReport（运行时产物） |
| eval judgment=pass | V2 冻结评分器对**输出质量**的判定：输出合同 + verdict 正确 + root_cause 正确 + 缺陷定位满分（token/文件命中）+ 指针全解析 | V2 deterministic_grader/v1（评测侧） |

两者维度不同：前者是"送检对象没问题"，后者是"Agent 的报告写得对"。本审计中两者恰好同时成立（40/40），但**互不推导**——例如 Agent 给出 `insufficient_evidence` 时 QC 无 verdict，评分侧也跳过；Agent 若误报 block，eval 会判 fail 而 QC 决定仍自洽。报告中两者分开陈述。

## 1. 动态工具选择

| 模型 | 路由分布（40 runs） | distinct 路由 |
|---|---|---|
| GLM-5.3-Flash | lookup_feature 分支 17 / 直通 verify 23 | 2 |
| DeepSeek-V4.1-Flash | lookup_feature 分支 20 / 直通 verify 20 | 2 |

分支完全由工具事实驱动：`multipart_structure_issues` 非空 → 补 `lookup_feature`；否则直通指针核验。两模型都出现两类路由（G2 闸门"≥2 条有意义的不同工具路径"在真实模型下同样成立），且分支与各案例的缺陷类型对应（CDS/ID/META/NORMAL 条件族）。

## 2. 工具调用有效性

- 图内所有工具调用经 `ToolRegistry` 强 Schema 校验执行；40×2 runs 终态全部 `succeeded`。
- 注册表层错误合同（error_code/retryable）在 G1-G2 测试中覆盖；本轮 LLM 规划的参数**零 schema 拒绝**（policy_gate 无一次 `schema_invalid`）。
- GLM 主轮 6 例失败均发生在**规划调用**（网关层），非工具层；工具层零故障。

## 3. 规划分支

| 模型 | plan 事件 | tool 计划 | synthesize 计划 | 动态性 |
|---|---|---|---|---|
| GLM | 89 | 55 | 34 | true |
| DeepSeek | 101 | 61 | 40 | true |

每案例 2–3 次规划（1 次首轮 + verify 轮 + 收尾轮）；两个模型都稳定走完 "事实 → （按需补证据）→ 指针核验 → 收尾" 的决策链，无循环、无空转。

## 4. 错误恢复

- **图内 bounded_recovery：0 次触发**——两模型切片中未出现工具层瞬时故障（G2 的注入式恢复路径由测试覆盖：transient→retry 1 次→成功；persistent→failed）。
- **轮外 repair**（V2 repair-mode 先例）：GLM 主轮 6 例 infra 失败 → repair1 修复 3、repair2 修复 3 → **6/6 闭环**；DeepSeek 零 repair 需求。
- **planner 失败纪律**：网关截断/超时/401/503 一律 `PlannerError` → 运行终态 `failed`，**付费调用零自动重试**（5 类失败全部留痕于 G4 收据）。

## 5. 证据真实性与支持性

| 模型 | 指针引用 | 解析成功 | 解析率 |
|---|---|---|---|
| GLM | 304 | 302 | 99.34% |
| DeepSeek | 319 | 319 | **100%** |

**黄金案例（GLM 幻觉被拦截）**：`chloro_C1_NC_088770_1_CHLORO-CDS-03` 中 GLM 规划的核验请求包含两个**模型编造的指针**——`artifacts/proteins.fa:all_records`（文件名幻觉：包内是 `proteins.faa`）与 `artifacts/genome.fa:all_sequences`（无效 locator）。机械验证层正确判为 unresolved → 该报告 `evidence_validity=partial`（未掩盖），QC 决定仍由真实缺陷事实支撑（block）。这正是总控 §3 "指针有效 ≠ 证据充分" 纪律的实证：幻觉发生在模型侧，**被规则层拦截并降级**，不影响任何结论真实性。

## 6. 成本与延迟

| 模型 | 调用 | 总 tokens | 延迟中位数 | p95 |
|---|---|---|---|---|
| GLM-5.3-Flash | 89 | 172,246 | 14.3 s | 45.9 s |
| DeepSeek-V4.1-Flash | 101 | 214,962 | 9.6 s | 38.8 s |

- GLM 延迟更高的主因是强制 thinking（reasoning 最长 8.5k 字符/次）且截断敏感；DeepSeek 本轮零故障。
- 成本留空（单价未配置）；token 为供应商计费口径。

## 7. 审计边界

- 覆盖 chloro 40 案例 × 2 模型；mito 跨域见 `V3_CROSS_DOMAIN_REPORT.md`（scripted planner）。
- checkpoint 只保留终态（每 thread 最新），中间态回放依赖 events/tool_trace 的终态快照——两者均在 persist 前累积完成，审计完整性以 `trace_audit.json` 为准。
