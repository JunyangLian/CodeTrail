# EVAL_PROTOCOL — V3 ↔ V2 Benchmark 评测协议（G5）

> 版本：1.0 ｜ 日期：2026-10-17 ｜ 状态：implemented/tested（见 §6 首轮结果）
> 本协议约束 V3 输出如何与 V2 冻结基准对接评分。**私有金标只存在于隔离评测进程**。

## 1. 两阶段架构与隔离边界

```text
Phase A（agent 进程，V3 venv）          Phase B（grader 进程，V2 venv python）
┌────────────────────────────┐        ┌──────────────────────────────────┐
│ QCGraph.run(candidate_dir) │        │ scripts/eval_grader_worker.py    │
│  只读 task.md + artifacts/  │  ───▶  │  sys.path += [V2 repo, V2/bench] │
│  private/ 永不打开          │ 映射后  │  import deterministic_grader     │
│  → QC_REPORT.json          │ 文本   │  read private/expected.json      │
│  → v2_output.json (映射)    │        │  → scorecard.json                │
└────────────────────────────┘        └──────────────────────────────────┘
```

| 边界 | 机制 |
|---|---|
| 私有目录不可达 | V3 沙箱 `visible_files` 白名单 + `PacketFiles.FORBIDDEN_NAMES`；candidates 的 `private/` 天然不在白名单 |
| 代码级隔离 | 静态守卫 `test_v3_runtime_never_imports_private_grading_modules`：`src/genomeqc_agent/` 禁止 import oracle/grader/grade_output 或读 private 路径 |
| 进程级隔离 | 评分只发生在 `scripts/eval_grader_worker.py`，以 **V2 仓库自己的 venv** 解释器运行（`--v2-python` 可覆盖）；V3 venv 不装 jsonschema，无法评分 |
| 数据级隔离 | 聚合结果（aggregate.json）只含分数/判定/分母；私有 token 值不出现在 V3 仓库任何文件（负测试锁定） |
| 双 sys.path | V2 `harness_common` 顶层导入 `visible_input` → worker 同时注入仓库根与 `bench/`（审计 §5.5 已记录的 V2 耦合） |

## 2. QCReport → V2 合同映射（`src/genomeqc_agent/eval/adapter.py`）

V2 冻结评分器（`organelle_v2.rubric.deterministic_grader/v1`）的输出合同：
`verdict ∈ {pass,warn,block,rollback}`、`root_cause ∈ {none, cross_file_id_mismatch, coordinate_out_of_bounds, stats_conflict}`、`observed_defect`、`evidence[≥1] {pointer, note}`、`action`。

| V3 | V2 | 规则 |
|---|---|---|
| `qc_decision=pass/warn/block` | `verdict` 同名直映 | `insufficient_evidence` **不映射**（V2 无对应诚实值），计入 skipped 分母 |
| block 的 `coordinate_bounds` | `coordinate_out_of_bounds` | 单一 block 类时映射 |
| block 的 `cross_file_protein_id` | `cross_file_id_mismatch` | 同上 |
| block 的 `stats_consistency` | `stats_conflict` | 同上 |
| block 的 `multipart_structure` | **无对应 token，不映射** | V2 root_cause 枚举无此缺陷类；跳过并记录，不猜测 |
| 多类 block 并存 | **不映射**（multi-block） | 单输出合同无法诚实表达 |
| findings 的 `evidence_refs` | `evidence[].pointer` | 形式同为 `artifacts/<file>:<locator>`，与 V2 指针合同天然兼容 |
| blocked findings 摘要（含公开 feature_id/protein_id） | `observed_defect` | 证据支撑的定位信息，全部来自工具事实 |

**分母纪律**：`cases_total = graded + skipped_phase_a + skipped_not_mappable + phase_b_errors`，任何跳过都带原因，不缩水分母。

## 3. 评测流程（复现命令）

```powershell
# Phase A+B 一条命令（8 个 chloro 案例）
.venv\Scripts\python.exe scripts\eval_offline.py `
  --candidates D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2\data\organelle_v2\candidates `
  --limit 8 --match chloro
# 输出：data/eval/<时间戳>/aggregate.json + 每案例 agent/ 与 grader/ 产物
```

Phase B 可单独重放：`<V2 venv python> scripts/eval_grader_worker.py --case <dir> --input v2_output.json --out scorecard.json`（同输入字节 ⇒ 同 scorecard，确定性可重放）。

## 4. 评分维度与已知合同差距

- **自动评分**：output_contract / verdict / root_cause / decision_joint / defect_localization / evidence_locator_validity / critical false-pass gate。
- **pending_human**：`evidence_support` 与 `action_quality` 由 V2 冻结设计永不自动评分（v2.1.1 人工复核教训：AI 代理在 es 维系统性宽松，MAE 0.675）——V3 同样**不得**自动声称证据充分，报告的 `evidence_validity` 只回答指针可解析性与事实集合覆盖。
- **已知差距**（如实记录，不修复不掩盖）：
  1. V3 `warn` 与 `insufficient_evidence` 决策在 V2 合同中无落点（当前跳过）；
  2. `multipart_structure` 单独成 block 时跳过评分；
  3. V3 不产出 V2 的 `rollback` 语义（组合期 block 覆盖）。

## 5. 来源级划分纪律

- 评测样本只能来自 `data/organelle_v2/candidates` 的公开视图；**不复制 private/ 内容到 V3**。
- 看过的样本不冒充 held-out：对生产池（1092 条服务器侧）的任何评测须走 V2 侧流程并单独申报；本协议当前仅覆盖本地 candidates DEV 切片。
- 任何"未见组"结论必须有对应 V2 收据支撑，否则不声称。

## 6. 首轮结果（2026-10-17，8 案例 chloro DEV 切片）

| 分母 | 值 |
|---|---|
| cases_total | 8（NC_013991_2 与 NC_082429_1 两来源 × NORMAL/CDS-03/ID-01/META-05） |
| graded | 8（Phase A 0 失败、not_mappable 0、Phase B 0 错误） |
| judgment | **pass 8 / fail 0** |

- 全部 scorecard：output_contract=1、verdict 得分 1、`evidence_locator_validity.all_resolved=true`。
- **边界声明**：本切片是 ScriptedPlanner（FakeModel，无 LLM）+ 单域（chloroplast）+ 每来源 4 条件的小样本 DEV 验证，**不构成**全域性能、held-out 或双模型对照结论；LLM 规划的对应结果属 G4（待授权）。
- 首轮曾出现 2 类真实缺陷并在修复后复测归零（过程留档）：① 跨 run 模型预算计数泄漏（state 化修复）；② pass/定位报告未携带完整事实指针（§3 Evidence Verifier 纪律修复——pass 结论现在引用全部统计指针与具体 feature/protein ID）。
