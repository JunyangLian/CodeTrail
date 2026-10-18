"""M1-M4 driver: build failure pool, propose strategy, run the evolution
state machine (offline simulation), build the E2 regression pool, and
execute deterministic regressions. No model API is called."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genomeqc_agent.evolution.controller import EvolutionController, EvolutionError
from genomeqc_agent.evolution.failure_intel import collect_all
from genomeqc_agent.evolution.regression_pool import RegressionPool

DATA = Path(__file__).resolve().parents[1] / "data"
EVAL_ROOT = Path(__file__).resolve().parents[1]
V2_REPO = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2")

MODELS = {
    "g4_glm_chloro40": {"provider": "siliconflow",
                        "model_id": "zai-org/GLM-5.3-Flash"},
    "g4_ds_chloro40": {"provider": "siliconflow",
                       "model_id": "deepseek-ai/DeepSeek-V4.1-Flash"},
}


def main() -> int:
    out = DATA / "evolution"
    summary: dict = {"simulation": "offline_simulation"}

    # M1: failure pool from real traces
    failure_report = collect_all([DATA / "eval" / name for name in MODELS],
                                 MODELS, DATA / "failure_pool")
    pool_lines = (DATA / "failure_pool" / "failure_cases.v1.jsonl").read_text(
        encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in pool_lines if line.strip()]
    summary["m1_failure_pool"] = failure_report["reports"]
    summary["m1_entries_total"] = len(entries)
    print(f"M1 failure pool: {len(entries)} entries "
          f"(failures={failure_report['reports'][0]['counts']['failures']}, "
          f"high_risk={failure_report['reports'][0]['counts']['high_risk_successes']})")

    # M2: proposer against the ACTIVE production config (post-repair reality)
    from genomeqc_agent.evolution.controller import StrategyProposer
    proposer = StrategyProposer(active_config={"max_tokens": 8192,
                                               "gateway_timeout_s": 120,
                                               "verify_pointer_cap": 50})
    proposal = proposer.propose(entries)
    summary["m2_proposal"] = proposal
    print(f"M2 proposer: proposed={proposal['proposed']} "
          f"candidates={len(proposal['candidates'])} "
          f"refusals={len(proposal['refusals'])}")

    # M3: controller state machine over a mock candidate (offline simulation)
    controller = EvolutionController(out / "receipts",
                                     active_config={"max_tokens": 4096,
                                                    "gateway_timeout_s": 60,
                                                    "verify_pointer_cap": 50})
    cand = controller.propose("CAND-4096-to-8192", {
        "config_delta": {"max_tokens": 8192},
        "root_cause": "mock: planning call truncated at 4096 (real GLM case family)",
        "rollback_ref": json.dumps({"max_tokens": 4096}, sort_keys=True),
        "simulation": "offline_simulation",
    })
    base_metrics = {"judgment_pass_rate": 0.85, "pointer_resolution_rate": 0.99,
                    "critical_false_pass": 0, "denominator": 40}
    cand_metrics = {"judgment_pass_rate": 0.90, "pointer_resolution_rate": 1.0,
                    "critical_false_pass": 0, "denominator": 40}
    controller.transition("CAND-4096-to-8192", "validated",
                          metrics=cand_metrics, baseline=base_metrics)
    controller.transition("CAND-4096-to-8192", "shadow_tested",
                          shadow_result={"replay": "historical-trace-replay",
                                         "simulation": "offline_simulation"})
    controller.transition("CAND-4096-to-8192", "accepted")
    try:
        controller.transition("CAND-4096-to-8192", "approved", human_approval=False)
        approved = False
    except EvolutionError:
        approved = True  # human gate blocked publishing as designed
    controller.transition("CAND-4096-to-8192", "approved", human_approval=True)
    controller.transition("CAND-4096-to-8192", "published")
    cand["offline_simulation"] = True
    summary["m3_state_machine"] = {
        "final_status": cand["status"],
        "human_gate_blocked_publish_without_approval": approved,
        "published_config": cand.get("published_config"),
        "receipts_dir": str(out / "receipts" / "CAND-4096-to-8192"),
    }
    print(f"M3 state machine: {cand['status']} "
          f"(human gate blocked: {approved})")

    # auto-reject path: regressing candidate
    controller.propose("CAND-REGRESS", {"config_delta": {"max_tokens": 4096},
                                        "rollback_ref": "{}",
                                        "simulation": "offline_simulation"})
    controller.transition("CAND-REGRESS", "validated",
                          metrics={"judgment_pass_rate": 0.60,
                                   "pointer_resolution_rate": 0.95,
                                   "critical_false_pass": 0, "denominator": 40},
                          baseline=base_metrics)
    regress = controller.state["CAND-REGRESS"]
    summary["m3_auto_reject"] = {"status": regress["status"],
                                 "reason": regress.get("rejection_reason")}
    print(f"M3 auto-reject: {regress['status']} ({regress.get('rejection_reason')})")

    # M4: E2 regression memory
    pool = RegressionPool(DATA / "regression_pool" / "v3_pool.v1.jsonl")
    imported = pool.import_v2_pool(V2_REPO / "reports" / "organelle_v2" / "e2"
                                   / "E2_REGRESSION_POOL.v2.json")
    h1 = pool.add_v3_case(
        family="planner.hallucinated_pointer",
        error_mechanism="planner cited pointers failing mechanical resolution",
        task_scope="chloro_CDS",
        evidence={"case": "chloro_C1_NC_088770_1_CHLORO-CDS-03",
                  "unresolved": ["artifacts/genome.fa:all_sequences",
                                 "artifacts/proteins.fa:all_records"],
                  "interception": "evidence_validity downgraded to partial"},
        regression_test="tests/unit/test_evolution.py::test_hallucinated_pointer_downgrades_validity",
        status="formal_regression")
    h2 = pool.add_v3_case(
        family="planner.output_truncation",
        error_mechanism="reasoning+content exceed max_tokens",
        task_scope="chloro_CDS",
        evidence={"case": "chloro_C1_NC_088768_1_CHLORO-ID-01",
                  "error": "finish_reason=length at 4096"},
        regression_test="tests/unit/test_evolution.py::test_truncation_is_attributed",
        status="formal_regression")
    h3 = pool.add_v3_case(
        family="contract.null_tool_name",
        error_mechanism="model emits tool_name=null on synthesize",
        task_scope="any",
        evidence={"normalisation": "validator maps null to empty string"},
        regression_test="tests/unit/test_evolution.py::test_null_tool_name_normalised",
        status="formal_regression")
    h4 = pool.add_v3_case(
        family="infra.provider_503",
        error_mechanism="provider capacity 503, no auto retry",
        task_scope="chloro_META",
        evidence={"cases": 3, "terminal_status": "failed"},
        status="candidate")  # no deterministic unit test yet -> candidate
    pool.save()
    regression = pool.run_formal_regressions(tests_root=EVAL_ROOT)
    summary["m4_e2_pool"] = {"imported_from_v2": imported,
                             "v3_added": [h for h in (h1, h2, h3, h4) if h],
                             "entries_total": len(pool.entries),
                             "regression": regression}
    (out / "e2_regression_report.v1.json").write_text(
        json.dumps(summary["m4_e2_pool"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"M4 e2 pool: {len(pool.entries)} entries "
          f"(v2 imported={imported}); regressions all_passed="
          f"{regression['all_passed']}")

    (out / "phase1_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"summary: {out / 'phase1_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
