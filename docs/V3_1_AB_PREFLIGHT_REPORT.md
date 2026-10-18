# V3.1 Phase 2 A/B 预检报告（零 API）

> 日期：2026-10-18 ｜ 状态：**预检完成 — GO（仅限 Smoke）**
> 预算状态：**100 万 Token 预算未批准，本轮零付费 API 调用**（`api_calls: 0`）
> 收据：`evolution_state/V3_1_AB_PREFLIGHT_TEST_RECEIPT.json` ｜ 执行计划：`docs/V3_1_AB_EXECUTION_PLAN.md`

## 0. 结论

| 项 | 判定 |
|---|---|
| P0 策略实际生效 | **PASS**（本轮实现后实测；实现前为 FAIL，见 §1） |
| P1 A/B 可比性 | **PASS**（核查结论见 §4；B 臂 = 组合策略，只做系统级归因） |
| P2 Shadow 执行与审批 | **PASS**（模拟审批已无法冒充真实人工批准） |
| P3 预检与预算 | **PASS**（Mock 双臂链 4/4 终态；Smoke 估算 86k vs 150k cap） |
| **GO / NO-GO** | **GO — 仅限 16-run Smoke**；完整 A/B 仍需协议 §8 签字 + 预算确认 |

## 1. 预检发现的缺口（实现前审计，如实记录）

用户要求核查的 P0 三项，在预检起点**全部为 FAIL**：

| 核查项 | 预检起点状态 | 证据 |
|---|---|---|
| planner 按版本加载 prompt | **FAIL** | `llm_planner.py` 的 `next_action` 硬编码使用模块常量 `SYSTEM_PROMPT`；`prompt_version` 类属性纯装饰（注释写着 "E1: strategy candidates version this" 但无任何消费点） |
| Prompt 内容/版本/SHA256 运行绑定 | **FAIL** | 版本号不进入任何模型调用，`last_usage` 不携带 prompt 身份 |
| 三开关运行时效果 | **FAIL** | `evidence_enforce_manifest` / `pre_verify_pointers` / `unresolved_requires_review` 仅存在于 manifest、收据和 `controller.py` 的候选生成端——运行时（graph/policy/composer）**零消费**，正是"只有注册表字段而没有运行效果"的禁例 |

本轮把这四个缺口**全部实现为真实运行时机制**，并用行为差异测试锁定。

## 2. P0：策略实际生效（实现 + 证据）

### 2.1 Prompt 版本运行时绑定

- `LLMPlanner(gateway, prompt_registry, prompt_version)`：构造期把**版本号、内容、SHA256** 三者钉死；经注册表加载时逐次读取重验哈希（`PromptIntegrityError` 即拒）；无注册表时回落模块 prompt 并绑定其哈希。
- 每次模型调用的 `last_usage` 收据携带 `prompt_version` + `prompt_sha256` → 任何一次调用的 prompt 身份可审计。
- **篡改证明**：注册后篡改内容 → `get()` 与 planner 构造均抛 `PromptIntegrityError`（测试 `test_tampered_registry_cannot_reach_a_model_call`）——被污染的 prompt 无法到达模型调用。
- **发送内容证明**：FakeGateway 记录实际发送的 system prompt，与注册表内容逐字节相等（v1/v2 双版本，收据 `p0_prompt_binding.all_bound = true`）。

### 2.2 三开关运行时接入（每开关一个行为差异测试）

| 开关 | 生效层 | 机制 | 行为差异实测（off → on） |
|---|---|---|---|
| `evidence_enforce_manifest` | Tool Policy | policy gate 在**执行前**拒绝引用 manifest 外文件的 `verify_evidence_pointer` 调用（`pointer_not_in_manifest`），拒绝入事件流 | partial（幻觉指针执行后 unresolved）→ **valid + 拦截事件**；findings 逐项相同（无证据被删） |
| `pre_verify_pointers` | Evidence Verification | 未验证即 synthesize 被拒（`evidence_unverified`），强制先跑 `verify_evidence_pointer` | 零机械验证即出报告 → **验证后才能收尾**（拒绝事件在案） |
| `unresolved_requires_review` | Report Composer | composer 在 unresolved>0 时置 `needs_human_review=True` | review False → **True**；validity 不变（只升级不粉饰） |

