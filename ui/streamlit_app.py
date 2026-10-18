"""GenomeQC Agent Streamlit UI (plan §7.2, extended in P4).

Talks to the FastAPI backend over HTTP only - no direct tool/model access,
no keys in the UI. Start the API first:

    uvicorn genomeqc_agent.api.main:create_app --factory --host 127.0.0.1 --port 8000
    streamlit run ui/streamlit_app.py

Pages: submit (upload + model/strategy selection), execution trace,
evidence view, refinement history, report, history/resume, examples,
strategy versions, human approvals.
"""
from __future__ import annotations

import httpx
import streamlit as st

API = "http://127.0.0.1:8000"
DOMAINS = ["vertebrate_mitochondrion", "chloroplast", "auto_detect_supported"]
TERMINAL = {"succeeded", "needs_human_review", "insufficient_evidence",
            "failed", "cancelled", "budget_exhausted"}
MODELS = {"Mock planner（离线，无付费）": ("scripted", None),
          "GLM-5.3-Flash（需付费授权）": ("llm", "zai-org/GLM-5.3-Flash"),
          "DeepSeek-V4.1-Flash（需付费授权）": ("llm", "deepseek-ai/DeepSeek-V4.1-Flash")}
STRATEGIES = {"promptv1（基线）": ("promptv1", {}),
              "promptv2 pointer-guard（候选策略）": (
                  "promptv2", {"evidence_enforce_manifest": True,
                               "pre_verify_pointers": True,
                               "unresolved_requires_review": True})}

st.set_page_config(page_title="GenomeQC Agent", page_icon="🧬", layout="wide")
st.title("GenomeQC Agent — 细胞器基因组 QC 智能体")
st.caption("规则负责事实，LLM 负责规划与解释，人对高风险处置保有审批权。")


def api() -> httpx.Client:
    if "api" not in st.session_state:
        st.session_state["api"] = httpx.Client(base_url=API, timeout=30)
    return st.session_state["api"]


def get(path: str, **kwargs):
    response = api().get(path, **kwargs)
    response.raise_for_status()
    return response.json()


page = st.sidebar.radio("页面", ["New QC Job", "Agent Execution", "Evidence",
                                "Refinement History", "QC Report",
                                "History / Resume", "Examples",
                                "Strategy Versions", "Human Approvals"])

if page == "New QC Job":
    st.header("1. 提交新的 QC 任务")
    uploaded = st.file_uploader("上传 GenBank 记录（.gbff/.gbk/.gb/.dat/.txt）",
                                type=["gbff", "gbk", "gb", "dat", "txt"])
    col1, col2 = st.columns(2)
    domain = col1.selectbox("领域", DOMAINS, index=2)
    model_label = col2.selectbox("规划模型", list(MODELS), index=0)
    strategy_label = col1.selectbox("策略版本（A/B）", list(STRATEGIES), index=0)
    enable_refinement = col2.checkbox("启用有界证据自纠错（≤2 轮）", value=False)
    st.caption("付费模型仅在服务端完成授权（GENOMEQC_PAID_AUTHORIZED=true）后可用；"
               "未授权提交会被 403 拒绝，绝不发起付费调用。")
    goal = st.text_area("检查目标（可选）", placeholder="例如：核对 CDS 与蛋白质一致性")
    with st.expander("预算上限（只能往更严格方向收紧）"):
        max_tool_calls = st.number_input("max_tool_calls", 1, 200, 200)
        max_model_requests = st.number_input("max_model_requests", 1, 50, 50)
        max_retries = st.number_input("max_retries_per_tool", 0, 3, 1)
    if st.button("提交 QC 任务", type="primary", disabled=uploaded is None):
        planner_kind, model_id = MODELS[model_label]
        prompt_version, guards = STRATEGIES[strategy_label]
        features = {"self_refinement": bool(enable_refinement)}
        upload = api().post("/api/v1/packets",
                            files={"file": (uploaded.name, uploaded.getvalue())},
                            params={"domain": domain})
        if upload.status_code != 201:
            st.error(f"上传被拒绝：{upload.json().get('detail', upload.text)}")
        else:
            packet_id = upload.json()["packet_id"]
            job = api().post("/api/v1/jobs", json={
                "packet_id": packet_id, "domain": domain, "user_goal": goal,
                "max_tool_calls": int(max_tool_calls),
                "max_model_requests": int(max_model_requests),
                "max_retries_per_tool": int(max_retries),
                "planner_kind": planner_kind, "model_id": model_id,
                "prompt_version": prompt_version,
                "policy_guards": guards, "features": features})
            if job.status_code == 202:
                st.success(f"任务已受理：run_id = `{job.json()['run_id']}`")
                st.code(job.json()["run_id"])
            else:
                detail = job.json().get("detail", job.text)
                st.error(f"提交失败（{job.status_code}）：{detail}")

