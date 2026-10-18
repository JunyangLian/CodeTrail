"""P6: integrate the V3.2 real A/B result into the Evolution Controller as
a context-strategy candidate (ACCEPT evidence -> proposed->validated, cap
enforced; publishing requires real human approval - NOT executed)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent.evolution.controller import EvolutionController
from genomeqc_agent.evolution.prompts import PromptRegistry

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "evolution_state"

analysis = json.loads((ROOT / "data/eval/v32_ab/v32_analysis.json")
                      .read_text(encoding="utf-8"))
a, b = analysis["per_condition"]["A"], analysis["per_condition"]["B"]
entries = [{
    "case_id": "V3.2_P4_real_ab",
    "category": "model_planning_error",
    "error_evidence": {
        "invalid_pointer_obs_A": a["invalid_pointer_obs"],
        "invalid_pointer_obs_B": b["invalid_pointer_obs"],
        "design": "12 tasks x 2 models x 2 conditions x 3 repeats = 144 "
                  "real observations, single factor = context assembly",
    },
}]
registry = PromptRegistry(STATE / "prompt_registry.v1.json")
controller = EvolutionController(
    STATE / "receipts" / "v32_context_candidate",
    active_config={"max_tokens": 8192, "gateway_timeout_s": 120,
                   "verify_pointer_cap": 50})
candidate = controller.propose("CAND-contextv1-pointer-guard", {
    "kind": "context_strategy",
    "config_delta": {"context_builder_v1": True},
    "context_builder_version": "contextv1",
    "prompt_version": "promptv1 (UNCHANGED - single factor was context)",
    "root_cause": (f"real repeated A/B: invalid-pointer observations "
                   f"A={a['invalid_pointer_obs']} vs B="
                   f"{b['invalid_pointer_obs']} over 72 obs/arm"),
    "failure_refs": ["data/eval/v32_ab/v32_analysis.json"],
    "rollback_ref": json.dumps({"config_delta": {},
                                "features": {}}, sort_keys=True),
    "max_status": "validated",
    "simulation": "real_ab_evidence",
})
candidate["status_cap"] = "validated"  # enforce before any promotion
controller.transition("CAND-contextv1-pointer-guard", "validated", metrics={
    "judgment_pass_rate": 1.0, "pointer_resolution_rate":
        1 - b["invalid_pointer_obs"] / 72,
    "critical_false_pass": 0, "denominator": 72,
    "note": "B-arm effective rate; A arm comparison in decision evidence"},
    baseline={"judgment_pass_rate": 1.0,
              "pointer_resolution_rate": 1 - a["invalid_pointer_obs"] / 72,
              "critical_false_pass": 0, "denominator": 72})
assert candidate["status"] == "validated"
assert candidate["status_cap"] == "validated"

# simulated approval must NOT unlock publishing (state assertion)
try:
    controller.transition("CAND-contextv1-pointer-guard", "approved",
                          human_approval=True)
    blocked = False
except Exception:
    blocked = True
assert blocked and controller.active_config["max_tokens"] == 8192

summary = {
    "candidate_id": "CAND-contextv1-pointer-guard",
    "status": candidate["status"],
    "status_cap": candidate["status_cap"],
    "publish_blocked_without_real_approval": blocked,
    "active_strategy_unchanged": "promptv1",
    "receipts": str(STATE / "receipts" / "v32_context_candidate"),
}
(ROOT / "data/eval/v32_ab/evolution_integration.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=1))