保守性论证（方向 4 合规）：
- `enforce_manifest` 只拒绝**本来就不可能解析**的指针（文件不在工具返回的 manifest 中），且拒绝记录在审计流——不是静默删除；facts/findings 完全不变。
- `pre_verify` 只能把"未验证就通过"变成"必须验证"——只增不减。
- `unresolved_requires_review` 只能升级人工审查——不能把 partial 变 valid。
- 三开关默认全 **OFF**：`strategy` 块缺省/为空的运行与历史基线**行为逐字节一致**（测试 `test_baseline_topologies_are_identical_with_and_without_strategy_block`，含 trace 与判定相等断言）。

### 2.3 promptv2 个案硬编码检查

- 无 `NC_\d{4,}`、无 `CHLORO-`/`MITO-`、无 `chloro_C1` 等个案标识；
- 规则均为通用表述（manifest 文件名精确匹配、locator 必须来自工具输出、不确定不引用）——适用于任意基因组证据包（收据 `p0_promptv2_genericity` 全 true）。

## 3. P2：Shadow 执行与审批

| 要求 | 实现 | 测试/收据 |
|---|---|---|
| validated 候选影子可执行，不得绕过状态限制发布 | shadow = 独立 `QCGraph` 运行 + strategy 块；状态机 `validated → published` 为非法转换 | `test_shadow_runs_never_touch_active_strategy` |
| 影子执行不改 active 配置 | active_config 前后相等断言；candidate 状态停在 validated | 同上 + 收据 `p2_shadow_and_approval.active_strategy_unchanged=true` |
| 拒绝/回滚真实状态断言 | 质量门自动拒绝、人工拒绝（`record_approval(reject)` → 终态 rejected、active 逐字段不变）均为状态断言 | Phase 1.5 测试保留 + `run_phase15.py` 重跑实测 |
| **模拟审批不能冒充真实人工批准** | `transition(→approved)` 现在要求：存在 `decision=approve` **且 `real=True`** 的审批收据；模拟收据（`real=False`）一律 `EvolutionError`（状态断言，非荣誉制） | `test_simulated_approval_cannot_authorize_publishing` |
| 收据含候选版本/配置哈希/模型输出引用 | `shadow_receipt()`：candidate_version + prompt 版本/SHA256 + `active_config_sha256` + 逐 run 的 run_id/status/decision/report 引用 | `test_shadow_receipt_carries_version_config_and_run_refs` |

## 4. P1：A/B 可比性核查结论

1. **对齐项**：双臂同模型（GLM-5.3-Flash）、同 40 案例 DEV 切片、同序执行、同工具集、同 Scorer（V2 v2.1.1 冻结 grader）、同网关参数（temperature=0 / max_tokens=8192 / timeout=120s / 零自动重试）、同预算上限——全部由构造保证（冻结协议 §1–§5）。
2. **B 臂定义**：**Prompt + Policy Guards 组合策略**（promptv2 + 三开关），非 Prompt-only。
3. **归因纪律**：A/B 结果只评价**系统级改进**（prompt+卫兵整体），不单独归因于 prompt 文本。若未来需要 prompt-only 归因，须新协议版本增加 C 臂（promptv2 无卫兵），本轮不做。
4. **来源组隔离**：DEV = chloro 40（**已见**）；Smoke = 4 个已见案例（仅管线演练，**永不计分、永不宣称未见**）；历史回归 = G4 收据 + E2 池（已见）；**最终独立测试 = 尚未选定、尚未运行**——未来 holdout 必须是与 40 案例不相交的新切片，本阶段不声称任何未见测试。
5. **已见任务纪律**：预检收据与执行计划均明示 smoke 任务为 seen cases（P1.5）。
6. **预定义指标与失败判据**：沿用冻结协议 §4（主指标不降、解析率不降、critical false pass=0、泄漏=0、分母完整）；本报告新增过程指标——逐臂 guard 拒绝率入收据；**B 臂不得以删除必要证据换取通过率**（findings 与 facts 逐案例一致性检查）。

