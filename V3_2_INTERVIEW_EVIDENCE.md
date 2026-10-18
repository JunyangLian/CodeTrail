# V3.2 Interview Evidence（面试可核验证据清单）

原则：只记录可核验的真实贡献、实验指标、失败案例与工程修复；不杜撰人工评审、独立泛化提升或统计显著性。每条附验证途径（文件/命令/哈希）。

## 一、可核验的实验规模（全部真实 API 运行）

| 实验 | 规模 | 关键结果 | 验证 |
|---|---|---|---|
| S1 Smoke（V3.1） | 16 观测（GLM/DS × A/B × 2 案例） | 决策 parity 8/8；发现 DS/A 幻觉指针黄金对照 | `V3_S1_ARCHIVE_MANIFEST.json`（41 文件） |
| S2 正式（V3.1） | 120 观测（GLM A/B 80 + DS 40） | 1×503 基础设施失败 → 冻结协议 repair 双口径归档；计费 681,440 tok | `V3_S1/`+`data/archive/s2_experiment/`（SHA256 清单 248 文件） |
| V3.2 DEV A/B | 144 观测（12 任务 × 2 模型 × 2 条件 × 3 重复，4 进程×5 线程 20 并发） | 无效指针 A=4 vs B=0；质量层 72/72 双臂；2 次无效启动作废留痕 ~13k tok | `V3_2_REAL_AB_LEDGER.jsonl` + `data/archive/v32_ab/`（299 文件） |
| P2.2 消融 | 24 观测（C/D 两新臂） | C=0/12 vs D=1/12 → 证据块机制方向性支持 | `data/eval/final_round/p2_ablation/ablation_ledger.jsonl` |
| 独立 holdout | 96 观测（8 新属 × 3 任务 × 2 模型 × 2 条件） | 双臂 48/48 满分、0 无效指针、0 安全事件 | `data/eval/final_round/holdout_run/holdout_ledger.jsonl` |

总真实付费观测：**400**（16+120+144+24+96），全部带唯一格键账本 + per-thread checkpoint + 冻结计划先于运行。

## 二、发现并修复的真实缺陷（每条有根因+回归锁）

1. **ScriptedPlanner 混入真实实验**（S1）：runner 漏传 planner → 16 格零 token 假运行被 `tok=0` 暴露 → 全部 VOID 留痕重跑 → `is_real_model_run` 守卫 + 三个真实 runner 的授权硬门（子进程级回归测试）。
2. **断点恢复预算从零重算**（S2）：跨进程恢复后 BudgetGuard 未播种 → 可预算失控 → `BudgetGuard.from_ledger` + 回归锁。
3. **20 并发 planner 竞态**（V3.2）：LLMPlanner.last_usage 跨线程共享 → per-thread planner map + 实例隔离测试。
4. **node_build_packet 真实崩溃**（V3.2 P3 注入发现）：缺失 packet_dir 抛未处理 FileNotFoundError → 结构化安全失败 + 10 域注入回归。
5. **审计脚本 FAULT_CASES 前缀 bug**（S1/S2）：critical false pass 恒 0 → 修正 + false-pass 定义锁死为 `qc_decision=pass on fault`（与 judgment 彻底分离）。
6. **条件标签未接线**（V3.2）：B 臂首 4 格跑成基线 → per-cell strategy 注入修复 + 误标行删除留痕（8,130 tok 作废）。
7. **v2_snapshot 字节保真缺陷**（本轮 P4）：ruff 显式路径覆盖 exclude 重写快照 + 揭示 blob(LF) vs manifest(CRLF) 的 git 不可恢复性 → `restore_snapshot_bytes.py` 权威恢复（10/10）+ parity 测试绿。

## 三、方法学贡献（可复述、可答辩）

- **单因素受控实验设计**：promptv1 双臂唯一变量=上下文组装；16 字段配置逐项审计机器可读（`p1_audit/ab_config_diff.json`）；执行序偏倚披露（V3.2 24/24 A-first → holdout 修复为 12/12 平衡）。
- **删证据负例审计**（P1.2）：对"B 臂失败 4→0"系统排除 6 类作假路径（未生成/静默删/过滤/省略验证/强改 valid/评分双标）——结论：引用画像逐项相同（516=516），改善真实。
- **统计诚实修正**：发现并纠正自己此前报告的"任务级 CI 不重叠"错误声明（打印区间实际重叠；正确聚类单位下配对 CI [−3.79%, +28.65%] 含 0）→ 成本结论降级为"点估计 +12.7~13.5%，不显著"。**在最终发布文档中主动降级自己的结论**。
- **独立 holdout 构建**：NCBI eutils 检索 → V2 冻结生成器（14/14 域门+Oracle 通过）→ 六项隔离校验（含 216 份 DEV 基因组序列查重）→ manifest/计划先于运行冻结 → 运行时逐文件哈希重验。
- **归因系统 2.0**：规则只播种假设（8 类 × 4 级证据），升级必经单因素干预；无实战实例的类别诚实标注 `no_field_instance_mechanism_tested`。

## 四、明确的边界与未证明项（主动声明）

- 无效指针 4→0 的臂间差异：**方向性/计数证据**，holdout 双零无法判别；细粒度机制（证据块 vs token 体积）在 n=12/臂下不可分——标注 UNCONFIRMED。
- 成本差异：统计不显著，只有方向。
- promptv2：未进入正式真实 A/B，保持 DEV-validated。
- 所有候选未发布：active strategy 全程 promptv1，模拟审批阻断实测（不宣称已获人工批准）。

## 五、工程数字

- 自动化测试：**206 项全部通过**（20 个套件：contract 7 / api 19 / security 23 / integration 52 / unit 105…逐套件见 P4 报告）
- 交付文档：V3 系列报告 15+ 份；三个归档 manifest（S1 41 / S2 248 / V3.2 299 文件，全 SHA256）
- 代码资产：ContextBuilder（protected 块 + receipt 溯源）、Failure Attribution 2.0、BudgetGuard（账本播种）、shard/线程并行实验框架、NCBI holdout 流水线

## 六、一句话定位

构建并审计了一个带真实 LLM 的只读基因组 QC Agent 的完整评测基础设施：400 次付费观测、五轮冻结协议实验、对自己结论的主动统计降级、以及从幻觉指针到快照字节保真的 7 个真实缺陷的根因修复与回归锁定。
