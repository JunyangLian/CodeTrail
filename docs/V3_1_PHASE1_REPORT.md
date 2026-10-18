# V3_1_PHASE1_REPORT — 自进化最小基础设施 Phase 1（offline simulation）

> 日期：2026-10-18 ｜ 状态：M1–M4 implemented/tested（**全部 offline simulation**）
> 质量门：**116 passed** + ruff clean + secrets CLEAN ｜ commit：见 git log
> 红线遵守：未重写 Runtime/Tool Adapter/Scorer；V2 v2.1.1、V3 历史 commits 与收据零覆盖；零付费 API 调用。

## 1. 能力状态总表（真实实现 vs 离线模拟 vs 待真实 A/B）

| 能力 | 状态 | 证据 |
|---|---|---|
| M1 失败自动归因 | **真实实现**（对真实 Trace） | `evolution/failure_intel.py`：GLM 6 例真实失败 + 1 例高风险成功全部自动归类，类别与人工分析一致（`data/failure_pool/attribution_report.v1.json`） |
| M1 失败案例池（版本化、append-only、幂等） | **真实实现** | `data/failure_pool/failure_cases.v1.jsonl`（7 条：6 failure + 1 high_risk_success；重跑不重复——幂等测试覆盖） |
| M2 Prompt 版本标识 | **真实实现** | `llm_planner.SYSTEM_PROMPT_VERSION='promptv1'`；报告 `versions.prompt` 携带（graph `node_synthesize`） |
| M2 受限候选生成器 | **真实实现（生成逻辑）+ 离线输入** | `evolution/controller.py::StrategyProposer`：白名单四字段、diff/根因依据/回滚引用齐全；**对真实失败池输出 1 候选 + 1 拒绝**（幻觉类拒绝：需人工 prompt 候选，超出白名单）；无依据时拒绝生成（测试） |
| M3 候选状态机（8 环验收链） | **offline simulation** | `EvolutionController`：proposed→validated→shadow_tested→accepted→**approved（人工门硬阻断实测）**→published；gate 失败自动 rejected、生产配置不变、非法转换拒绝、每转换写收据（`data/evolution/receipts/`） |
| M3 质量门槛判定器 | **offline simulation**（Mock 指标） | 判定规则实现并被 Mock 指标驱动测试；**尚未对真实 A/B 指标判定** |
| M4 E2 回归池（V2 承接 + V3 入池 + 去重） | **真实实现** | `evolution/regression_pool.py`：V2 池 10 条只读导入（治理字段保留）+ V3 4 条入池；去重键 (source, mechanism, scope)（幂等测试）；状态三档治理 |
| M4 确定性回归执行 | **真实实现** | `run_formal_regressions`：3 条 V3 formal 回归（幻觉指针降级/截断归因/null tool_name 归一）pytest 实跑全过；V2 冻结树内测试不执行（保留 V2 收据为证据） |
| 真实 DEV A/B | **未实施**（需授权） | 阶段 2；质量门槛判定器已就绪等待真实指标 |
| 人工审批真实闭环 | **未实施**（模拟为 `human_approval=True`） | 状态机阻断已实测；真实审批人/界面属阶段 2 |
| 跨进程恢复 / mito LLM / 浏览器验收 | **未实施** | 见 V3_1 计划 Phase 2 |

## 2. 交付物位置

| 交付 | 路径 |
|---|---|
| Failure Case Pool | `data/failure_pool/failure_cases.v1.jsonl`（append-only）+ `attribution_report.v1.json` |
| Strategy Candidate Registry | `data/evolution/receipts/`（逐转换收据）+ `scripts/run_phase1.py`（驱动） |
| Evolution Decision Receipts | 同上（proposed/validated/shadow_tested/accepted/approved/published/rejected 每转换一份） |
| E2 Regression Report | `data/evolution/e2_regression_report.v1.json` + 池 `data/regression_pool/v3_pool.v1.jsonl`（14 条：V2 导入 10 + V3 4） |
| 代码 | `src/genomeqc_agent/evolution/{failure_intel,controller,regression_pool}.py`；测试 `tests/unit/test_evolution.py`（23 项） |
| 汇总 | `data/evolution/phase1_summary.json` |

## 3. M1 归因结果（真实 Trace，与人工分析对照一致）

| 类别 | 数量 | 案例 |
|---|---|---|
| infrastructure_error | 6（全部失败例） | 截断 1（4096 上限）、ReadTimeout 2、供应商 503 3 |
| model_planning_error | 1（高风险成功） | 幻觉指针 `proteins.fa:all_records` 等被机械层拦截、报告降级 partial、QC 决定仍正确 |
| data_or_scoring_defect | 0 | 本轮 Trace 无实例（V2 STAT_DEFINITIONS 先例类别在池 schema 中保留） |
| tool_error / unclassified | 0 | 工具层零故障 |

## 4. M3 状态机实测行为（offline simulation）

1. `proposed→validated`：质量门槛（judgment 不降 + 解析率不降 + 0 critical false pass + 分母完整）通过 → 放行。
2. gate 失败（pass_rate 0.85→0.60）→ **自动 rejected**，生产配置不变。
3. `accepted→approved` 在 `human_approval=False` 时**抛错阻断**（真实阻断，非日志提示）；`human_approval=True` 才放行 → `published` 更新 active 配置。
4. 非法转换（proposed→published）拒绝。
5. 每次转换生成独立收据 JSON，全部携带 `simulation: offline_simulation`。

## 5. 诚实声明（必须读）

- **本轮全部产物为 offline simulation**：验证的是自进化闭环的机械结构（状态机、门槛、审批阻断、池治理、回归执行）与对**真实历史 Trace** 的归因能力。
- **未验证**：任何真实 Agent 策略改善；质量门槛对真实 A/B 指标的判定；真实人工审批界面。
- **禁止宣称**："自进化系统/自进化完成/策略已改善"。可宣称口径（沿 V2 §11.7）："自进化闭环的机械基础设施已在离线模拟中实现并通过测试，归因器对 6 例真实失败 100% 对齐人工分析"。
- 历史 repair 结果（GLM 8192 修复）仅作为**归因依据与门槛重放素材**，不作为新候选的 A/B 收益（M3.6 遵守）。

## 6. Phase 2 清单（需用户新授权/预算）

1. 真实 DEV A/B：候选（promptv2 / max_tokens=8192 / timeout 240）在 chloro-40 对 GLM/DS 重跑（~200k tokens 量级）。
2. 质量门槛真实判定 + APPROVAL 收据链真实化（人工审批界面或 CLI 确认流程）。
3. 跨进程恢复测试（1.3 缺口）；`optional_human_review` 图节点（1.5 缺口）。
4. mito LLM 跨域（GLM/DS 各 20 案例）；Streamlit 浏览器验收。
