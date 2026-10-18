# GenomeQC Agent（AssemblyGenomics-Copilot V3）

面向细胞器基因组的交互式质量检查智能体：用户提供 GenBank 或标准化公开证据包及检查需求，
Agent 基于当前证据动态选择**只读工具**，执行生信质控、定位异常、核验证据，并生成可复核的 QC 决策报告。

> 规则负责事实，LLM 负责规划与解释，人对高风险处置保有审批权。
> 总控文件：[V3_MASTER_PLAN.md](V3_MASTER_PLAN.md)（唯一总控入口）。

## 当前状态

**G0–G6 总控里程碑全部完成** — 状态标记均为 `implemented/tested`。

- G0：V2 资产审计（[V2_ASSET_AUDIT.md](docs/V2_ASSET_AUDIT.md)）+ V2 公开工具只读快照（哈希锚定）
- G1：Packet/Tool/Report Pydantic 合同、V2Port 适配层、4 个只读工具、离线 smoke
- G2：LangGraph StateGraph runtime（条件路由、有界恢复、SQLite checkpoint、中断/恢复等价性）
- G3：FastAPI 七端点 + Streamlit 五页面 + SQLite 业务账本（真实 HTTP 全链路实证）
- G4：双模型受控验证（[G4_GLM_RECEIPT.md](docs/G4_GLM_RECEIPT.md) / [G4_DEEPSEEK_RECEIPT.md](docs/G4_DEEPSEEK_RECEIPT.md)）——chloro 40 全量：GLM 40/40、DeepSeek 40/40，**判定一致率 40/40**
- G5：V2 冻结评分器对接（[EVAL_PROTOCOL.md](docs/EVAL_PROTOCOL.md)；隔离 grader 进程）
- G6：v2.1.1 基线升级完成（[V2_TO_V3_UPGRADE_REPORT.md](docs/V2_TO_V3_UPGRADE_REPORT.md)，零行为差异）

测试：`93 passed`（Mock 全绿，CI 无网络；真实模型调用仅在授权下经 `--planner llm` 发生）。

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
```

## 边界

- 默认离线 Mock；调用真实 GLM/DeepSeek 需另行授权（见总控 §6/§12）。
- V2 仓库（`../AssemblyGenomics-Copilot_V2`）保持只读冻结；V3 通过哈希锚定的快照复用其公开纯函数。
- 运行时禁止接触任何私有金标（expected/rubric/oracle）。
