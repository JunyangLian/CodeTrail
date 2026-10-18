# V2_ASSET_AUDIT — V2 源码资产审计（V3 G0 产物 · 深化版 v2）

> 首版：2026-10-17 ｜ 深化：2026-10-17（第二轮）
> 审计方式：直接读取 V2 本机源码与 git 历史，非仅凭验收报告推断。所有导入符号均经实际打开文件核验。
> **V2 仓库未被本审计修改**（全部操作为只读；每轮复核 `git status` 均为空）。

## 0. 本轮深化范围（相对首版）

| 新增维度 | 章节 |
|---|---|
| V2 release 演进与测试基线更新（510→517） | §1、§8 |
| V2 测试基础设施盘点（含答案泄漏负测试矩阵） | §7 |
| 私有/公开隔离机制的源码级验证 | §7.2 |
| git 历史中的关键修复与决策教训 | §8 |
| 模型配置结构（models.yaml，密钥不落盘） | §9 |
| V2 模型输出合同 schema | §10 |
| 快照锚点有效性复核（00e3424→2f49258 区间 diff=0） | §11 |

## 1. 审计对象与状态

| 项 | 首版值 | 本轮复核值 |
|---|---|---|
| V2 仓库 | `D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2` | 同左 |
| 导出时 HEAD | `00e3424`（Release checklist） | 当前 HEAD **`7518adc` = tag `v2.1.1`（Stable handoff：P0-1/P0-2/P0-3/P1-1 全 DONE）**；快照源文件在 00e3424..7518adc 区间 **diff = 0 行**（历次提交仅变更文档/收据/工具树，未触及快照源），快照内容继续有效 |
| 工作树状态 | 干净 | 干净（0 modified / 0 untracked，本轮实测） |
| 验收文档 | `bench/organelle_v2/docs/V2_FINAL_ACCEPTANCE.md`，SHA256 `4fc296c0…3739bba5` | 同左（未变） |
| 测试基线 | 验收报告记录 510 passed + 18 skipped | v2.1.1 基线 **522 passed + 18 skipped**（交接报告 §3 实测命令表：517 + 盲化测试 1 + E2 回归 4） |

## 2. 逐文件核验结果（可复用公开工具）

### 2.1 纯函数模块（零 V2 内部依赖，可直接快照复用）

| V2 文件 | 关键符号 | 依赖 | V3 用途 |
|---|---|---|---|
| `bench/visible_input.py` | `visible_files(case)` 白名单枚举（拒符号链接、拒 `meta.json`/`expected.json`、拒逃逸 artifacts 的路径） | 仅 stdlib | Packet 公开视图闸门 |
| `bench/organelle_v2/recovery/facts.py` | `public_facts(files)`、`FACTS_VERSION='organelle-public-facts/v1'`；`facts_tool()` 是 Inspect 包装，**核心计算是纯函数** | 仅 stdlib | `organelle_summary` 工具 |
| `bench/organelle_v2/recovery/verify.py` | `verify_pointers(final_content, case_dir)`、`_resolve(name, locator, files)`（JSON 顶层键/1-based 行/子串三类 locator）、`correction_feedback(unresolved)` | 仅 stdlib | `verify_evidence_pointer` 工具 |
| `bench/organelle_v2/common.py` | `canonical/digest/read_json/write_json/sha_of_tree`（canonical UTF-8 JSON、拒符号链接） | 仅 stdlib | 序列化/哈希辅助 |
| `bench/organelle_v2/normalize/genbank.py` | `parse_genbank(text)` → `GenBankRecord`（自研解析器，含 `Location`） | 仅 `re` | 单份 GenBank 输入解析 |
| `bench/organelle_v2/normalize/seqio.py` | `parse_fasta/render_fasta` | 仅 stdlib | FASTA 读写 |
| `bench/organelle_v2/normalize/feature_contract.py` | `FEATURE_COLUMNS`、`COORDINATE_SYSTEM='1-based-inclusive'`、`rows_from_record/render_features_tsv/parse_features_tsv/group_parts` | stdlib | features.tsv 合同 |
| `bench/organelle_v2/normalize/translation.py` | 翻译辅助 | 仅 stdlib | （备用） |

### 2.2 需 import 补丁的模块（补丁已快照并有漂移测试看守）

