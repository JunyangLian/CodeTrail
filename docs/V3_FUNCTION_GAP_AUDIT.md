# V3_FUNCTION_GAP_AUDIT — 功能缺口审计（对照 V2 §11 E1/E2/E3 进化规划）

> 日期：2026-10-18 ｜ 方法：逐项对照实际代码路径、测试、Trace、收据；**设计文档与 Mock 测试不作为真实模型能力证明**。
> 结论先行：**自进化（自动闭环）状态 = NOT_IMPLEMENTED**。V2 侧 E1 首轮全链在人工监督下 DONE；
> V3 侧不存在任何自动"失败→归因→候选→A/B→门槛→审批→版本化→回归"环节。不声称自进化已完成。
> 本审计不修改任何 Runtime / Tool Adapter / Scorer 代码；不触碰 V2 v2.1.1、git 历史、既有收据。

## 0. 证据标准

| 等级 | 含义 |
|---|---|
| 实证 | 有代码路径 + 测试或真实运行 Trace/收据 |
| 机制实证 | 机制经注入式测试证明，真实运行中未（或未需）触发——如实注明 |
| 仅设计 | 仅存在于设计文档/合同字段，无实现或从未生效 |
| offline simulation | 只在 Mock/历史重放下验证，禁止宣称真实能力 |

## 1. 逐项审计（10 项）

### 1.1 Agent 动态规划和工具选择 — **DONE**（范围注记）

- 证据：`src/genomeqc_agent/runtime/graph.py`（`node_plan`/`route_after_plan`/`route_after_policy` 条件路由）+ `model/scripted_planner.py`（事实驱动决策表）；真实模型 Trace：`docs/trace_audit.json`——GLM 89 次规划（55 tool + 34 synthesize）、DeepSeek 101 次（61+40），两模型均 `dynamic=true`，2 条事实驱动路由（lookup_branch 17/20 vs direct_verify 23/20）。
- 范围注记：工具面仅 4 个只读工具、分支由事实字段驱动；`user_goal` 尚未参与规划（计划 §1.3 的个性化规划属扩展项，见 V3_1 计划）。

### 1.2 运行内有界错误恢复 — **DONE**（机制实证）

- 证据：`graph.py::node_mandatory`（内联 retry，上限 `max_retries_per_tool`）、`node_verify`/`route_after_verify`/`node_recovery`、`registry.TransientToolError`；测试 `test_transient_error_recovers_within_bounds`（retry 1 次→成功，计数留痕）、`test_persistent_transient_error_stops_after_limit`、`test_mandatory_failure_cannot_be_skipped_into_a_report`。
- 注记：GLM/DS 真实 40 案例**图内 0 次触发**（infra 故障全部发生在规划调用层）——机制为注入式证明，非实战触发统计。

### 1.3 Checkpoint 与跨进程恢复 — **PARTIAL**

- 已实证：SQLite checkpointer（`SqliteSaver`）+ `interrupt_before` + resume 循环；`test_interrupt_and_resume_preserves_facts`、`test_resume_output_equals_uninterrupted_run`（逐字节一致）、`test_resume_after_completion_is_noop`。
- 缺口：三个 resume 测试都在**同一 `QCGraph` 实例（同进程）**内执行；"进程终止后新进程从 checkpoint 文件恢复"无测试——`QCGraph(db_path=...)` 技术上支持，但无证据。跨进程恢复测试列入 V3_1 计划 C 线。

### 1.4 Evidence Support 语义验证 — **NOT_IMPLEMENTED（by design）+ OPTIONAL**

- 现状：V3 只做指针**可解析性**（`verify_evidence_pointer`）与事实集合覆盖（`evidence_validity`/partial 纪律）；**语义支持性判断不存在也不自动化**。
- 依据：V2 冻结设计（grader `evidence_support` 恒 `pending_human`）+ v2.1.1 盲评实证（AI 代理 es 维宽松 MAE 0.675，`RUBRIC_REVIEW_THREE_WAY_REPORT`）。**刻意 NOT_IMPLEMENTED，自动化它反而违反总控 §3 纪律**。
- OPTIONAL 后续：半自动辅助标注（AI 提草稿、强制人工确认），须先经人工校准——列入 V3_1 计划备选，非本期。

### 1.5 人工审批 / 暂停 / 恢复 — **PARTIAL**

- 已实证：`POST /jobs/{id}/resume`（checkpoint 恢复）、`POST /jobs/{id}/cancel`（queued→cancelled；running→`cancel_requested` 如实标记）。
- 缺口（代码事实）：
  1. 图中**无 human_review 节点**（`runtime/graph.py` 零匹配；总控 §4.1 的 `optional_human_review` 未实现）；
  2. `reports/composer.py` `needs_human_review=False` **硬编码**——合同字段存在但永不生效；
  3. running 任务取消只是标记，不中断图执行。
