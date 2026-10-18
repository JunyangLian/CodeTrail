"""Final-round P3: isolation checks + frozen selection + dataset manifest.

Read-only over DEV material; writes ONLY under data/eval/final_round/.

Isolation checks (must ALL pass before freezing):
  1. no holdout accession appears in the DEV blacklist (10 sources)
  2. no holdout genome SEQUENCE equals any DEV genome sequence
     (V2 candidates + V2 imported pools, sequence content not bytes)
  3. holdout source_group ids do not collide with DEV group ids
  4. no holdout accession appears in the E2 regression pool or V3 failure pool
  5. genus diversity >= 8 distinct genera across the selected 8 sources

Frozen selection (deterministic, declared BEFORE any model run):
  - sources: first 8 by accession among the 14 converted candidates
  - tasks per source: CHLORO-NORMAL-00 + CHLORO-CDS-03
    + (ID-01 for even index, META-05 for odd index)  => 8/8/4/4 = 24 tasks
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FINAL = ROOT / "data" / "eval" / "final_round"
SOURCES = FINAL / "holdout_sources"
CAND = FINAL / "holdout_candidates"
V2 = Path(r"D:\1_yanjiusheng\AssemblyGenomics-Copilot_V2")
DEV_CANDIDATES = V2 / "data" / "organelle_v2" / "candidates"
DEV_IMPORTED = V2 / "data" / "imported"
E2_POOL = V2 / "reports" / "organelle_v2" / "e2" / "E2_REGRESSION_POOL.v2.json"
FAILURE_POOL = ROOT / "data" / "failure_pool"

BLACKLIST = {
    "NC_013991.2", "NC_082429.1", "NC_088757.1", "NC_088761.1",
    "NC_088762.1", "NC_088765.1", "NC_088768.1", "NC_088770.1",
    "NC_088771.1", "NC_140640.1",
}
SELECT_N = 8
TASKS_PER_SOURCE = ("CHLORO-NORMAL-00", "CHLORO-CDS-03")  # + rotating third


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fasta_sequences(path: Path) -> dict[str, str]:
    seqs, name, chunks = {}, None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            if name:
                seqs[name] = "".join(chunks).upper()
            name, chunks = line[1:].split()[0], []
        else:
            chunks.append(line.strip())
    if name:
        seqs[name] = "".join(chunks).upper()
    return seqs


def main() -> int:
    meta = json.loads((SOURCES / "sources_metadata.json").read_text(encoding="utf-8"))
    candidates = meta["candidates"]

    # ---- DEV sequence universe ------------------------------------------
    dev_sequences: set[str] = set()
    dev_accessions: set[str] = set()
    dev_groups: set[str] = set()
    dev_fa_files = sorted(DEV_CANDIDATES.glob("*/artifacts/genome.fa")) + \
        sorted(DEV_IMPORTED.glob("*/public/*/artifacts/genome.fa"))
    for fa in dev_fa_files:
        dev_sequences.update(fasta_sequences(fa).values())
        m = re.search(r"(NC_\d+_\d+)", fa.parent.parent.name)
        if m:
            dev_accessions.add(m.group(1).replace("_", "."))
            dev_groups.add(fa.parent.parent.name)

    # DEV pools text (E2 + failure pool) for substring checks
    pool_text = ""
    if E2_POOL.exists():
        pool_text += E2_POOL.read_text(encoding="utf-8")
    for fp in FAILURE_POOL.glob("**/*.json"):
        pool_text += fp.read_text(encoding="utf-8")

    isolation = {"checks": [], "per_source": []}

    def check(name: str, ok: bool, detail: str):
        isolation["checks"].append({"check": name, "ok": ok, "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")

    # ---- per-source isolation --------------------------------------------
    chosen = sorted(candidates, key=lambda c: c["accession_version"]
                    )[:SELECT_N]
    assert len(chosen) == SELECT_N, "not enough candidates"
    per_source = []
    for c in chosen:
        acc = c["accession_version"]
        packet_dir = SOURCES / "packets" / f"source_{acc}"
        fa = packet_dir / "public" / "artifacts" / "genome.fa"
        seqs = fasta_sequences(fa)
        assert len(seqs) == 1
        seq = next(iter(seqs.values()))
        organism = c["organism"]
        genus = organism.split()[0]
        entry = {
            "accession_version": acc,
            "organism": organism,
            "genus": genus,
            "taxid": c["taxid"],
            "ncbi_uid": c["uid"],
            "gbff_sha256": c["sha256_raw_gbff"],
            "fetched_at_utc": c["fetched_at_utc"],
            "genome_sequence_sha256": sha(seq.encode()),
            "sequence_length": len(seq),
            "in_dev_blacklist": acc in BLACKLIST,
            "sequence_in_dev_pool": seq in dev_sequences,
            "accession_in_dev_pool": acc in dev_accessions,
            "in_e2_or_failure_pool_text": acc.replace(".", "_") in pool_text
                                          or acc in pool_text,
            "dev_group_collision": f"source_{acc}" in dev_groups,
        }
        per_source.append(entry)

    ok_blacklist = not any(e["in_dev_blacklist"] for e in per_source)
    ok_seq = not any(e["sequence_in_dev_pool"] for e in per_source)
    ok_acc = not any(e["accession_in_dev_pool"] for e in per_source)
    ok_pool = not any(e["in_e2_or_failure_pool_text"] for e in per_source)
    ok_group = not any(e["dev_group_collision"] for e in per_source)
    ok_div = len({e["genus"] for e in per_source}) == SELECT_N
    check("accession_not_in_dev_blacklist", ok_blacklist,
          f"{SELECT_N} sources vs {len(BLACKLIST)} DEV accessions")
    check("sequence_not_in_dev_pool", ok_seq,
          f"compared against {len(dev_fa_files)} DEV genome.fa files "
          f"({len(dev_sequences)} distinct sequences)")
    check("accession_not_in_dev_pool", ok_acc, "V2 candidates + imported")
    check("accession_not_in_e2_or_failure_pool", ok_pool,
          "E2_REGRESSION_POOL.v2.json + data/failure_pool JSON text scan")
    check("source_group_no_collision", ok_group, "source_<acc> dir names")
    check("genus_diversity", ok_div,
          f"{len({e['genus'] for e in per_source})} distinct genera: "
          f"{sorted({e['genus'] for e in per_source})}")

    all_ok = ok_blacklist and ok_seq and ok_acc and ok_pool and ok_group and ok_div
    if not all_ok:
        print("ISOLATION FAILED - dataset must NOT be frozen")
        return 1

    # ---- frozen task selection --------------------------------------------
    tasks = []
    for i, e in enumerate(per_source):
        acc = e["accession_version"]
        third = "CHLORO-ID-01" if i % 2 == 0 else "CHLORO-META-05"
        for tpl in (*TASKS_PER_SOURCE, third):
            task_id = f"chloro_C1_{acc.replace('.', '_')}_{tpl}"
            case_dir = CAND / task_id
            if not case_dir.is_dir():
                raise SystemExit(f"missing generated case: {task_id}")
            public = {}
            for rel in ("task.md", "artifacts/genome.fa",
                        "artifacts/features.tsv", "artifacts/proteins.faa",
                        "artifacts/sequence_stats.json"):
                public[rel] = sha((case_dir / rel).read_bytes())
            priv = {}
            for rel in ("private/expected.json", "private/mutation.json",
                        "private/rubric.json"):
                priv[rel] = sha((case_dir / rel).read_bytes())
            expected = json.loads((case_dir / "private" / "expected.json")
                                  .read_text(encoding="utf-8"))
            tasks.append({
                "task_id": task_id,
                "source_accession_version": acc,
                "organism": e["organism"],
                "template_id": tpl,
                "task_type": expected.get("task_type"),
                "root_cause": expected.get("root_cause"),
                "public_sha256": public,
                "private_sha256": priv,
                "case_dir": str(case_dir.relative_to(ROOT)),
            })

    counts: dict[str, int] = {}
    for t in tasks:
        counts[t["template_id"]] = counts.get(t["template_id"], 0) + 1

    manifest = {
        "manifest": "V3_2_HOLDOUT_DATASET_MANIFEST/v1",
        "frozen_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "baseline_commit": "c30a4f3",
        "generator": "organelle_v2.factory.generator/v1 (V2 v2.1.1, read-only)",
        "converter": "organelle_v2.normalize.genbank_to_packet/v1",
        "domain": "chloroplast",
        "contract_version": "chloro_qc_v1",
        "provenance": {
            "esearch_term": meta["esearch_term"],
            "candidates_considered": len(candidates),
            "sources_selected": SELECT_N,
            "sources_metadata_file": "holdout_sources/sources_metadata.json",
        },
        "isolation": isolation,
        "per_source": per_source,
        "tasks": tasks,
        "task_counts_by_template": counts,
        "total_tasks": len(tasks),
        "planned_observations": len(tasks) * 2 * 2 * 1,
    }
    out = FINAL / "holdout_dataset"
    out.mkdir(parents=True, exist_ok=True)
    (out / "DATASET_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"\nFROZEN {len(tasks)} tasks "
          f"({counts}) -> {out / 'DATASET_MANIFEST.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
