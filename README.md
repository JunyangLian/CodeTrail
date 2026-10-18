# GenomeQC Agent（AssemblyGenomics-Copilot V3）

面向细胞器基因组的交互式质量检查智能体：用户提供 GenBank 或标准化公开证据包及检查需求，
Agent 基于当前证据动态选择**只读工具**，执行生信质控、定位异常、核验证据，并生成可复核的 QC 决策报告。

> 规则负责事实，LLM 负责规划与解释，人对高风险处置保有审批权。
> 总控文件：[V3_MASTER_PLAN.md](V3_MASTER_PLAN.md)（唯一总控入口）。

## 当前状态

**V3.2 Release Candidate（RC-FINAL，commit `df02a07`）** — G0–G6 总控里程碑 + V3.1 进化闭环 + V3.2 能力增强全部完成。

- **G0–G6**：V2 资产审计与只读快照、Pydantic 合同 + V2Port 适配、LangGraph runtime（中断/恢复等价性）、FastAPI 七端点 + Streamlit 五页面、双模型受控验证（chloro 40 全量判定一致率 40/40）、冻结评分器对接、v2.1.1 基线升级
- **V3.1 进化闭环**：失败情报池 → 策略提议 → 审批硬门（模拟审批阻断实测）→ E2 回归池；S1/S2 真实 A/B（16 + 120 观测，含 503 冻结协议修复与断点恢复实测）
- **V3.2**：ContextBuilder（protected 块 + 逐调用溯源 receipt）、Failure Attribution 2.0（规则只播种、升级必经单因素干预）、Harness 稳健性注入（10 域）、144 观测重复 A/B（4 进程×5 线程）

测试：`206 passed`（Mock 全绿，CI 无网络；真实模型调用仅在授权下发生）。

## 最终发布证据（400 次真实付费观测）

| 实验 | 规模 | 关键结果 |
|---|---|---|
| S1 / S2（V3.1） | 16 + 120 观测 | 幻觉指针黄金对照；503 冻结协议修复双口径归档 |
| V3.2 DEV A/B | 144 观测（3 重复） | 无效指针 A=4/72 vs B=0/72；质量层 72/72 双臂满分 |
| 归因消融 | 24 观测 | C（仅证据块）0/12 vs D（token 等量填充）1/12 |
| **独立 Holdout** | **96 观测** | **8 个全新属 × 24 任务，双臂 48/48 满分、0 无效指针、0 critical false pass** |

- 有效性审计：[V3_2_AB_VALIDITY_AUDIT.md](docs/V3_2_AB_VALIDITY_AUDIT.md)（16 字段配置逐项对照 + 删证据负例排除 + 统计重分析）
- 归因终验：[V3_2_FAILURE_ATTRIBUTION_FINAL.md](docs/V3_2_FAILURE_ATTRIBUTION_FINAL.md)（8 类失败完整证据链：4 confirmed / 3 experimentally_supported / 1 无实战实例）
- 独立 Holdout：[V3_2_INDEPENDENT_HOLDOUT_REPORT.md](docs/V3_2_INDEPENDENT_HOLDOUT_REPORT.md)（NCBI 检索 → 隔离校验 → 先冻结后运行）
- 发布决策：[V3_2_FINAL_RELEASE_REPORT.md](V3_2_FINAL_RELEASE_REPORT.md) + [Manifest](V3_2_FINAL_RELEASE_MANIFEST.json)（38 文件 SHA256）

统计诚实声明：无效指针 4→0 为方向性/计数证据；Token 成本 +12.7~13.5% 为点估计（任务级配对 CI 含 0）；所有候选未经人工发布审批，active strategy 保持 promptv1。

## 运行

```powershell
# 环境（Python 3.11，uv 锁定）
uv sync --all-extras

# 离线测试（Mock 全绿，无任何网络/模型调用）
.venv\Scripts\python.exe -m pytest

# 离线 smoke：公开输入 → 工具事实 → 结构化 QC finding
.venv\Scripts\python.exe scripts\smoke_offline.py --packet examples\public_packets\chloro_C1_NC_013991_2_CHLORO-CDS-03

# G3 API + UI
.venv\Scripts\python.exe -m uvicorn genomeqc_agent.api.main:create_app --factory --host 127.0.0.1 --port 8000
.venv\Scripts\python.exe -m streamlit run ui\streamlit_app.py

# G5/G4 评测（脚本化 planner / 授权后真实模型）
.venv\Scripts\python.exe scripts\eval_offline.py --candidates ..\AssemblyGenomics-Copilot_V2\data\organelle_v2\candidates --limit 8 --match chloro [--planner llm]

# 最终回归（10 项稳定性 + 全套件）
.venv\Scripts\python.exe -m pytest tests\unit\test_final_stability.py -q
```

## 边界

- 默认离线 Mock；调用真实 GLM/DeepSeek 需另行授权（见总控 §6/§12）。
- V2 仓库（`../AssemblyGenomics-Copilot_V2`）保持只读冻结；V3 通过哈希锚定的快照复用其公开纯函数。
- 运行时禁止接触任何私有金标（expected/rubric/oracle）。