elif page == "Agent Execution":
    st.header("2. 运行轨迹")
    run_id = st.text_input("run_id", key="exec_run", placeholder="粘贴 run_id 后自动加载").strip()
    if st.button("刷新轨迹", key="exec_refresh") or run_id:
        try:
            status = get(f"/api/v1/jobs/{run_id}")
            c1, c2, c3 = st.columns(3)
            c1.metric("状态", status["status"])
            c2.metric("QC 决策", status.get("qc_decision") or "—")
            c3.metric("证据有效性", status.get("evidence_validity") or "—")
            events = get(f"/api/v1/jobs/{run_id}/events")["events"]
            for event in events:
                st.markdown(f"`step {event['step']:>3}` **{event['node']}** — "
                            f"{event['summary']}")
        except (httpx.HTTPStatusError, httpx.RequestError) as error:
            st.error(f"查询失败：{getattr(error, 'response', None) and error.response.status_code or error}")

elif page == "Evidence":
    st.header("2b. 证据与工具 Trace")
    run_id = st.text_input("run_id", key="evidence_run", placeholder="粘贴 run_id 后自动加载").strip()
    if st.button("加载证据", key="evidence_refresh") or run_id:
        try:
            body = get(f"/api/v1/jobs/{run_id}/evidence")
            evidence = body.get("evidence") or {}
            unresolved = evidence.get("unresolved", [])
            st.metric("未解析指针", len(unresolved))
            for entry in evidence.get("pointers", []):
                icon = "✅" if entry.get("resolved") else "❌"
                st.markdown(f"{icon} `{entry['pointer']}` "
                            f"({entry.get('locator_kind', '?')})")
            if unresolved:
                st.warning("未解析指针：\n\n" + "\n".join(f"- `{p}`" for p in unresolved))
            st.subheader("工具调用 Trace")
            st.json(body.get("tool_trace") or {})
        except (httpx.HTTPStatusError, httpx.RequestError) as error:
            st.error(f"查询失败：{getattr(error, 'response', None) and error.response.status_code or error}")

elif page == "Refinement History":
    st.header("2c. 有界证据自纠错历史")
    st.caption("最多 2 轮；每个被接受的纠错都经 verify_evidence_pointer 重新机械验证；"
               "未修复的指针保持 partial，绝不强行升级为 valid。")
    run_id = st.text_input("run_id", key="refine_run", placeholder="粘贴 run_id 后自动加载").strip()
    if st.button("加载自纠错历史", key="refine_refresh") or run_id:
        try:
            body = get(f"/api/v1/jobs/{run_id}/refinement")
            record = body.get("refinement")
            if not record:
                st.info("该任务未触发自纠错（未启用或无未解析指针）。")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("轮数", len(record.get("rounds", [])))
                c2.metric("已纠正", len(record.get("corrected", [])))
                c3.metric("不可纠正", len(record.get("uncorrectable", [])))
                st.markdown(f"未解析指针：{len(record.get('before_unresolved', []))} → "
                            f"**{len(record.get('after_unresolved', []))}**")
                for corr in record.get("corrected", []):
                    st.success(f"`{corr['from']}` → `{corr['to']}`（已重新验证）")
                for bad in record.get("uncorrectable", []):
                    st.warning(f"`{bad['pointer']}` — {bad['reason']}（保留原状）")
                with st.expander("完整轮次 Trace"):
                    st.json(record.get("rounds", []))
        except (httpx.HTTPStatusError, httpx.RequestError) as error:
            st.error(f"查询失败：{getattr(error, 'response', None) and error.response.status_code or error}")

