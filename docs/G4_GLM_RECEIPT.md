# G4_GLM_RECEIPT — 真实模型受控验证收据（GLM-5.3-Flash @ 硅基流动）

> 日期：2026-10-18 ｜ 状态：implemented/tested（GLM 单模型小样本）
> 授权：用户于 2026-10-17/18 提供 SiliconFlow API key（仅 GLM；DeepSeek 未授权未测）。
> 密钥仅存于 git-ignored 的 `.env`，本收据与任何代码、日志、报告均不含密钥。

## 1. 精确配置（收据要求：不用模糊品牌名）

| 项 | 值 |
|---|---|
| provider | `siliconflow`（OpenAI-compatible，`https://api.siliconflow.cn/v1`） |
| model_id（平台实测） | `zai-org/GLM-5.3-Flash`（经 `/v1/models` 枚举确认，非猜测） |
| 角色 | planner only（工具事实、评分器零模型参与） |
| 参数 | temperature=0, max_tokens=4096 |
| 已探明供应商行为 | **强制 thinking**：`reasoning_content` 始终产生且计入 max_tokens（`enable_thinking=false` 被忽略，已实测）；`finish_reason=length` 视为网关错误（截断输出不可信） |

## 2. 正式运行（8 案例 chloro DEV 切片）

| 项 | 值 |
|---|---|
| 输入 | V2 candidates 公开视图（NC_013991_2 / NC_082429_1 × NORMAL/CDS-03/ID-01/META-05） |
| 分母 | cases_total=8；**graded=8**；not_mappable=0；phase_b_errors=0 |
| judgment | **pass 8 / fail 0**（与 ScriptedPlanner 基线 8/8 一致） |
| 模型消耗 | 18 requests；prompt 16,358 + completion 17,775 = **34,133 tokens**；含 1 次出错调用（计时入分母） |
| 成本 | tokens 如上；**单价未配置，成本留空不估算**（诚实口径） |

每案例 planning 轮次 2–3 次；reasoning 内容 0.5k–5.7k 字符/次（模型自主思考，不进入报告）。

## 2b. 扩片运行（2026-10-18，chloro 40 全量 × LLM planner + repair 轮）

**主轮**（`data/eval/g4_glm_chloro40/aggregate.json`）：

| 项 | 值 |
|---|---|
| 分母 | cases_total=40；graded=34；skipped_phase_a=6；not_mappable=0；phase_b_errors=0 |
| judgment | **pass 34 / fail 0** |
| 模型消耗 | 89 requests；172,246 tokens（含 6 次出错调用） |

**6 例失败分类**（全部留痕于主轮 aggregate）：
1. 1 × 输出截断（4096 上限被 reasoning+content 耗尽）
2. 2 × ReadTimeout（传输层）
3. 3 × 供应商 503 `System is too busy`（连续发生，服务端拥塞时段）

**Repair 轮**（V2 repair-mode 先例：infra-failed 槽位单独重跑、原收据不动；`scripts/repair_g4_slice.py`，`data/eval/g4_glm_chloro40_repair/repair_aggregate.json`；本轮唯一配置变更 max_tokens 4096→8192）：

| 轮次 | 分母 | 判定 |
|---|---|---|
| repair 1（2026-10-18） | 6 → graded 3；still_failed 3（均 503） | pass 3 |
| repair 2（2026-10-18，供应商恢复后） | 3 → graded 3；still_failed 0 | pass 3 |
| repair 合计 | **6/6 修复** | pass 6 / fail 0 |

**合并账本（主轮 + 两轮 repair）**：cases_total=40；graded=**40**；judgment **40 pass / 0 fail**；still infra-failed=**0**。合并 token ≈ 189,229 + repair2 增量。

**首轮 8 案例 vs 扩片一致性**：8 案例在两轮中 judgment 完全一致（pass），LLM planner 结果稳定。

## 3. 安全失败留痕（计划要求的"失败示范"，全部真实发生）

| # | 故障 | 表现 | 处置 |
|---|---|---|---|
| 1 | max_tokens 截断（1024 时代） | `finish_reason=length`，content 被切在 JSON 中间 | gateway 将截断输出判为不可信 → PlannerError；修复 = max_tokens 4096 + 截断检测 |
| 2 | 合同不匹配 | 模型 synthesize 时输出 `tool_name: null` | PlannedAction validator 归一 null→""（合同本意即"无工具"）；**不是**静默换 scripted 兜底 |
| 3 | 网关读超时 | NC_082429_1_CHLORO-ID-01 一次规划 `ReadTimeout` | 按 §6 纪律**不自动重试付费调用** → run 终态 failed、错误进收据与分母 |
| 4 | 无效密钥（演示） | 401 `Token is invalid` | 同上，failed 终态，零重试零副作用（`data/eval/g4_badkey_demo/`） |

单元层另有 mocked-transport 全覆盖（成功/围栏 JSON/合同违约/401/断连/图终止），93 项测试全绿，CI 无网络。

## 4. 诚实边界

- 小样本（8 案例、单域、DEV 切片、两来源）；**不声称**全域性能、held-out、双模型对照或改进 A/B。
- LLM 仅规划；每条 QC 决定仍由只读工具事实决定（本轮 LLM 规划与 scripted 规划产出完全一致的评分结果，属预期——决策权在规则层）。
- 未测：DeepSeek（未授权）、生产池 1092、mito 域切片、并发/限流画像。
- 下一步可选：扩大 chloro 切片至全部 236 条（需另行确认 token 预算）；或维持当前收据、转入 G6。

## 5. 复现

```powershell
.venv\Scripts\python.exe scripts\eval_offline.py `
  --candidates D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2\data\organelle_v2\candidates `
  --limit 8 --match chloro --planner llm --out data\eval\g4_glm_final
# 安全失败演示（PowerShell 会话级覆盖密钥）
$env:GENOMEQC_API_KEY="sk-invalid"; .venv\Scripts\python.exe scripts\eval_offline.py `
  --candidates ... --limit 1 --match CHLORO-NORMAL-00 --planner llm --out data\eval\g4_badkey_demo
```

产物：`data/eval/g4_glm_final/aggregate.json`（含 planner 精确 id、model_totals、逐案例 model_usage）+ 每案例 `agent/QC_REPORT.json`、`agent/v2_output.json`、`grader/scorecard.json`。