| V2 文件 | 关键符号 | 原依赖链问题 | V3 快照补丁 |
|---|---|---|---|
| `bench/inspect_adapter/readonly.py` | `PublicFiles`（白名单 + 大小限额 128KB/256KB + UTF-8 校验；`manifest/text/read_lines/fasta/table/stats` 等只读方法） | `from bench.v2.runtime import visible_files` → `bench/v2/runtime.py` 顶层执行 `from harness_common import …` 与 `from visible_input import packet, visible_files`（本轮实测第 10/13 行），**要求仓库根与 bench/ 双目录同时在 sys.path**，运行时不可移植 | 仅重定向该 import 行到快照 `visible_input` |
| `bench/organelle_v2/normalize/genbank_to_packet.py` | `build_packet/compute_sequence_stats/write_packet/read_artifacts/read_packet/read_case/convert_file`、`PUBLIC_ARTIFACTS`、`STAT_DEFINITIONS`（含 2026-10-16 forward-only 口径修复注释） | 3 行 `bench.*` import（common、normalize、genbank） | 重定向到对应快照模块 |

### 2.3 不复用 / 禁止复用

| V2 部分 | 原因 |
|---|---|
| `recovery/run.py`（37KB）、`recovery/tasks.py`、`recovery/dataset.py` | 耦合 Inspect AI Solver、`inspect_integration.tasks`、`oracle.audit`、`dataset.freeze`；属离线评测执行器，不进 V3 运行时 |
| `oracle/audit.py`、`oracle/independent_checks.py`、`rubric/*`、任务 `private/expected.json`、`mutation.json`、`rubric.json` | 私有金标/评分器。V3 Agent 运行时与模型 API 路径**禁止接触**；仅隔离评测进程（G5）可引用 |
| `inspect_integration/run_live.py`（50KB） | Inspect Runner，不嵌入 FastAPI |
| `bench/v2/*` 收据、`bench/*/RUN_PLAN.json`、账本、`models.yaml`、`bench/v2/history/v2-run-*/` | V2 实验账本与凭据配置；V3 新建独立预算账本，**不继承 SESSION_CAPS** |
| `bench/inspect_adapter/readonly.py` 的 `annotation_counts`/`interval_counts` | 面向 V1 表结构（`-Annotated` 列、regions TSV），organelle packet 不含这些列；随快照保留但 V3 工具注册表暂不暴露 |

## 3. 数据资产（公开部分）

- `data/organelle_v2/normalized/source_<acc>/public/artifacts/{genome.fa, features.tsv, proteins.faa, sequence_stats.json}`：33 个标准化源包（本机实测目录数）。
- `data/organelle_v2/candidates/<task_id>/{task.md, artifacts/, private/}`：任务案例；**V3 仅取 `task.md + artifacts/` 公开部分**，`private/` 一律不复制。
- V3 已导出 3 个演示包（公开部分）至 `examples/public_packets/`：
  `mito_M1_NC_002333_2_MITO-NORMAL-00`、`chloro_C1_NC_013991_2_CHLORO-NORMAL-00`、`chloro_C1_NC_013991_2_CHLORO-CDS-03`（后者为含 CDS 缺陷的公开视图；不含 private）。
- 许可口径：源数据为 NCBI RefSeq/GenBank 公开记录的确定性转换（见 organelle_v2 README 流水线），随 V2 仓库 LICENSE 分发口径复用。

## 4. V2 报告事实 vs 源码证据对照（G0 核验表）

| 验收报告声明 | 源码核验 |
|---|---|
| `organelle_summary` 事实工具 | ✅ `recovery/facts.py::facts_tool`，核心 `public_facts` 为纯函数 |
| 指针机械校验 + 有界纠错 | ✅ `recovery/verify.py::verify_pointers/correction_feedback`，自包含、不导 grader |
| 只读证据工具、路径白名单 | ✅ `inspect_adapter/readonly.py::PublicFiles`（绝对路径/`..`/反斜杠/空字节全拒绝，128KB/256KB 限额） |
| GenBank → 公开证据包 | ✅ `normalize/genbank_to_packet.py`（公开四件套 + 私有 provenance 分离） |
| 域任务库 984+236 | 数据目录实测存在（normalized 33 源包、candidates 任务目录）；全量收据复核属 V2 职责，不在本审计重跑范围 |
| 模型 `glm-5.3-flash` / `deepseek-v4.1-flash` | 验收报告 §1.2 记录；`bench/models.yaml` 结构为 `version/price_date/currency/models`（本轮实测前 4 行），密钥经 env 引用不落盘——**本审计未读取其密钥内容，也不复制该文件** |

