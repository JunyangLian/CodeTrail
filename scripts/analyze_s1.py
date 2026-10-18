"""One-off S1 report analysis (not part of the test suite)."""
import json
from collections import defaultdict

r = json.load(open("data/eval/s1_smoke/s1_report.json", encoding="utf-8"))
print("=== observations ===")
for o in r["observations"]:
    print(f"{o['task'].split('_')[-1]:<12} {o['model_key']}/{o['strategy']} "
          f"{o['status']:<9} dec={str(o['qc_decision']):<6} "
          f"ev={str(o['evidence_validity']):<7} unres={o['unresolved_pointers']} "
          f"rev={str(o['needs_human_review']):<5} tok={o['tokens']:>6} "
          f"req={o['requests']} prompt={o['prompt_version']} "
          f"sha_ok={bool(o['prompt_sha256'])} tools={o['tool_calls']}")

print("\n=== A/B comparison ===")
cells = defaultdict(dict)
for o in r["observations"]:
    cells[(o["task"], o["model_key"])][o["strategy"]] = o
parity = 0
for (task, m), pair in sorted(cells.items()):
    a, b = pair["A"], pair["B"]
    same = a["qc_decision"] == b["qc_decision"]
    parity += same
    print(f"{task.split('CHLORO')[-1]:<14} {m}: A={a['qc_decision']}/"
          f"{a['evidence_validity']} B={b['qc_decision']}/{b['evidence_validity']} "
          f"parity={same} tokA={a['tokens']} tokB={b['tokens']}")

false_pass = [o["task"] for o in r["observations"]
              if o["qc_decision"] == "pass" and "NORMAL" not in o["task"]]
print(f"\ndecision parity: {parity}/8")
print("critical false pass (pass on non-NORMAL):", false_pass or "none")
print("guard denials total:", sum(len(o["guard_denials"]) for o in r["observations"]))
print("prompt identity bound:", all(o["prompt_sha256"] for o in r["observations"]))
print("raw errors:", [o["raw_error"] for o in r["observations"] if o["raw_error"]] or "none")
