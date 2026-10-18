# V3_AGENT_ACCEPTANCE — Agent 能力验收与首版发布验收（P0–P2）

> 日期：2026-10-18 ｜ 基线：commit `48ef4b8`（首提交）｜ v2_adapter `v2.1.1-snapshot:7518adc`
> 验收总表：每项给出证据与可复现命令；OPEN 项如实标注。

## P0 — 安全与版本

| # | 项 | 状态 | 证据 |
|---|---|---|---|
| 1 | 凭据暴露检查 | ✅ | `scripts/check_secrets.py`：101→102 个被跟踪文件零凭据；`.env` 仅在本地且被 `.gitignore` 排除（`git ls-files` 验证未跟踪）。**OPEN（需用户操作）：SiliconFlow key 曾出现在聊天输入中，请到 SiliconFlow 控制台轮换**——轮换后只需更新本地 `.env`，代码零改动 |
| 2 | Git 初始化 | ✅ | `git init`；`.gitignore` 覆盖 `.env/data/.venv/运行产物`；secret 扫描纳入提交流程（commit 前必跑） |
| 3 | 首个可复现版本 | ✅ | commit `48ef4b8`（102 files, +19,577）；快照绑定 v2.1.1 @ 7518adc 由 `SNAPSHOT_MANIFEST.json` 携带（8/10 文件逐字节一致 + 2 个设计补丁文件的漂移测试看守） |

## P1 — Agent 能力

| # | 项 | 状态 | 证据 |
|---|---|---|---|
| 4 | 六维 Trace 审计 | ✅ | [V3_AGENT_TRACE_REPORT.md](V3_AGENT_TRACE_REPORT.md)：动态路由（两模型均 2 条事实驱动分支）、工具调用零 schema 拒绝、规划 89/101 次（tool+synthesize 混合）、恢复纪律（图内 0 触发 + 轮外 repair 9/9 闭环 + planner 失败零重试）、证据解析率 GLM 99.34%（幻觉指针被拦截降级 partial 的黄金案例）/ DS 100%、成本延迟分列 |
| 5 | QC pass ≠ eval success | ✅ | 概念二分已在 [V3_AGENT_TRACE_REPORT.md](V3_AGENT_TRACE_REPORT.md) §0 冻结：QC verdict=工具事实无异常；eval judgment=报告质量。两者分列陈述、互不推导 |
| 6 | 跨域验证 | ✅ | [V3_CROSS_DOMAIN_REPORT.md](V3_CROSS_DOMAIN_REPORT.md)：mito 20 案例（5 来源 × 4 条件），20/20 graded、20 pass/0 fail（scripted planner，零模型调用） |
| 7 | 私有 Oracle 隔离 | ✅ | 运行时三重隔离：沙箱黑名单（`PacketFiles.FORBIDDEN_NAMES`）+ 静态守卫（`test_v3_runtime_never_imports_private_grading_modules`）+ 进程隔离（grader 仅在 V2 venv 子进程）。负测试全绿（含 V2 candidates 实测抽查与 task.md 一致性） |

## P2 — 端到端与发布

| # | 项 | 状态 | 证据 |
|---|---|---|---|
| 8 | FastAPI+前端 E2E | ✅ | 真实 uvicorn 全链路（`scripts/acceptance_e2e.py`）：upload `upload_1e6cf232321c` → submit → status `succeeded` → events 11 条（intake→build→mandatory→plan→policy→execute→verify→synthesize→validate→finish 全节点）→ JSON 报告 `pass` → Markdown 下载 200/1346B |
| 9 | 故障恢复演示 | ✅ 可复现 | 两类均有确定性测试与演示入口：(a) 瞬时工具故障有界恢复——`.venv\Scripts\python.exe -m pytest tests\integration\test_langgraph_runtime.py::test_transient_error_recovers_within_bounds`（flaky 工具→1 次 retry→succeeded，retry 计数留痕）；(b) 中断后 checkpoint 恢复——同文件 `test_interrupt_and_resume_preserves_facts`（interrupt→resume→事实无损、报告与不间断运行逐字节一致） |
| 10 | 文档 | ✅ | README（状态+运行）、[V3_AGENT_TRACE_REPORT.md]、[V3_CROSS_DOMAIN_REPORT.md]、[V2_TO_V3_UPGRADE_REPORT.md]、[EVAL_PROTOCOL.md]、[G4_GLM_RECEIPT.md]、[G4_DEEPSEEK_RECEIPT.md]、本文件、[V3_RELEASE_GUIDE.md]；架构图见 RELEASE_GUIDE §2 |

## 测试与质量基线

- `pytest`：**93 passed**（Mock 全绿；CI 无网络；真实模型仅在授权下经 `--planner llm`）
- `ruff`：clean；`scripts/check_secrets.py`：CLEAN（提交流程强制）

## 验收结论

P0 三项、P1 四项、P2 三项全部通过；唯一 OPEN 项为用户侧的 API key 轮换（不影响代码与发布物）。满足首版发布条件。
