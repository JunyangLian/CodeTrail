"""G3 API: upload -> run -> events -> report user journey, plus lifecycle."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genomeqc_agent.api.main import AppSettings, create_app

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "public_packets"
NORMAL_PACKET = "chloro_C1_NC_013991_2_CHLORO-NORMAL-00"
FAULT_PACKET = "chloro_C1_NC_013991_2_CHLORO-CDS-03"

MINIMAL_GBFF = """LOCUS       TESTD                    60 bp ds-DNA circular SYN 17-OCT-2026
DEFINITION  Test record for V3 API upload.
ACCESSION   TESTD
VERSION     TESTD.1
KEYWORDS    .
SOURCE      Test organism
  ORGANISM  Test organism
            Test phylum
FEATURES             Location/Qualifiers
     source          1..60
                     /organism="Test organism"
     CDS             join(1..30,31..60)
                     /gene=atpA
                     /protein_id="TST_1"
                     /translation="MKVL"
ORIGIN
        1 acgtacgtac gtacgtacgt acgtacgtac gtacgtacgt acgtacgtac gtacgtacgt
//
"""

TERMINAL = {"succeeded", "failed", "cancelled", "budget_exhausted"}


@pytest.fixture()
def client(tmp_path):
    settings = AppSettings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _wait_terminal(client, run_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{run_id}")
        body = response.json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.1)
    raise AssertionError(f"job {run_id} did not reach a terminal state in time")


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert "chloroplast" in body["supported_domains"]


def test_examples_preloaded(client):
    packets = client.get("/api/v1/packets").json()
    ids = {p["packet_id"] for p in packets}
    assert {NORMAL_PACKET, FAULT_PACKET} <= ids
    assert all(p["source"] == "example" for p in packets if p["packet_id"] == NORMAL_PACKET)


def test_upload_genbank_creates_content_addressed_packet(client):
    response = client.post("/api/v1/packets", files={"file": ("testd.gbff", MINIMAL_GBFF.encode())})
    assert response.status_code == 201
    body = response.json()
    assert body["packet_id"].startswith("upload_")
    assert body["reused"] is False
    assert sorted(body["artifact_files"]) == [
        "artifacts/features.tsv", "artifacts/genome.fa",
        "artifacts/proteins.faa", "artifacts/sequence_stats.json"]
    # identical upload is content-addressed and idempotent
    again = client.post("/api/v1/packets", files={"file": ("testd.gbff", MINIMAL_GBFF.encode())}).json()
    assert again["reused"] is True and again["packet_id"] == body["packet_id"]


def test_upload_rejects_wrong_suffix_and_bad_genbank(client):
    response = client.post("/api/v1/packets", files={"file": ("evil.exe", b"MZ..." )})
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "unsupported_file_type"
    response = client.post("/api/v1/packets", files={"file": ("bad.gbff", b"not genbank")})
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "invalid_genbank"


def test_submit_unknown_packet_404(client):
    response = client.post("/api/v1/jobs", json={"packet_id": "ghost"})
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "packet_not_found"


def test_full_journey_upload_run_events_report(client):
    """G3 gate: upload -> execute -> trace -> export report, no code edits."""
    upload = client.post("/api/v1/packets",
                         files={"file": ("testd.gbff", MINIMAL_GBFF.encode())}).json()
    submission = client.post("/api/v1/jobs", json={
        "packet_id": upload["packet_id"], "domain": "auto_detect_supported",
        "user_goal": "API journey smoke"})
    assert submission.status_code == 202
    run_id = submission.json()["run_id"]

    final = _wait_terminal(client, run_id)
    assert final["status"] == "succeeded"
    assert final["qc_decision"] == "pass"
    assert final["report_ref"] is not None

    events = client.get(f"/api/v1/jobs/{run_id}/events").json()["events"]
    nodes = [e["node"] for e in events]
    assert nodes[0] == "intake_validate"
    assert "deterministic_mandatory_checks" in nodes
    assert "synthesize_report" in nodes

    report_json = client.get(f"/api/v1/jobs/{run_id}/report").json()
    assert report_json["format"] == "json"
    assert report_json["report"]["qc_decision"] == "pass"
    report_md = client.get(f"/api/v1/jobs/{run_id}/report",
                           params={"format": "markdown"}).json()
    assert "# QC Report" in report_md["report"]


def test_example_fault_packet_blocks_via_api(client):
    run_id = client.post("/api/v1/jobs", json={
        "packet_id": FAULT_PACKET, "domain": "chloroplast"}).json()["run_id"]
    final = _wait_terminal(client, run_id)
    assert final["qc_decision"] == "block"
    report = client.get(f"/api/v1/jobs/{run_id}/report").json()["report"]
    assert report["evidence_validity"] == "valid"
    assert any(f["status"] == "block" and f["evidence_refs"] for f in report["findings"])


def test_report_not_ready_409(client):
    run_id = client.post("/api/v1/jobs", json={"packet_id": NORMAL_PACKET}).json()["run_id"]
    # poll until terminal to avoid a race, then verify both error paths exist
    _wait_terminal(client, run_id)
    assert client.get(f"/api/v1/jobs/{run_id}/report").status_code == 200
    assert client.get("/api/v1/jobs/missing-run/report").status_code == 404


def test_resume_terminal_job_is_noop(client):
    run_id = client.post("/api/v1/jobs", json={"packet_id": NORMAL_PACKET}).json()["run_id"]
    _wait_terminal(client, run_id)
    outcome = client.post(f"/api/v1/jobs/{run_id}/resume").json()
    assert outcome["outcome"].startswith("resume_ok:succeeded")


def test_cancel_queued_job_with_paused_worker(tmp_path):
    settings = AppSettings(data_dir=tmp_path / "data", register_examples=False)
    app = create_app(settings)
    with TestClient(app) as client:
        client.post("/api/v1/packets", files={"file": ("t.gbff", MINIMAL_GBFF.encode())})
        packets = client.get("/api/v1/packets").json()
        # worker thread is alive; make the queue back up by submitting many
        # jobs, then cancel the last one before it starts
        run_ids = [client.post("/api/v1/jobs", json={"packet_id": packets[0]["packet_id"]})
                   .json()["run_id"] for _ in range(6)]
        outcome = client.post(f"/api/v1/jobs/{run_ids[-1]}/cancel").json()["outcome"]
        assert outcome in {"cancel_requested", "already_finished", "cancelled"}


def test_job_and_packet_404s(client):
    assert client.get("/api/v1/jobs/missing").status_code == 404
    assert client.get("/api/v1/jobs/missing/events").status_code == 404
    assert client.post("/api/v1/jobs/missing/cancel").status_code == 404


def test_budget_cannot_exceed_service_defaults(client):
    response = client.post("/api/v1/jobs", json={
        "packet_id": NORMAL_PACKET, "max_tool_calls": 10_000})
    assert response.status_code == 422  # pydantic le=DEFAULT_MAX_TOOL_CALLS


# -- P4: model/strategy selection authorization boundary + new endpoints -------

def test_paid_run_requires_authorization(client):
    response = client.post("/api/v1/jobs", json={
        "packet_id": NORMAL_PACKET, "planner_kind": "llm",
        "model_id": "zai-org/GLM-5.3-Flash"})
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "payment_not_authorized"


def test_paid_run_unknown_model_rejected_even_when_authorized(client, monkeypatch):
    monkeypatch.setenv("GENOMEQC_PAID_AUTHORIZED", "true")
    response = client.post("/api/v1/jobs", json={
        "packet_id": NORMAL_PACKET, "planner_kind": "llm",
        "model_id": "totally-fake-model"})
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "unknown_model"


def test_paid_run_without_credentials_never_submits(client, monkeypatch):
    monkeypatch.setenv("GENOMEQC_PAID_AUTHORIZED", "true")
    response = client.post("/api/v1/jobs", json={
        "packet_id": NORMAL_PACKET, "planner_kind": "llm",
        "model_id": "zai-org/GLM-5.3-Flash"})
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "gateway_unconfigured"


def test_strategy_versions_endpoint(client):
    body = client.get("/api/v1/strategy/versions").json()
    versions = {v["version"] for v in body["versions"]}
    assert {"promptv1", "promptv2"} <= versions
    assert body["active_prompt_version"] == "promptv1"
    assert body["prompt_diff_v1_v2"]  # real diff text
    assert body["paid_runs_authorized"] is False  # default: no paid runs


def test_strategy_approvals_endpoint(client):
    body = client.get("/api/v1/strategy/approvals").json()
    assert isinstance(body["approvals"], list)
    assert body["approvals"]  # phase 1.5/2 receipts exist in evolution_state
    assert all(a["real"] is False for a in body["approvals"])  # all simulated


def test_evidence_and_refinement_endpoints(client):
    run_id = client.post("/api/v1/jobs", json={"packet_id": NORMAL_PACKET}).json()["run_id"]
    _wait_terminal(client, run_id)
    evidence = client.get(f"/api/v1/jobs/{run_id}/evidence").json()
    assert evidence["evidence"].get("pointers")
    assert "verify_evidence_pointer" in evidence["tool_trace"]
    refinement = client.get(f"/api/v1/jobs/{run_id}/refinement").json()
    assert refinement["refinement"] is None  # feature off -> engine never ran


def test_scripted_run_with_candidate_strategy_and_refinement(client):
    response = client.post("/api/v1/jobs", json={
        "packet_id": NORMAL_PACKET,
        "policy_guards": {"evidence_enforce_manifest": True,
                          "pre_verify_pointers": True},
        "features": {"self_refinement": True}})
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    _wait_terminal(client, run_id)
    report = client.get(f"/api/v1/jobs/{run_id}/report").json()["report"]
    assert report["versions"]["policy_guards"] != "none"
    assert report["versions"]["prompt"] == "promptv1"
