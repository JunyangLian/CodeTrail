# V3_1_EVOLUTION_IMPLEMENTATION_PLAN — 自进化最小闭环实施计划

> 日期：2026-10-18 ｜ 前置：[V3_FUNCTION_GAP_AUDIT.md](V3_FUNCTION_GAP_AUDIT.md)（10 项审计，E1/E2 承接为零）
> 目标链（自进化最小验收，全部 8 环缺一不可）：
> **真实失败 → 自动归因 → 自动提出受限候选 → DEV A/B → 质量门槛判定 → 人工审批或自动拒绝 → 版本化记录 → 历史回归**
> 阶段 1 约束：**仅复用历史 Trace（GLM/DS 80 run）与离线 Mock**；未经新授权不调用付费 API；
> 所有阶段 1 产物强制标注 `offline simulation`。
> 红线：不重写 LangGraph Runtime / Tool Adapter / Scorer；V2 v2.1.1、V3 git commits、全部历史收据零覆盖。

## 1. 三条主线与验收链映射

| 主线 | 内容 | 覆盖验收链环节 |
|---|---|---|
| **A. Failure Intelligence** | 从历史 Trace 自动归因失败 → 版本化失败案例池 | 环节 1–2（真实失败→自动归因） |
| **B. E1 Strategy Evolution** | 受限候选生成（白名单参数 + prompt 版本化）→ 候选注册表 → DEV A/B → 质量门槛 → 审批/拒绝 → 版本化发布 | 环节 3–7 |
| **C. E2 Regression Memory** | 失败经验 → 回归测试候选 → 审核 → 机械回归执行；承接 V2 E2-006 deferred 项 | 环节 8（历史回归） |

## 2. Phase 1（本期实施，offline simulation）

### A 线：Failure Intelligence

- 新增 `src/genomeqc_agent/evolution/failure_intel.py`：
  - 输入：`data/eval/g4_glm_chloro40/`、`g4_ds_chloro40/`、`g4_glm_chloro40_repair/` 的 aggregate + checkpoint 终态（经 `QCGraph.get_state`，同 audit_traces 路径）。
  - 归因分类器（确定性规则，白名单枚举）：`output_truncation` / `read_timeout` / `provider_503` / `auth_401` / `plan_contract_violation` / `transport_error`。
  - 输出：`data/failure_pool/failure_cases.v1.jsonl`（append-only，每条含 case_id、model_id、error_code、evidence pointer（aggregate 行号/run_id）、first_seen、status=`open`）。
  - **已知输入**：GLM 6 例真实失败 + 401 演示 + G2 注入故障——全部为真实 Trace，非编造。
- 验收：6 例 GLM 失败 100% 自动归类且类别与人工分析一致；池文件哈希入 git。

### B 线：E1 Strategy Evolution（阶段 1 = offline simulation）

- 前置补齐：`llm_planner.SYSTEM_PROMPT_VERSION = "promptv1"` 并入 `versions`（E1 版本化前提）。
- 新增 `src/genomeqc_agent/evolution/strategy.py`：
  - 候选白名单（**仅此四类**，超出即拒绝生成）：`max_tokens`（4096/8192）、`gateway_timeout`（120/240）、`verify_pointer_cap`（50/100）、`prompt_version`（引用注册表中的已审核 prompt 文本）。
  - 候选注册表 `data/strategy_registry.v1.json`：每候选含 id、base_version、diff、proposer=auto、status=`proposed`。
  - 质量门槛判定器 `quality_gate.py`：对比基线/候选在 DEV 切片上的 (a) judgment 分布不降 (b) 指针解析率不降 (c) 0 critical false pass (d) 分母完整——任一不满足 → `auto_rejected`。
  - 审批收据：沿用 V2 `APPROVAL_*.json` 模式——`proposed → (gate pass) → needs_human_approval → approved|rejected → applied|discarded`；**无人工批准不得 applied**（阶段 1 中 approved 环节模拟为 `human_approval=simulated` 并标注）。