## 5. P3：预检与预算

- **Mock 双策略链**：4 包 × A/B 双臂全图执行，8/8 终态 succeeded，逐包判定相等（NORMAL=pass、CDS-03/MULTI=block、mito NORMAL=pass），B 臂报告携带 prompt 版本+SHA256+卫兵指纹（收据 `p3_mock_ab_chain`）。
- **预算估计**（G4 实测外推）：

| 项 | 实测来源 | 值 |
|---|---|---|
| DeepSeek 每案例 | g4_ds_chloro40（40 案例，101 请求，214,962 tok） | 2.52 req / **5,374 tok** |
| GLM 每案例 | g4_glm_final（8 案例，18 请求，34,133 tok，含 reasoning） | 2.25 req / 4,267 tok |
| Smoke 估算（16 run） | 16 × 5,374 | **≈85,984 tok**（×1.5 裕度 ≈129k） |
| Smoke 硬上限提案 | — | **150,000 tok / 80 请求**（5 req/run：覆盖均值 2.6 + 一次卫兵拒绝重规划） |
| 完整 A/B 参考（80 run） | 80 × 5,374 | ≈430k tok < 协议 §5 的 1M cap ✓ |

- **Smoke 设计**：4 任务 × 2 模型 × 2 策略 = 16 run；任务冻结于收据 `p3_smoke_design.tasks`；成功判据与中止判据已冻结。
- **人工继续门槛**：Smoke 16 run 结束后**强制停止**，须用户审阅 smoke 收据并明确授权才可进入完整 A/B（`no_auto_continue: true`）。
- **零付费**：本轮 `api_calls: 0`；未经 §8 签字不调用任何付费 API。

## 6. 变更清单（相对 f9d3724）

| 文件 | 变更 |
|---|---|
| `src/genomeqc_agent/model/llm_planner.py` | 版本化 prompt 绑定（注册表加载 + SHA256 双向校验 + last_usage 身份） |
| `src/genomeqc_agent/runtime/policy.py` | 三开关卫兵（GUARD_KEYS、strategy_guards、evaluate 扩展） |
| `src/genomeqc_agent/runtime/graph.py` | `run(strategy=...)`、policy gate 消费卫兵与 manifest、synthesize 卫兵路由（基线拓扑不变）、composer 卫兵参数、报告版本块含 prompt_sha256+policy_guards |
| `src/genomeqc_agent/runtime/state.py` | `strategy` 字段（checkpoint 安全） |
| `src/genomeqc_agent/reports/composer.py` | `unresolved_requires_review` → needs_human_review |
| `src/genomeqc_agent/evolution/controller.py` | 真实审批收据强制（`real=True` 才可发布）+ `shadow_receipt()` + 收据含 active_config_sha256 |
| `tests/unit/test_ab_preflight.py` | **12 项新测试**（绑定 3、genericity 1、行为差异 3、基线不变 1、shadow/审批 3、Mock 链 1） |
| `scripts/run_ab_preflight.py` | 预检驱动（收据生成器） |
| `scripts/run_phase1.py` | 发布路径补真实审批收据 |
| `evolution_state/` | `V3_1_AB_PREFLIGHT_TEST_RECEIPT.json` 新增；manifest/收据刷新（real + config hash 字段） |

## 7. 测试与静态检查

- **129 passed**（117 原有 + 12 新增），0 failed
- ruff clean ｜ secrets 扫描 CLEAN（`.env` 不入库）
- 历史冻结数据零覆盖：V2 快照、G4/G5 收据、既有 117 项测试全部原样通过

## 8. GO 条件与边界

**GO** 判定覆盖：**仅 16-run Smoke**。启动 Smoke 前仍需用户确认：
1. 本报告 §5 的 Smoke 硬上限（150k tok / 80 req）；
2. Smoke 完成后的**人工继续门槛**（审阅收据 → 决定是否进入完整 A/B）；
3. 完整 A/B 需对 `docs/V3_1_REAL_AB_PROTOCOL.md` §8 签字 + 1M 预算确认——Smoke 通过不自动解锁。
