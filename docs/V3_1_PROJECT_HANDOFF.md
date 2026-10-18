# V3.1 Project Handoff（项目交接）

> 日期：2026-10-18 ｜ Release Candidate commit：见 `V3_1_RELEASE_CANDIDATE_MANIFEST.json`
> 读者：接手 V3 维护/演进或评审发布的工程师

## 1. 项目一句话

AssemblyGenomics-Copilot V3（GenomeQC Agent）：细胞器基因组只读 QC 智能体（LangGraph + 4 只读工具 + 冻结 V2 评分），带**受控自进化管线**（失败归因 → 版本化策略候选 → 真实 A/B → 冻结门槛 → 人工审批 → 回归记忆），核心纪律：**规则负责事实，LLM 负责规划与解释，人对高风险处置保有审批权**。

## 2. 当前状态（一页）

| 维度 | 状态 |
|---|---|
| 里程碑 | G0–G6 全 DONE；V3.1 Phase 1 / 1.5 / 2 预检 DONE；一体化收尾 DONE |
| 真实实验 | S1 Smoke 16/16（98,851 tok）+ S2 方案甲 120/120（681,440 tok，含 1 分列 repair）；项目累计 **780,291 / 1M tokens** |
| A/B 结论 | GLM 配对 40 对：质量指标全平（40/40），无效指针 A=1 vs **B=0**，B +14.9% tokens → **ACCEPT_CANDIDATE（待审批）** |
| 策略状态 | **active = promptv1（未动）**；promptv2 候选 = DEV-verified / pending approval（cap: validated） |
| 测试 | 161 collected / exit 0；ruff clean；secrets CLEAN |
| 权威文件 | `V3_MASTER_PLAN.md`（总控）· `V3_FINAL_EXPERIMENT_REPORT.md`（实验汇总）· `docs/V3_1_FINAL_RELEASE_AUDIT.md`（发布前审计，GO） |

## 3. 目录导览

| 路径 | 内容 |
|---|---|
| `src/genomeqc_agent/runtime/` | LangGraph 运行时（graph/policy/state/refinement） |
| `src/genomeqc_agent/evolution/` | 失败归因/控制器/回归池/飞轮/预算守卫/flags |
| `src/genomeqc_agent/model/` | LLMPlanner（版本化 prompt 绑定）/网关/脚本 planner |
| `src/genomeqc_agent/api/` + `ui/` | FastAPI（含付费授权边界）+ Streamlit 9 页 |
| `scripts/` | 全部实验驱动（smoke/s2/repair/审计/归档/飞轮） |
| `evolution_state/` | git 跟踪的版本化资产（prompt 注册表/候选 manifest/收据） |
| `data/`（gitignored） | 原始实验数据；**独立备份在 `data/archive/s1_smoke` 与 `data/archive/s2_experiment`**（248+41 文件，SHA256 manifest 在仓库根） |
| `bench` 外的 V2 冻结 | V2 仓库只读；快照在 `src/genomeqc_agent/adapters/v2_snapshot/`（ruff exclude 保护） |

## 4. 关键机制速查

1. **Prompt 版本化**：`PromptRegistry`（注册即锁 SHA256，逐读重验）；`LLMPlanner(gateway, prompt_registry, prompt_version)` 构造期三方钉死；每份报告 `versions` 携带版本+哈希。
2. **策略卫兵**（默认 OFF，随 `run(strategy=...)` 携带）：`evidence_enforce_manifest`（policy 拒发）、`pre_verify_pointers`（强制验证）、`unresolved_requires_review`（composer 升级）。
3. **审批硬门**：`transition(→approved)` 需 `record_approval(..., real=True)`；模拟收据 raise。
4. **付费授权**：API 层四层校验（`GENOMEQC_PAID_AUTHORIZED=true` + 模型白名单 + 凭据 + prompt 注册）+ worker 二次断言；实验脚本要求调用方设 env。
5. **预算守卫**：`BudgetGuard`（最坏情形预留 + `from_ledger` 播种恢复）；`ledger_billing` 为权威计费（observation+repair）。
6. **断点恢复**：S2 runner 以账本为完成判据（`(case_id, arm)` 去重）；中断重启零重复。

## 5. 常用命令

```powershell
.venv\Scripts\python.exe -m pytest            # 全量测试
.venv\Scripts\python.exe -m ruff check src tests scripts ui
.venv\Scripts\python.exe scripts\check_secrets.py
# S2 断点续跑（已 120/120，重跑=零动作）
$env:GENOMEQC_PAID_AUTHORIZED='true'; .venv\Scripts\python.exe scripts\run_s2_experiment.py
# API + UI
.venv\Scripts\python.exe -m uvicorn genomeqc_agent.api.main:create_app --factory --port 8000
.venv\Scripts\python.exe -m streamlit run ui\streamlit_app.py
```

## 6. 回滚方法

- 代码/文档：`git checkout <commit>`（RC commit 见 `V3_1_RELEASE_CANDIDATE_MANIFEST.json`；阶段提交链完整可二分）。
- 策略回滚：active 即 promptv1（无变更发生）；若未来发布 promptv2 后需回滚 → 候选 `rollback_ref` + git revert 策略记录 → active 恢复 promptv1（注册表不可变，无需回滚 prompt 内容）。
- 实验数据：`data/` 不入 git；依赖 `data/archive/` 独立副本（勿删）。

## 7. 已知限制与 OPEN 项

1. promptv2 发布待真实审批（+14.9% token 换证据卫生，40 对方向性证据）。
2. 结论限 chloro DEV 40 案例；mito / 独立 holdout 未测。
3. 飞轮 2 条候选待人工评审。
4. SiliconFlow key 未轮换（用户豁免中）。
5. 单机单用户 MVP：API 仅绑 127.0.0.1，公网部署需 auth/HTTPS/留存策略。

## 8. 禁止事项（对接手者的硬约束）

- 不修改 V2 冻结资产与 v2_snapshot（ruff exclude 已护）。
- 不把私有金标引入 V3 运行时。
- 不在无真实审批下发布 promptv2 或改动 active。
- 不调用未授权付费 API；不无上限重试；不静默丢弃失败观测。
- 不改写历史实验收据与评分（新发现 = 新收据）。
