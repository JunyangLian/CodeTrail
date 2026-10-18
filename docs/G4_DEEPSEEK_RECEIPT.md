# G4_DEEPSEEK_RECEIPT — 真实模型受控验证收据（DeepSeek-V4.1-Flash @ 硅基流动）

> 日期：2026-10-18 ｜ 状态：implemented/tested（DeepSeek 单模型，与 GLM 同切片对标）
> 授权：用户 2026-10-18 指示"跑 deepseek-4.1-flash"（复用已授权的 SiliconFlow key）。
> 密钥仅存于 git-ignored `.env`；本收据无任何密钥。

## 1. 精确配置

| 项 | 值 |
|---|---|
| provider | `siliconflow`（与 GLM 同网关、同合同、同 `LLMPlanner`） |
| model_id（平台实测） | `deepseek-ai/DeepSeek-V4.1-Flash`（`/v1/models` 枚举确认） |
| 参数 | temperature=0, max_tokens=4096, gateway timeout 120s |
| 供应商行为差异 | 与 GLM 不同：**无强制 thinking 问题、无截断、无 503**——40 案例 0 次出错调用 |

## 2. 运行（chloro 40 全量，与 GLM 同切片）

| 项 | DeepSeek-V4.1-Flash | GLM-5.3-Flash（对照） |
|---|---|---|
| 分母 | cases_total=40；**graded=40**；skipped=0；phase_b_errors=0 | 40；graded=40（经 2 轮 repair）；still-failed=0 |
| judgment | **pass 40 / fail 0** | **pass 40 / fail 0** |
| requests | 101（0 出错） | 97（6 出错 + 2 轮 repair） |
| tokens | prompt 102,539 + completion 112,423 = **214,962** | ≈188,767（三轮合计） |
| 平均规划延迟 | ≈15.4 s/次 | ≈10–22 s/次（reasoning 更长、截断敏感） |
| repair 需求 | **0 轮** | 2 轮 |

## 3. 双模型一致性（同切片、同工具、同评分器）

- **verdict/judgment 一致率：40/40**（每案例逐一对表，disagreements=[]）
- 两模型的 QC 决定完全一致——符合架构预期：决定权在规则层（只读工具事实 + 确定性 composer），planner 只选择"下一个看什么"
- 成本/稳定性差异是 planner 工程属性：DeepSeek 本轮零基础设施故障；GLM 需 2 轮 repair + 8192 token 余量

## 4. 诚实边界

- chloro 单域 DEV 切片（本地 candidates 池全部 40 条；两来源 × 4 条件 × 5 重复）；**不构成** held-out、生产池（1092）或 mito 域结论
- LLM 仅规划；证据充分性判定仍由工具事实集合覆盖（master plan §3 纪律）
- 成本留空（单价未配置）；token 为精确计费口径

## 5. 复现

```powershell
$env:GENOMEQC_MODEL_PROVIDER="deepseek"; $env:GENOMEQC_MODEL_ID="deepseek-ai/DeepSeek-V4.1-Flash"
.venv\Scripts\python.exe scripts\eval_offline.py `
  --candidates D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2\data\organelle_v2\candidates `
  --limit 40 --match chloro --planner llm --out data\eval\g4_ds_chloro40
```

产物：`data/eval/g4_ds_chloro40/aggregate.json`（planner 精确 id、model_totals、逐案例 model_usage）+ 每案例 agent/ 与 grader/ 产物。