- **offline simulation 声明**：阶段 1 的 "DEV A/B" 为**参数重放模拟**——用历史 Trace 中已存在的同参数对照（GLM 4096 截断失败 vs repair 轮 8192 成功，即真实发生的天然 A/B）验证门槛判定器逻辑；**不产生新的模型调用，不宣称任何真实 Agent 策略已改善**。

### C 线：E2 Regression Memory

- 新增 `data/regression_pool/v3_pool.v1.jsonl`（沿用 V2 池 schema：id/family/status/regression_test/transition_note，append-only）。
- 首批入池（全部来自真实 Trace）：
  1. `infra.planner_read_timeout`（GLM 2 例）→ 回归：gateway 截断/超时单测已存在，补池登记；
  2. `infra.provider_503`（GLM 3 例）→ 回归：503→PlannerError→failed 终态测试；
  3. `plan.hallucinated_pointer`（GLM 幻觉 `proteins.fa` 案例）→ 回归：**新测试**——伪造指针必须 unresolved 且 evidence_validity 降级 partial（锁定拦截行为）；
  4. `contract.null_tool_name`（DS synthesize null）→ 回归：validator 归一测试。
- 承接 V2 E2-2026-006（deferred to V3）：在 V3 composer/报告层加 "计数控诉须逐类 breakdown" 的检查候选（只动 V3 文件，不碰 V2 冻结审计器）——本期仅登记为 `proposed`，不实现。
- 审核：池条目 status 变更须记录 reviewer 与日期（阶段 1 reviewer=自动化收据 + 人工复核标记 simulated）。

## 3. Phase 2（需用户新授权后才可执行）

1. 真实 DEV A/B：候选（如 max_tokens=8192、prompt v2）在 chloro-40 上对 GLM/DS 重跑——已授权模型 + 已确认预算。
2. 质量门槛真实判定 → APPROVAL 收据 → 人工批准后 applied → prompt 版本 bump。
3. 跨进程恢复测试补齐（1.3 缺口）：杀进程→新进程 `QCGraph(db_path=同库).resume(thread)`。
4. human approval gate：`optional_human_review` 节点实现（block 决策或 partial 证据时暂停等待审批），`needs_human_review` 按规则置真。
5. mito LLM 跨域（GLM/DS 各 20 案例）与 Streamlit 浏览器验收（Playwright/浏览器 agent 走五页面）。

## 4. 里程碑

| 里程碑 | 内容 | 闸门 |
|---|---|---|
| V3.1-M1 | A 线归因器 + 失败池（6/6 归类一致） | 池 JSONL 入 git；归因=人工分析对照一致 |
| V3.1-M2 | C 线回归池 v1（4 条入池 + 幻觉指针回归测试） | pytest 含新回归全绿 |
| V3.1-M3 | B 线候选注册表 + 质量门槛判定器（重放模拟验证） | 截断案例被 8192 候选"通过门槛"且 4096 基线被正确比较；全程 offline simulation 标注 |
| V3.1-M4 | 审批收据链模拟闭环（proposed→gate→needs_human_approval） | 8 环验收链在 simulation 下全链走通，标注清楚 |
| V3.1-M5（Phase 2） | 真实 A/B + 审批门 + 跨进程恢复 + mito LLM + 浏览器验收 | 需授权；不做则 V3.1 停在 M4 并如实声明 |

## 5. 明确不宣称

- 阶段 1 全部产物为 **offline simulation**：验证的是"自进化闭环的机械结构与判定逻辑"，**不是**真实 Agent 策略改进。
- 不写"自进化系统/自进化完成"；按 V2 §11.7 口径只能写"基于轨迹归因的评测驱动改进流程的离线模拟"。
- E3 权重进化不规划（V2 准入未满足，维持 NOT DONE）。
