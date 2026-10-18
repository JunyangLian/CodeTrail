"""GenomeQC Agent Streamlit UI (plan §7.2).

Talks to the FastAPI backend over HTTP only - no direct tool/model access,
no keys in the UI. Start the API first:

    uvicorn genomeqc_agent.api.main:create_app --factory --host 127.0.0.1 --port 8000
    streamlit run ui/streamlit_app.py
"""
from __future__ import annotations

import httpx
import streamlit as st

API = "http://127.0.0.1:8000"
DOMAINS = ["vertebrate_mitochondrion", "chloroplast", "auto_detect_supported"]
TERMINAL = {"succeeded", "needs_human_review", "insufficient_evidence",
            "failed", "cancelled", "budget_exhausted"}

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


page = st.sidebar.radio("页面", ["New QC Job", "Agent Execution", "QC Report",
                                "History / Resume", "Examples"])

if page == "New QC Job":
    st.header("1. 提交新的 QC 任务")
    uploaded = st.file_uploader("上传 GenBank 记录（.gbff/.gbk/.gb/.dat/.txt）",
                                type=["gbff", "gbk", "gb", "dat", "txt"])
    domain = st.selectbox("领域", DOMAINS, index=2)
    goal = st.text_area("检查目标（可选）", placeholder="例如：核对 CDS 与蛋白质一致性")
    with st.expander("预算上限（只能往更严格方向收紧）"):
        max_tool_calls = st.number_input("max_tool_calls", 1, 200, 200)
        max_model_requests = st.number_input("max_model_requests", 1, 50, 50)
        max_retries = st.number_input("max_retries_per_tool", 0, 3, 1)
    if st.button("提交 QC 任务", type="primary", disabled=uploaded is None):
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
                "max_retries_per_tool": int(max_retries)})
            if job.status_code == 202:
                st.success(f"任务已受理：run_id = `{job.json()['run_id']}`")
                st.code(job.json()["run_id"])
            else:
                st.error(job.text)

elif page == "Agent Execution":
    st.header("2. 运行轨迹")
    run_id = st.text_input("run_id")
    if st.button("刷新轨迹") and run_id:
        try:
            status = get(f"/api/v1/jobs/{run_id}")
            st.metric("状态", status["status"])
            if status.get("qc_decision"):
                st.metric("QC 决策", status["qc_decision"])
            events = get(f"/api/v1/jobs/{run_id}/events")["events"]
            for event in events:
                st.markdown(f"`step {event['step']:>3}` **{event['node']}** — "
                            f"{event['summary']}")
        except httpx.HTTPStatusError as error:
            st.error(f"查询失败：{error.response.status_code}")

elif page == "QC Report":
    st.header("3. QC 报告")
    run_id = st.text_input("run_id", key="report_run")
    if st.button("加载报告") and run_id:
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
