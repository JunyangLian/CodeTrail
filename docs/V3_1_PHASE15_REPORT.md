# V3_1_PHASE15_REPORT — 真实 A/B 离线准备（P0–P2，offline simulation）

> 日期：2026-10-18 ｜ 前置：V3.1 Phase 1（`bb88ecd`）｜ 本轮 commit：见 git log
> 质量门：**117 passed** + ruff clean + secrets CLEAN ｜ **零付费 API 调用**
> 红线遵守：active 策略全程未变；候选只达 `validated`；Runtime/Adapter/Scorer 零改动。

## P0 — 注册表与版本审计 ✅

| 项 | 现状（审计结论） | 本轮动作 |
|---|---|---|
| Prompt 版本 | 此前 `SYSTEM_PROMPT` **无版本字段**（审计缺口 1.6） | 新增 [prompts.py](../src/genomeqc_agent/evolution/prompts.py) `PromptRegistry`：注册即锁哈希、读取时**强制完整性校验**（漂移即抛 `PromptIntegrityError`）；`promptv1`（G4 收据对应的生产 prompt）与 `promptv2`（指针卫生候选）已注册；`llm_planner.SYSTEM_PROMPT_VERSION='promptv1'` 并入报告 `versions.prompt` |
| 候选注册 | 此前无 git 跟踪的注册表 | **[evolution_state/](../evolution_state/)（git 跟踪）**：`STRATEGY_CANDIDATE_MANIFEST.json` + `prompt_registry.v1.json` + `failure_pool/` + `receipts/`（决策收据从 data/ 迁入版本化目录） |
| Active 策略 | `promptv1` + `{max_tokens: 8192, gateway_timeout_s: 120, verify_pointer_cap: 50}` | **全程未变**（测试断言拒绝/失败路径后 active 逐字段相等） |

## P1 — 针对真实 invalid_evidence_pointer 的受限候选 ✅

真实失败：GLM 幻觉指针（`chloro_C1_NC_088770_1_CHLORO-CDS-03`，`proteins.fa` 文件名幻觉 + 伪造 locator）。

候选 `CAND-promptv2-pointer-guard`（`EvolutionController.propose_behavior_policy`）：

| 组成 | 内容 |
|---|---|
| config_delta（运行时卫兵） | `evidence_enforce_manifest`（指针文件名必须 ∈ manifest）、`pre_verify_pointers`（引用前先验 locator）、`unresolved_requires_review`（不足即 partial/needs_review） |
| prompt diff | promptv1 → promptv2（指针卫生三条款 + 禁止删证据条款），diff 全文随候选存储 |
| 失败依据 | 真实案例的 unresolved_pointers 原文（来自 M1 失败池，引用链保留） |
| 回滚引用 | `{"prompt_version": "promptv1", "config_delta": {}}` |
| 状态上限 | **`validated` 封顶**——`shadow_tested/accepted/approved/published` 一律抛错（需 Phase 2 真实 A/B 证据），实测 cap 强制生效 |
| 红线 | delta 不含任何代码路径；Python 源码/Oracle/Scorer/冻结数据零接触（结构上不可能：delta 只含三个布尔开关 + 注册表内 prompt 引用） |

## P2 — 审批机制与跨进程恢复 ✅

| 项 | 证据 |
|---|---|
| 无审批不可发布 | `accepted → approved` 在 `human_approval=False` 时抛错阻断（Phase 1 测试 + 本轮复用），active 不变 |
| 审批拒绝阻断 | `record_approval(decision="reject")` → 候选终态 `rejected`、active 逐字段不变（测试断言） |
| 审批身份记录 | 收据含 approver/decision/candidate_version/时间戳（`approval_reject_1.json` / `approval_approve_1.json`）；本轮审批人标注 `offline_simulation`——真实审批人流程属 Phase 2 |
| 跨进程 checkpoint 恢复 | **新测试** `test_cross_process_checkpoint_resume`：进程 A 写 checkpoint 后退出 → 全新解释器（[resume_worker.py](../scripts/resume_worker.py)）从同一 checkpoint 文件 resume → succeeded/block、facts 无损。过程中修复真实缺陷：packet 沙箱为进程内存态，跨进程后 `execute_tool` 找不到包 → `node_execute` 懒重注册（幂等只读） |

## P3 — 真实 A/B 协议冻结（只写协议，零 API 调用）

见 [V3_1_REAL_AB_PROTOCOL.md](V3_1_REAL_AB_PROTOCOL.md)：模型 ID、DEV 切片与 source_group、A/B 策略版本对、冻结 Scorer/Rubric、主+安全指标、硬预算、拒绝/回滚/发布门槛全部冻结成文；启动需用户对协议签字并确认预算。

## Phase 1.5 完成后的能力三态

| 状态 | 项 |
|---|---|
| 真实实现（有测试/Trace） | PromptRegistry 不可变版本化、behavior-policy 候选生成（真实失败驱动）、状态封顶强制、审批收据与拒绝阻断、跨进程恢复（发现并修复懒注册缺陷） |
| offline simulation | 审批人身份（simulated）、质量门槛指标（历史重放） |
| 未实施（需 Phase 2） | 真实 shadow/A-B、真实审批人、mito LLM、浏览器验收 |
