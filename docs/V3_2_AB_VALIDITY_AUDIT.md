# V3.2 AB Validity Audit（A/B 单因素真实性审计）

- 日期：2026-10-19（final round P1）
- 审计对象：`V3_2_REAL_AB_PLAN.json`（frozen 2026-10-18T13:53:39Z）+ `V3_2_REAL_AB_LEDGER.jsonl`（144 观测）+ 144 scorecards + 144 QC_REPORT + 21 个 checkpoint db（152 thread）
- 审计脚本：`scripts/audit_ab_validity.py`（离线，零 API 调用）
- 机器可读产物：`data/eval/final_round/p1_audit/{ab_config_diff,del_evidence_audit,ab_stats_reanalysis}.json`
- 基线 commit：`c30a4f3`（工作树快照见 `data/eval/final_round/p0_baseline_snapshot/`）

## 总结论

1. **单因素成立**：16 项可控配置字段中 15 项双臂严格一致，唯一差异 = 上下文组装方式（A=V3.1 raw-json / B=ContextBuilder v1 contextv1），与冻结计划声明一致。
2. **B 臂 4→0 的改善是真实的证据可靠性改善，不是删证据**：4 组焦点配对中 B 的报告引用集合与 A 逐项一致（各 6 条引用）、缺陷定位分与 judgment 无退化；全量 144 报告双臂引用画像完全相同（516/516 条引用、12 个去重引用、0 份零引用报告）；评分器指针复核 mismatch 双臂均为 0。
3. **旧报告的成本统计表述有误，需要降级**：旧文"任务级 CI 不重叠"不成立——旧文打印的两个区间本身重叠（A 4,556–5,647 vs B 5,012–6,491），且按正确单位（task 级配对 bootstrap，n=24 聚类）计算的差异 CI 为 **[−3.79%, +28.65%]，包含 0**。成本差异只能表述为点估计 +13.5%（任务级配对均值），**不能声称统计显著**。
4. 方向性结论保持成立：无效指针 4/72 vs 0/72、质量层 144/144 满分（verdict/judgment/contract/defect_localization 双臂零差异）。

## P1.1 配置逐字段审计

机器可读：`ab_config_diff.json`（16 checks + ledger_uniformity + confounds）。

| 字段 | A | B | 受控 | 出处 |
|---|---|---|---|---|
| model_id | GLM-5.3-Flash / DeepSeek-V4.1-Flash | 同 | ✔ | plan.matrix.models |
| system_prompt | promptv1 | promptv1 | ✔ | PromptRegistry 绑定（逐读重验） |
| prompt_sha256 | `f4f02d46…` | 同 | ✔ | 144 行账本全部一致且等于 registry |
| temperature | 0.0 | 0.0 | ✔ | gateway 默认，runner 未覆盖 |
| top_p / seed | 未发送 | 未发送 | ✔ | gateway payload 仅 temperature+max_tokens |
| max_tokens | 8192 | 8192 | ✔ | LLMPlanner 构造参数 |
| reasoning | GLM 强制 thinking 计入 completion，DS 无 | 同 | ✔ | gateway.py 探针（2026-10-17） |
| tool_schema / 权限 | V3 冻结工具契约（只读白名单） | 同 | ✔ | ToolRegistry 单实例共享 |
| policy_guards | `{}` | `{}` | ✔ | runner strategy dict |
| scorer | organelle_v2.rubric.deterministic_grader/v1 | 同 | ✔ | 144 scorecards grader_version 唯一 |
| oracle/数据 | V2 candidates 同一 case 目录 | 同 | ✔ | runner 常量 V2_CANDIDATES 双臂共用 |
| 工具/模型预算 | max_model_requests=5, max_tool_calls=32 | 同 | ✔ | run_one + graph 默认 |
| retry/repair | 无（0 条 repair 行） | 同 | ✔ | 账本 kind 全部=observation |
| self-refinement | 全关（features 仅 context_builder_v1） | 同 | ✔ | cell_strategy |
| **context_assembly** | **raw-json（builder=None）** | **contextv1** | ✘ | **唯一干预变量** |

账本均匀性（全部通过）：prompt_version/sha256 均一且匹配 registry；model 映射一致；A 行无 builder receipt、B 行 72/72 有 contextv1 receipt（completeness 全部 full、omitted 合计 0——**B 未发生上下文截断，不存在"压缩丢证据"路径**）；scorecards/reports 144/144。

### 残余混杂（披露，不改变结论）

- **执行顺序 24/24 A-first**：每个 (case, model) 对内 A 全部先于 B 完成窗口。机制：cells 按 case→model→cond 轮转分配到 4 进程×5 线程，A/B 落在不同线程但 A 有效负载更小、完成更快。属顺序-速度耦合，非串行污染；**结论方向为"更差的事件全在先跑完的 A 臂"，若为顺序效应只会低估 A 的失败率，不会制造 B 的优势**。
- **B 臂注入块本身计入成本**：contextv1 注入的 task/evidence_locators/facts/tool_history 块是干预的一部分，+13.5% token 已包含该成本，不存在双重计费。
- 运行窗口 2026-10-18T13:57–15:13 UTC（76 分钟），20 并发。

## P1.2 删证据负例审计

### 焦点配对（A 臂全部 4 个 invalid-pointer 观测 vs 同格 B 孪生）

