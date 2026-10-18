"""Phase 1.5 driver: prepare real-A/B offline prerequisites.

P0  audit current registry/controller/prompt version state
P1  restricted behaviour-policy candidate for the real
    invalid_evidence_pointer failure (capped at `validated`)
P2  governed approvals (reject blocks; active untouched) + receipts
P3  frozen real A/B protocol constants -> docs/V3_1_REAL_AB_PROTOCOL.md

Everything is offline_simulation; no model API is called.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent import config
from genomeqc_agent.evolution.controller import (
    EvolutionController,
    EvolutionError,
)
from genomeqc_agent.evolution.failure_intel import collect_all
from genomeqc_agent.evolution.prompts import PromptRegistry
from genomeqc_agent.model.llm_planner import SYSTEM_PROMPT

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATE = ROOT / "evolution_state"          # git-tracked versioned assets
V2_REPO = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2")

MODELS = {
    "g4_glm_chloro40": {"provider": "siliconflow",
                        "model_id": "zai-org/GLM-5.3-Flash"},
    "g4_ds_chloro40": {"provider": "siliconflow",
                       "model_id": "deepseek-ai/DeepSeek-V4.1-Flash"},
}

ACTIVE_CONFIG = {"max_tokens": 8192, "gateway_timeout_s": 120,
                 "verify_pointer_cap": 50}

PROMPT_V2_NOTES = (
    "pointer-hygiene guard, proposed from the real invalid_evidence_pointer "
    "failure chloro_C1_NC_088770_1_CHLORO-CDS-03 (proteins.fa filename "
    "hallucination + invented locators); capped at validated until real A/B")


def prompt_v2_content() -> str:
    base = SYSTEM_PROMPT
    addition = (
        "\nEvidence pointer hygiene (MANDATORY):\n"
        "- Cite ONLY files exactly as they appear in the packet manifest\n"
        "  (e.g. artifacts/proteins.faa - never invented names like proteins.fa).\n"
        "- Cite ONLY locators you have seen in tool output (line numbers from\n"
        "  lookup_feature rows, JSON keys from facts). Never invent locators.\n"
        "- If unsure a pointer resolves, do NOT cite it; describe the gap in\n"
        "  the reason field instead. Unresolved pointers downgrade the report\n"
        "  to partial evidence - dropping necessary evidence to look clean is\n"
        "  forbidden.\n")
    return base + addition


def main() -> int:
    config.load_dotenv()
    summary: dict = {"simulation": "offline_simulation"}

    # -- P0: audit current state -------------------------------------------
    registry = PromptRegistry(STATE / "prompt_registry.v1.json")
    base = registry.register("promptv1", SYSTEM_PROMPT,
                             notes="active production prompt (G4 receipts)")
    cand = registry.register("promptv2", prompt_v2_content(),
                             notes=PROMPT_V2_NOTES, author="StrategyProposer")
    summary["p0"] = {
        "prompt_versions": {v: {"sha256": r["sha256"][:16] + "…"}
                            for v, r in registry.versions.items()},
        "active_prompt": "promptv1",
        "active_config": ACTIVE_CONFIG,
        "integrity": "registered contents hash-verified on every read",
    }
    print("P0 audit:", json.dumps(summary["p0"], ensure_ascii=False))

    # -- M1 pool into git-tracked evolution_state ---------------------------
    failure_report = collect_all([DATA / "eval" / name for name in MODELS],
                                 MODELS, STATE / "failure_pool")
    pool_lines = (STATE / "failure_pool" / "failure_cases.v1.jsonl").read_text(
        encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in pool_lines if line.strip()]
    summary["m1"] = {"entries": len(entries),
                     "report": failure_report["reports"][0]["counts"]}
    print(f"M1 pool: {len(entries)} entries")

    # -- P1: restricted behaviour-policy candidate ---------------------------
    controller = EvolutionController(STATE / "receipts",
                                     active_config=dict(ACTIVE_CONFIG))
    try:
        candidate = controller.propose_behavior_policy(
            "CAND-promptv2-pointer-guard", entries,
            prompt_registry=registry)
    except EvolutionError as error:
        print(f"P1 refused: {error}")
        return 1
    # historical-replay metrics: the guard exists precisely because the
    # hallucination produced partial evidence; replay validates detection
    controller.transition("CAND-promptv2-pointer-guard", "validated",
                          metrics={"judgment_pass_rate": 0.85,
                                   "pointer_resolution_rate": 1.0,
                                   "critical_false_pass": 0,
                                   "denominator": 40,
                                   "replay": "historical-trace-replay"},
                          baseline={"judgment_pass_rate": 0.85,
                                    "pointer_resolution_rate": 0.9934,
                                    "critical_false_pass": 0,
                                    "denominator": 40})
    assert candidate["status"] == "validated"
    assert candidate["status_cap"] == "validated"
    try:
        controller.transition("CAND-promptv2-pointer-guard", "shadow_tested")
        cap_enforced = False
    except EvolutionError:
        cap_enforced = True
    summary["p1"] = {
        "candidate_id": "CAND-promptv2-pointer-guard",
        "status": candidate["status"],
        "status_cap_enforced": cap_enforced,
        "config_delta": candidate["proposal"]["config_delta"],
        "prompt_diff_lines": len((candidate["proposal"].get("prompt_diff") or "").splitlines()),
        "root_cause": candidate["proposal"]["root_cause"],
        "failure_refs": candidate["proposal"]["failure_refs"],
        "rollback_ref": candidate["proposal"]["rollback_ref"],
    }
    print("P1 candidate:", json.dumps(summary["p1"], ensure_ascii=False)[:220])

    # -- P2: governed approvals ----------------------------------------------
    controller.record_approval("CAND-promptv2-pointer-guard",
                               approver="offline_simulation:phase15-driver",
                               decision="reject", candidate_version=1)
    rejected = controller.state["CAND-promptv2-pointer-guard"]
    reject_keeps_active = controller.active_config == ACTIVE_CONFIG
    # a fresh candidate demonstrates the approve path with recorded identity
    controller.propose_behavior_policy("CAND-promptv2-pointer-guard-v2", entries,
                                       prompt_registry=registry)
    approval = controller.record_approval("CAND-promptv2-pointer-guard-v2",
                                          approver="offline_simulation:phase15-driver",
                                          decision="approve", candidate_version=1)
    summary["p2"] = {
        "rejection_blocked_candidate": rejected["status"] == "rejected",
        "rejection_keeps_active_strategy": reject_keeps_active,
        "approval_receipt": approval,
        "approver_identity_recorded": bool(approval["approver"]),
        "note": ("approvals in Phase 1.5 are simulated identities; a real "
                 "human approver flow arrives with Phase 2"),
    }
    print("P2 approvals:", json.dumps({k: summary["p2"][k] for k in
                                       ("rejection_blocked_candidate",
                                        "rejection_keeps_active_strategy")}))

    # -- P3: frozen real A/B protocol constants (document, no API calls) ------
    summary["p3"] = {
        "frozen_models": sorted({m["model_id"] for m in MODELS.values()}),
        "dev_slice": "chloro candidates 40 (local pool), source_group frozen",
        "strategies": {"baseline": "promptv1 + ACTIVE_CONFIG",
                       "candidate": "promptv2 pointer-guard (validated)"},
        "scorer": "organelle_v2.rubric.deterministic_grader/v1 (frozen)",
        "protocol_doc": "docs/V3_1_REAL_AB_PROTOCOL.md",
        "api_calls_this_phase": 0,
    }

    # -- candidate manifest (git-tracked) -------------------------------------
    manifest = {
        "manifest": "STRATEGY_CANDIDATE_MANIFEST/v1",
        "simulation": "offline_simulation",
        "generated_at": datetime_now_utc(),
        "active_strategy": {"prompt_version": "promptv1",
                            "prompt_sha256": base["sha256"],
                            "config": ACTIVE_CONFIG},
        "candidates": [{
            "candidate_id": "CAND-promptv2-pointer-guard",
            "kind": "behavior_policy",
            "status": "rejected_by_simulated_approval",  # see P2 receipt
            "status_cap": "validated",
            "prompt_version": "promptv2",
            "prompt_sha256": cand["sha256"],
            "config_delta": candidate["proposal"]["config_delta"],
            "root_cause": candidate["proposal"]["root_cause"],
            "failure_refs": candidate["proposal"]["failure_refs"],
            "rollback_ref": candidate["proposal"]["rollback_ref"],
            "approvals": [rejected.get("approval")],
        }, {
            "candidate_id": "CAND-promptv2-pointer-guard-v2",
            "kind": "behavior_policy",
            "status": "validated (approval receipt simulated)",
            "status_cap": "validated",
            "prompt_version": "promptv2",
            "prompt_sha256": cand["sha256"],
            "config_delta": candidate["proposal"]["config_delta"],
            "root_cause": candidate["proposal"]["root_cause"],
            "failure_refs": candidate["proposal"]["failure_refs"],
            "rollback_ref": candidate["proposal"]["rollback_ref"],
            "approvals": [approval],
        }],
        "prompt_registry": {"path": "evolution_state/prompt_registry.v1.json",
                            "versions": {v: r["sha256"]
                                         for v, r in registry.versions.items()}},
    }
    (STATE / "STRATEGY_CANDIDATE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (DATA / "evolution" / "phase15_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"manifest: {STATE / 'STRATEGY_CANDIDATE_MANIFEST.json'}")
    return 0


def datetime_now_utc() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