- 以上三项列入 V3_1 计划 A/C 线（审批门随质量门槛一起设计）。

### 1.6 E1 自动失败分析与候选策略生成 — **NOT_IMPLEMENTED**

- V2 侧：E1 首轮全链 **DONE**（人工监督）：`bench/organelle_v2/docs/E1_FIRST_LOOP_RECEIPTS.md`——发现（NORMAL-00 误报）→根因（STAT_DEFINITIONS 口径）→受控修复（forward-only）→修复池（C1R）→候选（located-v2 采纳/v2·v3 拒绝）→影子验证（48/48）→跨模型→错误入回归（E2-2026-002）；APPROVAL_*.json 收据链。
- V3 侧：G4 收据的失败分类（截断/超时/503/401）是**人工验收分析**，非自动管线；`llm_planner.SYSTEM_PROMPT` **无版本字段**（E1 必要前提缺失）；无候选生成器、无候选注册表。

### 1.7 Shadow A/B、质量门槛、版本发布和回滚 — **PARTIAL**

- 已有（版本化记录）：git commits、`v2_adapter_version`、`versions` dict（报告内）、V2 E1 的 APPROVAL/FROZEN 收据模式可复用；V2 先例包含真实回滚（located-v3 → 回滚 located-v2）。
- 缺失：V3 无 shadow A/B 执行框架、无质量门槛判定器（judgment 不降 + 解析率不降 + 0 critical false pass 的自动比较器）、无策略候选的发布/回滚机制（git revert 是唯一手段）。

### 1.8 E2 错误经验入池、审核和回归执行 — **NOT_IMPLEMENTED（V3 侧承接为零）**

- V2 侧：E2 池 v2（append-only，10 条：5 条 converted_to_regression + 5 条 confirmed/pending）；`test_e2_regressions.py` 4 条机械回归 + scoring_contract 1 条；运行收据 `E2_REGRESSION_RUN_20261017.json`（17 passed）。
- V3 侧缺口：`tests/` 对 E2 池**零引用**（grep 证实）；V3 自身的 6 例 GLM infra 失败 + 坏密钥 401 + 截断经验**未入任何经验池**；E2-2026-006 明确 "candidate countermeasure … **deferred to V3**"——未承接。

### 1.9 线粒体 GLM/DeepSeek 跨域能力 — **NOT_IMPLEMENTED**

- 已实证：mito 20 案例（5 来源 × 4 条件）**scripted planner** 20/20 graded、20 pass（`V3_CROSS_DOMAIN_REPORT.md`）。
- 缺口：GLM/DeepSeek 的 mito 运行**不存在**——LLM 跨域规划能力无证据；40 案例 LLM 对比全部为 chloro。

### 1.10 Streamlit 前端的实际浏览器验收 — **NOT_IMPLEMENTED**

- 已实证：真实 uvicorn HTTP 全链路（`scripts/acceptance_e2e.py`：upload→submit→status→events→report→download）+ Streamlit 无头启动 HTTP 200。
- 缺口：无真实浏览器交互验收（页面元素、上传交互、轨迹渲染、报告下载按钮均未在浏览器层验证）。

## 2. 与 V2 §11 对照总表

| V2 规划 | V2 状态 | V3 承接状态 | V3_1 计划 |
|---|---|---|---|
| E1 评测驱动策略进化（§11.1/11.3/11.7） | 首轮全链 DONE（人工监督） | **NOT_IMPLEMENTED**（无归因管线/无 prompt 版本/无候选机制） | B 线 |
| E2 数据进化（§11.4） | PARTIAL（池 v2 + 5 条机械回归） | **NOT_IMPLEMENTED**（零承接；E2-006 deferred 未接） | C 线 |
| E3 权重进化（§11.5） | NOT DONE（准入未满足） | NOT IMPLEMENTED（维持；V2 明确不写自进化宣称） | 不规划 |
| M6 独立 A/B | PARTIAL（条件验证≠改进 A/B） | NOT IMPLEMENTED | 阶段 2（需授权） |

## 3. 不变式（本审计后仍然有效）

V2 v2.1.1（tag @ 7518adc）冻结、V3 git commits（`48ef4b8`/`9cad65d`）、全部历史评分与收据零覆盖；
Runtime / Tool Adapter / Scorer 未被本审计修改；真实模型调用仅限已授权 GLM/DeepSeek。
