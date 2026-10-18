"""One-off: merge the 4-way shard ledgers into the main ledger."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "V3_2_REAL_AB_LEDGER.jsonl"

main_lines = [l for l in MAIN.read_text(encoding="utf-8").splitlines()
              if l.strip()]
seen = set()
for l in main_lines:
    rec = json.loads(l)
    seen.add((rec.get("case_id"), rec.get("model_key"), rec.get("condition"),
              rec.get("repeat"), rec.get("kind")))
added = 0
for i in range(4):
    src = ROOT / f"V3_2_REAL_AB_LEDGER.shard{i}.jsonl"
    if not src.exists():
        continue
    for l in src.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        rec = json.loads(l)
        key = (rec.get("case_id"), rec.get("model_key"),
               rec.get("condition"), rec.get("repeat"), rec.get("kind"))
        if key not in seen:
            main_lines.append(l)
            seen.add(key)
            added += 1
MAIN.write_text("\n".join(main_lines) + "\n", encoding="utf-8")
kinds = {}
for l in main_lines:
    rec = json.loads(l)
    kinds[rec.get("kind")] = kinds.get(rec.get("kind"), 0) + 1
print(f"merged: main now {len(main_lines)} rows (+{added}); kinds={kinds}")