elif page == "QC Report":
    st.header("3. QC 报告")
    run_id = st.text_input("run_id", key="report_run", placeholder="粘贴 run_id 后自动加载").strip()
    if st.button("加载报告", key="report_refresh") or run_id:
        try:
            response = api().get(f"/api/v1/jobs/{run_id}/report", params={"format": "markdown"})
            if response.status_code == 409:
                st.warning("报告尚未生成（任务未完成）")
            elif response.status_code == 200:
                markdown = response.json()["report"]
                st.markdown(markdown)
                st.download_button("下载 Markdown 报告", markdown,
                                   file_name=f"QC_REPORT_{run_id}.md")
            else:
                st.error(f"查询失败：{response.status_code}")
        except httpx.RequestError as error:
            st.error(f"查询失败：{error}")

elif page == "History / Resume":
    st.header("4. 历史 / 恢复")
    jobs = get("/api/v1/jobs")
    st.dataframe([{ "run_id": j["run_id"], "packet": j["packet_id"],
                    "status": j["status"], "decision": j["qc_decision"],
                    "updated": j["updated_at"]} for j in jobs],
                 use_container_width=True)
    target = st.text_input("要恢复/取消的 run_id")
    col1, col2 = st.columns(2)
    if col1.button("恢复（resume）") and target:
        st.info(api().post(f"/api/v1/jobs/{target}/resume").json()["outcome"])
    if col2.button("取消（cancel）") and target:
        st.info(api().post(f"/api/v1/jobs/{target}/cancel").json()["outcome"])

elif page == "Examples":
    st.header("5. 公开演示包")
    st.caption("全部为脱敏公开材料；提交后由后端 Mock/离线执行，无任何付费模型调用。")
    for packet in get("/api/v1/packets"):
        if packet["source"] == "example":
            with st.expander(packet["packet_id"]):
                st.code(packet["input_sha256"][:16] + "…")
                if st.button("用此包建任务", key=packet["packet_id"]):
                    job = api().post("/api/v1/jobs", json={"packet_id": packet["packet_id"]})
                    if job.status_code == 202:
                        st.success(f"run_id = `{job.json()['run_id']}`")
                    else:
                        st.error(job.text)

elif page == "Strategy Versions":
    st.header("6. 策略版本对比（Prompt Registry）")
    try:
        body = get("/api/v1/strategy/versions")
        c1, c2 = st.columns(2)
        c1.metric("Active 策略", body["active_prompt_version"])
        auth = "已授权（付费可启动）" if body["paid_runs_authorized"] else "未授权（付费禁用）"
        c2.metric("付费授权状态", auth)
        st.dataframe([{"版本": v["version"], "SHA256": v["sha256"][:16] + "…",
                       "作者": v["author"], "注册时间": v["registered_at"],
                       "备注": v["notes"]} for v in body["versions"]],
                     use_container_width=True)
        if body.get("prompt_diff_v1_v2"):
            st.subheader("promptv1 → promptv2 diff")
            st.code(body["prompt_diff_v1_v2"], language="diff")
        for cand in body.get("candidates", []):
            with st.expander(f"候选 {cand['candidate_id']}（{cand['status']}）"):
                st.json(cand)
    except httpx.HTTPStatusError as error:
        st.error(f"查询失败：{error.response.status_code}")

elif page == "Human Approvals":
    st.header("7. 人工审批记录")
    st.caption("模拟审批（real=false）永远不能解锁发布；只有 real=true 的真实审批收据可以。")
    try:
        body = get("/api/v1/strategy/approvals")
        rows = [{"候选": a["candidate_id"], "审批人": a["approver"],
                 "决策": a["decision"], "真实审批": "✅" if a["real"] else "⚠️ 模拟",
                 "时间": a["at"]} for a in body["approvals"]]
        st.dataframe(rows, use_container_width=True)
    except httpx.HTTPStatusError as error:
        st.error(f"查询失败：{error.response.status_code}")
