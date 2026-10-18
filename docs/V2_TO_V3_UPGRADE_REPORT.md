# V2_TO_V3_UPGRADE_REPORT — v2_adapter 基线升级（阶段 B / G6）

> 日期：2026-10-18 ｜ 旧基线：`v2-acceptance-snapshot:00e3424087e5`（2026-10-17 验收快照）→
> 新基线：**`v2.1.1-snapshot:7518adc`**（tag `v2.1.1`，Stable handoff，工作树干净）
> 结论：**升级完成，零行为差异**。回滚路径见 §5。

## 1. 升级前核验（计划 §10 阶段 B 步骤 1–2）

| 项 | 结果 |
|---|---|
| V2 仓库状态 | HEAD `7518adc` == tag `v2.1.1`；工作树干净（0 modified / 0 untracked） |
| 快照源文件 diff（00e3424 → 7518adc） | **0 行**——10 个快照源文件在 v2.0.0→v2.1.1 全区间未变（历次提交仅触及文档/收据/工具树） |
| 接口/合同 diff | `visible_files` / `public_facts(FACTS_VERSION='organelle-public-facts/v1')` / `verify_pointers` / `build_packet` / `grade_output(v1)` 均未变化 |
| 私有资产 | 未复制、未读取（V2 仅只读；grader 仍在隔离 worker 进程内运行） |

## 2. 变更内容（V3 侧）

| 文件 | 变更 |
|---|---|
| `src/genomeqc_agent/config.py` | `V2_SNAPSHOT_COMMIT: 00e3424… → 7518adc`；新增 `V2_BASELINE_TAG='v2.1.1'`；`V2_ADAPTER_VERSION: v2-acceptance-snapshot:00e3424087e5 → v2.1.1-snapshot:7518adc` |
| `src/.../v2_snapshot/SNAPSHOT_MANIFEST.json` | 重导出：`source_commit=7518adc20c37…`；8 个未补丁文件 byte_identical=true（对 7518adc 逐字节核验）；2 个补丁文件（readonly.py / genbank_to_packet.py）按设计仅 import 行差异，可执行行漂移测试通过 |
| `tests/contract/test_tool_contracts.py` | 版本前缀断言更新为 `v2.1.1-snapshot:` |

**未变更**：快照 10 个文件的代码内容（字节级）；V2 仓库任何文件；历史收据（`data/` 下旧版本串保留原样，不覆盖）。

## 3. 新旧 Adapter 配对比较（步骤 3）

固定公开用例（8 条 chloro DEV 切片，scripted planner，确定性重放）：

| 指标 | 旧 Adapter（00e3424） | 新 Adapter（7518adc） |
|---|---|---|
| graded / 判定 | 8/8，pass 8 | 8/8，pass 8 |
| 逐案例 judgment 对照 | — | **完全一致（identical: True）** |
| grader 版本 | deterministic_grader/v1 | deterministic_grader/v1（未变） |

配对产物：旧 `data/eval/20261017T153045/` ↔ 新 `data/eval/g6_regression_scripted/`。

## 4. 回归与安全验证（步骤 4）

| 验证 | 结果 |
|---|---|
| 全量测试（unit/contract/security/integration，含快照 parity 与 live-parity 守卫） | **93 passed** |
| ruff | clean |
| 安全回归 | 伪造路径/private 文件/符号链接/超限/预算/未知工具拒绝——全部保持 |
| 中断恢复 | resume 等价性测试保持 |

## 5. 回滚路径（步骤 5）

本工作区未启用 git，回滚 = 将以下两点还原到旧基线（均已在旧收据中留档）：
1. `config.py`：`V2_SNAPSHOT_COMMIT='00e3424087e570f22f322c9aa9f64d116857536f'`、`V2_ADAPTER_VERSION=f"v2-acceptance-snapshot:{V2_SNAPSHOT_COMMIT[:12]}"`；
2. `SNAPSHOT_MANIFEST.json`：重导出前旧 manifest 哈希记录于 git 历史之外的第一轮审计文档 `docs/V2_ASSET_AUDIT.md` §6（旧值：`00e3424` 锚点）。
由于两基线快照源字节相同（diff=0），回滚实际不改变任何执行行为，仅改版本串。

## 6. 收据连续性声明

- 升级前所有运行收据（G1–G5、G4 GLM/DeepSeek 40 案例扩片）记录 `v2-acceptance-snapshot:00e3424087e5`，**保留不覆盖**；
- 升级后新运行记录 `v2.1.1-snapshot:7518adc`；
- 两版本号的快照源字节相同（§1），因此跨版本的结果可比，无需重跑历史用例。