| case | model/r | A unresolved | A 引用 | B unresolved | B 引用 | A judgment/dl | B judgment/dl | 判定 |
|---|---|---|---|---|---|---|---|---|
| NC_088757_1_CHLORO-META-05 | glm/r1 | 1（malformed：`artifacts/genome.fa` 无 locator） | 6 | 0 | 6 | pass / 2 | pass / 2 | **valid** |
| NC_088762_1_CHLORO-META-05 | glm/r1 | 1 | 6 | 0 | 6 | pass / 2 | pass / 2 | **valid** |
| NC_088770_1_CHLORO-NORMAL-00 | glm/r3 | 1 | 6 | 0 | 6 | pass / – | pass / – | **valid** |
| NC_140640_1_CHLORO-NORMAL-00 | ds/r3 | 1 | 6 | 0 | 6 | pass / – | pass / – | **valid** |

四组配对中 B 全部：resolves_all ✔、仍引用证据 ✔、缺陷检查块完整 ✔、judgment/defect_localization 无退化 ✔。

### 逐项排除"删证据"假说

| 假说 | 排除证据 |
|---|---|
| 未生成必要证据 | 双臂 tool_trace 均含 inspect_packet + organelle_summary + verify_evidence_pointer；4 组配对 verify 调用数 A=2/1/1/1 vs B=1/1/1/1，B 均完成验证 |
| 静默删除 unresolved pointer | B 72/72 receipt omitted=0、completeness=full；evidence_locators 为 protected 块（builder 设计：永不压缩）；账本 unresolved_pointers 字段独立于评分，B 真实为 0 |
| 过滤错误 findings | 焦点配对 checks 块逐项一致；B 臂 72 报告无一缺失缺陷检查（scorecard verdict 全部 delivered=expected） |
| 省略证据验证 | 双臂报告引用画像完全相同：总引用 516=516、失败项引用 192=192、去重引用 12=12、零引用报告 0=0 |
| partial 强改 valid | evidence_validity 由机械指针解析决定（账本独立字段），B 的 0 来自 planner 未引用未解析指针，非判定篡改；评分器 evidence_locator_validity 复核 mismatch：A=0、B=0 |
| Scorer 双标 | 144 scorecards grader_version 唯一；同 case 双臂送入同一 grader 子进程与同一 case 目录 |

**机制解读**：报告引用集合由工具事实机械组装（check 级 evidence_refs），模型自由度在"verify 调用引用哪些指针"。A 的 4 例 = 模型在 verify 调用中引用了无 locator 的文件名（如 `artifacts/genome.fa`）→ 机械解析失败 → evidence_validity=partial；B 的 contextv1 把文件清单（evidence_locators 块）前置展示后，模型未再幻觉指针。**改善路径 = 输入组织方式抑制幻觉，非证据删除。**

## P1.3 统计再分析（正确单位）

### 无效指针（判别力所在）

- A = **4/72**（Wilson 95% CI 2.2%–13.4%），B = **0/72**（Wilson 95% CI 0%–5.1%）。
- 聚类 = (task, model)，n=24；配对差 B−A 的 bootstrap CI 见 JSON；受影响聚类 4 个（glm META-05×2、glm NORMAL-00、ds NORMAL-00），**GLM 3 例 / DS 1 例，双模型均出现 → 非单模型伪象**。
- 留一敏感度：剔除任一任务后 A 仍为 3–4，B 恒 0 → **对比不由单一困难任务驱动**。
- 重复一致性：verdict/judgment/contract 48/48 格内完全一致；invalid 仅 44/48 —— 4 个 A 格只在 3 次重复中的 1 次出现幻觉指针 → **幻觉为随机事件而非该格系统性缺陷**，也意味着 3 次重复下 A 的 4/72 是下界型观测（泊松意义上每格出现率 ~0.06）。

### Token 成本（修正旧声明）

| 口径 | A | B | 差异 |
|---|---|---|---|
| 观测级均值 | 5,101.3 tok | 5,751.4 tok | +12.7%（旧报告口径） |
| 任务级配对均值差 | – | – | **+13.47%** |
| **任务级配对 bootstrap 95% CI（正确单位，n=24 聚类）** | | | **[−3.79%, +28.65%] —— 包含 0** |
| 分模型 | glm 4,981→5,882（+18.1%）；ds 5,221→5,621（+7.6%） | | |

**旧声明"任务级 CI 不重叠"不成立**：旧文打印区间 A [4,556–5,647] 与 B [5,012–6,491] 重叠 635 tok；且观测级/任务级均值 CI 本就不是配对检验。正确表述：**B 成本点估计 +12.7%（观测级）/+13.5%（任务级配对），在任务级配对单位下与 0 无统计可区分性（n=24）**。方向在 13/24 聚类为正（见 JSON `cluster_pct_diffs`）。成本合理性结论在 P5 中按此口径重新表述。

### 质量层（天花板再次确认）

verdict_correct 1.0=1.0、judgment_pass 1.0=1.0、output_contract 1.0=1.0（配对差 0，bootstrap CI [0,0]）、defect_localization 均值 2.0=2.0、零聚类差异。

### first-run vs repair

V3.2 无 repair 行（144 全为首次观测、0 基础设施失败）；S2 的 503-repair 双口径不适用于本轮。两次无效启动（8,130 tok）已在 plan.voided_attempts 留痕且不在账本（voided_rows_in_ledger=0）。

## 对原结论的影响

| 原结论 | 审计后状态 |
|---|---|
| 单因素设计（唯一变量=上下文组装） | **维持**（16 字段审计通过） |
| B 无效指针 4→0 为真实改善 | **维持并加强**（引用画像逐项相同，排除删证据） |
| 质量零回归（72/72 双臂） | **维持** |
| B 成本 +12.7% 且"CI 不重叠" | **降级**：点估计 +12.7~13.5%，任务级配对 CI 包含 0，不得称显著 |
| 方向性结论（非显著性）的诚实表述 | 维持该框架，成本侧按新口径修正 |
