# V3.2 Failure Attribution 2.0 报告（P2）

> 实现：`src/genomeqc_agent/evolution/attribution.py` ｜ 测试：`tests/unit/test_attribution.py`（10 项）

## 1. 体系

- **8 个冻结类别**：model_capability / prompt_instruction / context_missing_or_truncated / tool_schema_or_argument / agent_state / infrastructure / dataset_or_scorer / unknown
- **证据四级**：observed → suspected → experimentally_supported → confirmed。规则只产 **suspected 假设**；只有单因素干预实验（`apply_intervention`）可升级为 experimentally_supported；confirmed 需人工确认（本轮回未发生）。
- **12 字段结构化记录**：failure_id / run_id / case_id / observed_failure / first_layer_category / root_cause_hypothesis / supporting_evidence / **alternative_explanations** / confidence / intervention_verified / suggested_fix / regression_ref。
- **不凭关键词定论**：指针错误在上下文完整时归 model_capability（中置信、带替代解释）；上下文 partial 时归 context_missing（同等地位替代解释）；503/超时高置信归 infrastructure 且明确"no model blame"；scripted 替身/预算重置归 agent_state；grader 错误归 dataset_or_scorer。

## 2. 八个真实历史故障的诊断回归（全部通过）

| 真实故障 | 断言 |
|---|---|
| SiliconFlow 503（S2 实录） | infrastructure / high / provider 归因 |
| `genome.fa:1` 幻觉指针（S1/S2 实录） | model_capability + **替代解释非空** + 非 high 置信 |
| checkpoint 恢复后预算重置（S2 实录） | agent_state_error / high |
| S1 误用 ScriptedPlanner（VOID 留痕） | agent_state_error / high（usage 判据） |
| cfp 评分语义错误（V3.1 审计发现） | dataset_or_scorer_error |
| 文件名对、Locator 错（黄金案例类） | model_capability + 定位层区分 |
| 工具参数违 Schema | tool_schema_or_argument + 单因素修复建议 |
| 上下文压缩后证据缺失（V3.2 注入） | context_missing_or_truncated / medium |

## 3. 受控归因实验（P2.3）

V3.2 主实验（P4）**本身就是单因素干预实验**：A/B 两臂同 prompt（promptv1）、同工具、同卫兵，唯一变量 = Context 组装；3 次重复。结果：无效指针观测 A=4 → B=0（72 观测/臂）。该实验使"指针类失败属 context 组织问题"的假设升级为 **experimentally_supported**（`apply_intervention(factor="context", outcome="improved")` 语义）。多因素场景（同时改 Prompt+Policy）不做单组件归因——协议在 `V3_2_REAL_AB_PLAN.json` 冻结。
