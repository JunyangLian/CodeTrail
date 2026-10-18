"""Final-round P3: fetch NEW independent chloroplast source records from NCBI.

Isolation contract: none of the fetched accession_versions may appear in any
V2/V3/V3.1/V3.2 DEV material (blacklist below, derived from the V2 candidate
pool + V3 eval copies). We fetch MORE candidates than needed (14) so that
generator eligibility gates / oracle baseline / domain checks can quarantine
records without dropping below the 8-source target.

Outputs (data/eval/final_round/holdout_sources/):
  raw/<accession_version>.gbff   raw GenBank flat files
  sources_metadata.json          accession, organism, uid, length, sha256,
                                 fetch timestamp (UTC), blacklist check result

Stdlib only. NCBI eutils courtesy: <=3 req/s, retries with backoff.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "final_round" / "holdout_sources"
RAW = OUT / "raw"

# DEV blacklist: every accession_version used in V2/V3 DEV material
# (V2 candidates pool chloro_C1_* == V3 eval copies; verified 2026-10-19).
BLACKLIST = {
    "NC_013991.2", "NC_082429.1", "NC_088757.1", "NC_088761.1",
    "NC_088762.1", "NC_088765.1", "NC_088768.1", "NC_088770.1",
    "NC_088771.1", "NC_140640.1",
}

WANT_CANDIDATES = 14   # extra headroom for quarantine losses
TARGET_LEN = (90_000, 260_000)
BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "AssemblyGenomics-Copilot-holdout"


def _get(url: str, attempts: int = 4) -> bytes:
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"{TOOL}/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as error:  # noqa: BLE001 - bounded backoff then raise
            last = error
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed after {attempts} attempts: {url}: {last}")


def esearch(term: str, retmax: int) -> list[str]:
    q = urllib.parse.urlencode({
        "db": "nuccore", "term": term, "retmax": str(retmax),
        "retmode": "json", "tool": TOOL,
    })
    data = json.loads(_get(f"{BASE}/esearch.fcgi?{q}"))
    ids = data["esearchresult"]["idlist"]
    print(f"esearch: {len(ids)} ids (count={data['esearchresult']['count']})",
          flush=True)
    return ids


def esummary(ids: list[str]) -> dict:
    out = {}
    for i in range(0, len(ids), 150):
        chunk = ids[i:i + 150]
        q = urllib.parse.urlencode({
            "db": "nuccore", "id": ",".join(chunk), "retmode": "json",
            "tool": TOOL,
        })
        data = json.loads(_get(f"{BASE}/esummary.fcgi?{q}"))
        result = data.get("result", {})
        for uid in chunk:
            rec = result.get(uid)
            if rec is None:
                continue
            length = rec.get("slen")
            if not length:
                genome_field = rec.get("genome")
                if isinstance(genome_field, dict):
                    for extra in genome_field.get("statistics", []) or []:
                        if (isinstance(extra, dict)
                                and extra.get("name") == "Sequence Length"):
                            length = int(extra.get("value") or 0)
            out[uid] = {
                "uid": uid,
                "accession_version": rec.get("accessionversion", ""),
                "organism": rec.get("organism", ""),
                "taxid": rec.get("taxid", ""),
                "title": rec.get("title", ""),
                "length": length,
            }
        time.sleep(0.5)
    print(f"esummary: {len(out)} records", flush=True)
    return out


def efetch_gbff(uid: str) -> bytes:
    q = urllib.parse.urlencode({
        "db": "nuccore", "id": uid, "rettype": "gb", "retmode": "text",
        "tool": TOOL,
    })
    return _get(f"{BASE}/efetch.fcgi?{q}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    term = ('chloroplast[Title] AND "complete genome"[Title] '
            'AND srcdb_refseq[PROP] AND Viridiplantae[Organism]')
    ids = esearch(term, retmax=2000)
    summaries = esummary(ids)

    # deterministic candidate selection: NC_ accessions, not blacklisted,
    # length in target range, one per genus (first by sorted accession)
    by_genus: dict[str, list[dict]] = {}
    rejected = []
    for rec in sorted(summaries.values(),
                      key=lambda r: r["accession_version"]):
        acc = rec["accession_version"]
        if not acc.startswith("NC_"):
            continue
        if acc in BLACKLIST:
            rejected.append({"accession": acc, "reason": "DEV blacklist"})
            continue
        length = rec.get("length") or 0
        if not (TARGET_LEN[0] <= length <= TARGET_LEN[1]):
            rejected.append({"accession": acc, "reason": f"length {length}"})
            continue
        organism = rec.get("organism", "")
        genus = organism.split()[0] if organism else "?"
        by_genus.setdefault(genus, []).append(rec)

    chosen: list[dict] = []
    # round-robin over genera sorted by name for diversity, then per-genus
    # order by accession for determinism
    genera = sorted(by_genus)
    while len(chosen) < WANT_CANDIDATES and any(by_genus[g] for g in genera):
        for genus in genera:
            if by_genus[genus] and len(chosen) < WANT_CANDIDATES:
                chosen.append(by_genus[genus].pop(0))
    print(f"chosen {len(chosen)} candidates across "
          f"{len({c['organism'].split()[0] for c in chosen})} genera", flush=True)

    records = []
    for rec in chosen:
        acc = rec["accession_version"]
        dest = RAW / f"{acc}.gbff"
        if dest.exists() and dest.stat().st_size > 1000:
            raw = dest.read_bytes()
            print(f"cached {acc}", flush=True)
        else:
            raw = efetch_gbff(rec["uid"])
            dest.write_bytes(raw)
            time.sleep(0.5)
            print(f"fetched {acc} ({len(raw)} bytes)", flush=True)
        records.append({
            **rec,
            "file": str(dest.relative_to(OUT)),
            "sha256_raw_gbff": hashlib.sha256(raw).hexdigest(),
            "fetched_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "in_dev_blacklist": acc in BLACKLIST,
        })

    (OUT / "sources_metadata.json").write_text(json.dumps({
        "fetched_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "esearch_term": term,
        "blacklist": sorted(BLACKLIST),
        "candidates": records,
        "rejected_sample": rejected[:50],
        "rejected_count": len(rejected),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"WROTE {OUT / 'sources_metadata.json'}", flush=True)
    print(f"OK: {len(records)} candidate sources", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