## 5. 缺口与风险（V3 适配注意）

1. **V2 无独立可导入的 `lookup_feature` API**——feature 级查询逻辑分散在 TSV 合同里；V3 自建确定性 `lookup_feature` 工具（只读，基于快照 `feature_contract` 解析）。
2. **`PublicFiles` 大小限额**（128KB/256KB）对叶绿体包不足，且 V1 `__init__` 硬编码字面量 → V2 已有 `PublicFilesV2`（512KB/1.5MB，`inspect_integration/readonly.py`）以**覆写 `__init__`** 方式放宽；V3 `PacketFiles` 按同口径同手法实现。
3. **`visible_files` 要求 case 含 `task.md`** → V3 packet 目录合同定为 `task.md + artifacts/`（与 V2 case 布局一致）；GenBank 单文件输入先由 `packet_adapter` 转换生成该布局。
4. **V2 仓库仍在移动**：本轮 HEAD 已从快照锚点 `00e3424` 前进到 `2f49258`；已验证两提交间快照源文件 diff = 0。G6 阶段按 release tag 重新对账，若源文件有实质变更需重新导出快照并更新 manifest。
5. V2 代码风格为顶层模块导入（`from harness_common import …`、`from visible_input import …`），任何直接 `sys.path` 注入运行 V2 包的方案都脆弱 → V3 采用**快照 + manifest 哈希锚定**，不做运行时依赖 V2 仓库。

## 6. 快照清单与完整性

- `src/genomeqc_agent/adapters/v2_snapshot/SNAPSHOT_MANIFEST.json`：10 个文件的 original/snapshot SHA256、补丁说明、导出时 HEAD、验收文档哈希。
- 完整性由 `tests/unit/test_snapshot_parity.py` 持续强制：
  - 测试 A：快照文件哈希 == manifest 记录（防 V3 侧漂移）；
  - 测试 B：若 V2 仓库存在，快照 == V2 当前源文件（8 个未补丁文件逐字节相等；2 个补丁文件仅允许 import 行差异），V2 不可访问时 SKIP（不失败）。

## 7. V2 测试基础设施盘点（本轮新增）

`bench/organelle_v2/tests/` 下 20 个测试文件 + conftest/fixtures，是 V3 测试矩阵设计的直接参照：

### 7.1 关键测试文件与 V3 对应关系

| V2 测试 | 覆盖机制 | V3 现状/计划 |
|---|---|---|
| `test_no_answer_leakage.py` | 私有文件不进模型视图、私有值不出现在公开字节、task.md 跨任务类型逐字节一致、task.md 不提及模板 ID、planted expected.json 被拒 | V3 已有等价项（`test_path_safety.py::test_private_files_never_enter_public_view`）；**"task.md 无泄漏提示"与"私有值不出现在公开字节"两类负测试 V3 尚未覆盖**，G5 评测侧补齐 |
| `test_generator_oracle_independence.py` | 生成器与 oracle 相互独立（防自评自） | V3 运行时无 oracle；G5 评测进程复用该独立性原则 |
| `test_public_files_v2.py` | PublicFilesV2 限额与行为 | V3 `PacketFiles` 已按同口径实现并有对应测试 |
| `test_packet_and_oracle.py` | packet 构建与 oracle 一致性 | 快照转换路径已有 round-trip 测试 |
| `test_scoring_contract.py` | 评分输出合同 | V3 `QCReport` Pydantic 合同替代；G5 对齐 |
| `test_genbank_parser.py` / `test_translation.py` | 解析器与翻译正确性 | 快照逐字节复用，parity 测试兜底 |
| `test_smoke_budget.py` | 冒烟预算 | V3 `test_budget_exhaustion_stops_tool_calls` 等价 |

### 7.2 私有/公开隔离机制的源码级验证

`test_no_answer_leakage.py` 确立的隔离矩阵（本轮逐行读取确认）：

