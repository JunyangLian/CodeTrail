# V3_RELEASE_GUIDE — GenomeQC Agent 首版发布指南

> 版本：v0.1.0（release candidate）｜ 基线：commit `48ef4b8` ｜ v2_adapter：`v2.1.1-snapshot:7518adc`
> 前置阅读：[V3_MASTER_PLAN.md](../V3_MASTER_PLAN.md)（总控）、[V3_AGENT_ACCEPTANCE.md](V3_AGENT_ACCEPTANCE.md)（验收）

## 1. 系统要求

- Windows / Python **3.11**（uv 管理，`.python-version` 已 pin）
- V2 仓库只读存在（评测与快照 parity 需要；运行 Agent 本身不需要）
- 硅基流动 API key（仅 `--planner llm` 真实模型模式需要；离线/演示零网络）

## 2. 架构

```mermaid
flowchart TD
    U[用户] -->|上传 GenBank / 选演示包| UI[Streamlit 五页面]
    UI -->|HTTP only| API[FastAPI 七端点]
    API --> LED[SQLite 业务账本<br/>packets/jobs]
    API --> W[单 Worker 线程]
    W --> G[LangGraph StateGraph<br/>intake→build→mandatory→plan→policy→<br/>execute→verify→(recovery)→synthesize→persist]
    G --> CP[SQLite Checkpoint<br/>thread_id=run_id]
    G --> T[ToolRegistry 沙箱<br/>4 个只读工具]
    T --> SNAP[V2 v2.1.1 快照<br/>SHA256 锚定 10 文件]
    G --> P[Planner: scripted 或授权 LLM]
    P -.规划 only.-> G
    G --> R[QCReport JSON+MD<br/>证据指针可回溯]
    SNAP -.私有 gold 仅限隔离进程.-> EVAL[V2 grader<br/>独立 venv 子进程]
```

职责底线：规则负责事实（只读工具），LLM 只规划与解释（PlannedAction 合同 + policy gate），人对高风险处置保有审批权。

## 3. 安装

```powershell
cd D:\1_yanjiusheng\AssemblyGenomics-Copilot_V3
uv sync --all-extras          # Python 3.11 + uv.lock 精确复现
copy .env.example .env        # 按需填 GENOMEQC_API_KEY（git-ignored）
.venv\Scripts\python.exe -m pytest          # 93 passed 预期
.venv\Scripts\python.exe scripts\check_secrets.py   # CLEAN 预期
```

## 4. 运行

### 4.1 离线 Agent（零网络）

```powershell
.venv\Scripts\python.exe scripts\smoke_offline.py --packet examples\public_packets\chloro_C1_NC_013991_2_CHLORO-CDS-03
```

### 4.2 API + 前端

```powershell
.venv\Scripts\python.exe -m uvicorn genomeqc_agent.api.main:create_app --factory --host 127.0.0.1 --port 8000
.venv\Scripts\python.exe -m streamlit run ui\streamlit_app.py
# 浏览器 http://127.0.0.1:8501 ；OpenAPI http://127.0.0.1:8000/docs
```

端点：`POST /api/v1/packets`（上传 GBFF）、`POST /api/v1/jobs`、`GET /api/v1/jobs/{id}`、
`GET .../events`、`GET .../report?format=json|markdown`、`POST .../resume`、`POST .../cancel`、`GET /healthz`。

### 4.3 真实模型评测（需已授权 provider）

```powershell
$env:GENOMEQC_MODEL_PROVIDER="glm"      # 或 deepseek（均已在授权集合）
$env:GENOMEQC_MODEL_ID="zai-org/GLM-5.3-Flash"
.venv\Scripts\python.exe scripts\eval_offline.py --candidates ..\AssemblyGenomics-Copilot_V2\data\organelle_v2\candidates --limit 40 --match chloro --planner llm --out data\eval\<tag>
```

授权 providers：`eval_offline.AUTHORIZED_PROVIDERS = {glm, deepseek}`——新增第三模型必须先经用户授权并扩充该集合。

## 5. 发布物清单

| 产物 | 位置 |
|---|---|
| 代码 + 合同 | `src/genomeqc_agent/`（93 tests） |
| 双模型收据 | `docs/G4_GLM_RECEIPT.md`、`docs/G4_DEEPSEEK_RECEIPT.md` |
| 评测协议 + 跨域 | `docs/EVAL_PROTOCOL.md`、`docs/V3_CROSS_DOMAIN_REPORT.md` |
| Agent 审计 | `docs/V3_AGENT_TRACE_REPORT.md`（+ `docs/trace_audit.json`）、`docs/V3_AGENT_ACCEPTANCE.md` |
| 升级对账 | `docs/V2_TO_V3_UPGRADE_REPORT.md`、`docs/V2_ASSET_AUDIT.md` |
| 快照绑定 | `src/genomeqc_agent/adapters/v2_snapshot/SNAPSHOT_MANIFEST.json`（v2.1.1 @ 7518adc） |
| 演示包 | `examples/public_packets/`（3 个 V2 公开视图 + 1 个 V3 合成缺陷包） |

## 6. 已知边界（不得宣称）

- chloro/mito 各 40/20 案例 DEV 切片；**非** held-out、非生产池（1092 在 V2 服务器侧）、非全域性能结论。
- LLM 仅规划；`evidence_support/action_quality` 按 V2 设计留待人工（AI 代理证据维宽松已被 v2.1.1 盲评实证）。
- API 本地单用户（127.0.0.1）；公网部署必须先加认证/HTTPS/限流。
- 成本口径为 tokens，单价未配置。

## 7. 回滚

- V3 内部：`git revert` / 检出前一 commit（`48ef4b8` 为根）。
- v2_adapter 基线：见 `docs/V2_TO_V3_UPGRADE_REPORT.md` §5（两基线快照字节相同，回滚仅版本串变化）。
- V2 冻结资产永不回写。
