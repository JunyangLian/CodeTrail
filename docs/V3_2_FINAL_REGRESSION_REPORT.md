# V3.2 Final Regression Report（最终工程稳定性回归）

- 日期：2026-10-19（final round P4）
- 范围：总控指令列出的 10 项高风险核查点；不扩展新架构
- 新增回归套件：`tests/unit/test_final_stability.py`（10 项）
- 全量结果：**206 collected / 206 passed（pytest exit 0）**；ruff 全绿；secret 扫描 CLEAN（222 tracked files）
- 基线 commit：`c30a4f3`

## 10 项核查点 → 测试映射

| # | 核查点 | 状态 | 锁定测试 |
|---|---|---|---|
| 1 | 断点恢复后 Token/HTTP 账本不归零 | 已锁定 | `test_release_audit.py::test_budget_guard_restores_from_ledger`（+`empty_ledger_is_zero`）；V3.2 账本播种实测（run_v32_ab 从 2 份 ledger 播种） |
| 2 | 中断任务不重复计数 | **新增** | `test_final_stability.py::test_interrupted_run_not_double_counted`（账本=唯一事实源，done 集去重 + guard 播种一次） |
| 3 | 20 并发下每线程 Planner 状态独立 | **新增** | `test_planner_state_is_per_instance`（交错调用下 last_usage 实例隔离——V3.2 竞态修复的回归锁） |
| 4 | 每线程 Checkpoint 独立且可恢复 | **新增** | `test_checkpoint_dbs_are_isolated_and_resumable`（跨 db 线程不可见 + 本 db 可恢复） |
| 5 | Ledger 并发追加完整、无半行/重复 | **新增** | `test_ledger_concurrent_appends_complete`（8 线程×50 行 → 400/400 完整 JSON、无重复） |
| 6 | 模型错误不被误归因为 Scorer 错误 | **新增** | `test_model_error_is_never_scorer_error`（503→infrastructure；契约违例≠scorer；无 grader_error 永不产 dataset_or_scorer） |
| 7 | ScriptedPlanner 不得进入真实实验 | **新增+已锁定** | `test_real_runners_block_unauthorized`（三个真实 runner 无 `GENOMEQC_PAID_AUTHORIZED=true` 即 BLOCKED——子进程实测）；`test_scripted_planner_is_identifiable_and_not_a_real_run` |
| 8 | 无效证据不被上下文压缩静默删除 | **新增+已锁定** | `test_compression_never_drops_unresolved`（UNRESOLVED 指针在压缩下原样保留 + honest partial）；`test_context_builder.py`×8 |
| 9 | judgment 与 QC verdict 不混淆 | 已锁定 | `test_critical_false_pass_requires_qc_pass_on_fault_case`、`test_grader_judgment_pass_is_never_a_false_pass_input` |
| 10 | 模拟审批不能发布生产策略 | 已锁定 | `test_simulated_approval_cannot_trigger_publish`；active strategy 全程保持 promptv1 未动 |

## 并发纪律（总控要求）

- 本轮新增真实 API 运行：消融 24 观测（串行）+ holdout 96 观测（4 线程）；并发度按 96 观测 0 失败、限流未触发保持保守（4 线程 < V3.2 的 20）。
- 所有运行带完整账本（case/model/condition/repeat 唯一键、per-thread checkpoint、锁内追加+flush）。

## 审计中发现并修复的真实问题

1. **v2_snapshot 字节保真事件（本轮最重要发现）**
   - **现象**：P4 全量测试首次运行 `test_snapshot_parity` 4 项中 2 项失败（facts.py、genbank.py 哈希漂移；readonly.py 内容漂移）。
   - **根因链**：① 我在 `ruff check --fix` 中显式传入 src 路径，**覆盖了 pyproject 的 `extend-exclude = v2_snapshot` 保护**，重写了 5 个快照文件；② 恢复时发现更深一层结构缺陷：**提交的 blob 是 LF/混合行尾，而 SNAPSHOT_MANIFEST 与 V2 原件是 CRLF**——`core.autocrlf=true` 的 smudge 无法为混合行尾文件重建原始字节，即快照的字节保真依赖工作区文件不被任何工具重写，git 本身无法恢复（首次 `git checkout --` 后仍失败的原因）。
   - **修复**：`scripts/restore_snapshot_bytes.py`——unpatched 文件直接从 V2 冻结仓逐字节复制（parity 测试的同一权威），patched 文件（readonly.py 导出时新建 LF、genbank_to_packet.py V2 派生 CRLF）按 blob 原样/CRLF 归一双候选对 manifest 校验；**10/10 恢复，parity 4/4 通过**。
   - **影响评估**：漂移仅行尾（内容归一化后与 V2 原件逐行相同），未进入任何已交付实验产物；快照哈希断言在漂移期间本可拦截，但因历史运行从未触发重写而未被暴露。
   - **流程修正**：后续任何 ruff 调用不携带显式路径参数覆盖 exclude；恢复脚本入库作为标准工具。
2. **S2 死进程残留澄清**：会话恢复时发现一个 12+ 小时前终止的 S2 runner 后台日志（110/120 处截断）——确认为会话结束时的死进程残留，非活动任务，S2 数据由恢复运行补完，无污染（不计入本轮修复，仅留痕）。

## 新增测试明细（10 项）

`tests/unit/test_final_stability.py`：test_interrupted_run_not_double_counted、test_planner_state_is_per_instance、test_checkpoint_dbs_are_isolated_and_resumable、test_ledger_concurrent_appends_complete、test_model_error_is_never_scorer_error、test_real_runners_block_unauthorized[run_v32_ab/run_holdout_ab/run_p2_ablation]、test_compression_never_drops_unresolved、test_zero_token_success_is_not_a_real_model_run。

## 全量测试构成（206 = 逐套件实测）

contract 7 / api 19 / eval_protocol 2 / harness_robustness 11 / langgraph_runtime 12 / smoke_pipeline 8 / path_safety 23 / ab_preflight 12 / attribution 16 / context_builder 8 / eval_adapter 8 / evolution 23 / **final_stability 10** / flywheel 8 / llm_planner 7 / no_leakage 5 / refinement 8 / release_audit 9 / snapshot_parity 4 / v2_adapter 6。