- **禁止文件名**：`expected.json / rubric.json / mutation.json / meta.json / oracle_report.json` 不得出现在 `visible_files` 输出中；
- **禁止公开字节中的私有标记**：`acceptable_decisions`、`defect_tokens`、`oracle_check`、`"task_type"`、`declared_root_cause`、`false positive`，以及 `private/expected.json` 的 `notes` 值；
- **task.md 无歧义**：所有任务类型（含 normal 与缺陷型）的 task.md **逐字节相同**，且不含任何模板 ID；
- **负向注入**：在 artifacts/ 植入 `expected.json` 必须被 `visible_files` 抛 `ValueError` 拒绝。

V3 `packet_adapter.public_view_files` 复用了文件名黑名单（`meta/expected/rubric/mutation`），**尚未包含 `oracle_report.json`**——本轮已在 §11 记录为 G1.1 修补项。

## 8. git 历史中的关键修复与决策教训（本轮新增）

从 `git log` 提取的对 V3 有直接约束力的决策：

| 提交 | 教训 |
|---|---|
| `b34455e` forward-only STAT_DEFINITIONS 修复 | **坐标/统计口径 bug 的修复方式是"改口径定义+全量确定性重算"，不是修补个别结果**。V3 若调整统计口径，必须走同路径：改定义 → 重算全部演示包 → 更新测试基线 |
| `6a87ded` prompt few-shot A/B 否决 | prompt 路线修不了"自相矛盾的数据"——**数据缺陷必须在数据层修复，不能指望模型侧绕过**。V3 的 QC 规则同理：工具事实优先，模型解释服从工具 |
| `9a7ef43` grader malformed-pointer 修复 + 确定性重评 | 评分器 bug 修复后必须**确定性重评全量**，不接受"新旧混排"。V3 报告合同变更时同样适用 |
| `c743c92` sync-channel 只读审计 + session cap 240→400（经用户授权） | 预算上调需要**显式用户授权**——V3 的 `GENOMEQC_MAX_*` 预算变更沿用此纪律 |
| `276d941` 服务器修复后 96/96 全绿 | 基础设施故障修复后需**重跑受影响全部槽位**而非只验证修复点 |
| `2f49258` release 基线刷新 510→517 | release 后仍可增补合同测试并刷新基线；V3 每个里程碑关闭时同样刷新自己的测试基线记录 |

## 9. 模型配置结构（本轮新增，密钥不落盘）

`bench/models.yaml` 实测结构（仅前 4 行键名）：`version: 1`、`price_date`、`currency: CNY`、`models:` 列表。多份历史副本位于 `bench/v2/history/v2-run-*/models.yaml` 与 `bench/plans/`。

**V3 纪律**：不复制任何 models.yaml；`.env.example` 仅存变量名；真实密钥仅存在于用户本地 `.env`（git-ignored）；价格/币种口径若需要，G4 阶段由用户显式提供。

## 10. V2 模型输出合同（本轮新增）

`bench/organelle_v2/schemas/model_output.schema.json`（1004 字节）定义 V2 模型响应的 JSON Schema。V3 对应物为 `src/genomeqc_agent/schemas/report.py::QCReport`（Pydantic）。**差异说明**：V2 输出 verdict 枚举为 `pass/warn/block/rollback`，V3 报告合同为 `pass/warn/block/insufficient_evidence`（rollback 在组合期映射为 block，见 report.py 模块 docstring）——此为有意合同演进，已在代码注释与本文档双重记录。

## 11. 快照锚点有效性复核与待办（本轮新增）

- 快照锚点：`00e3424`；当前 V2 HEAD：`2f49258`；两提交间快照源文件 diff = **0 行**（仅 2 个文档变更）→ **快照内容继续有效，manifest 无需重导出**；G6 升级对账时以 release tag 为准重新验证。
- 待办（G1.1 小修补，不阻塞 G2）：
  1. `packet_adapter.public_view_files` 私有文件名黑名单补 `oracle_report.json`（对齐 V2 `FORBIDDEN_ARTIFACT_NAMES`）；
  2. G5 评测侧补"私有值不出现在公开字节"与"task.md 跨任务类型一致性"两类负测试（参照 V2 `test_no_answer_leakage.py`）。
