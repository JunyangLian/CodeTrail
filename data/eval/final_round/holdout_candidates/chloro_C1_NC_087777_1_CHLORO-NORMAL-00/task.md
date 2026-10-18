# Organelle genome delivery QC review

You are reviewing a small organelle-genome delivery before it enters a
curated public collection. The delivery contains exactly these files:

- artifacts/genome.fa: the organelle genome sequence (single FASTA record).
- artifacts/features.tsv: the annotation table. Coordinates are 1-based
  inclusive on the genome.fa sequence. Multi-part locations appear as
  consecutive rows that share feature_id and carry part_index/part_count;
  the complete original location text is kept in location_raw.
- artifacts/proteins.faa: protein sequences for CDS features, keyed by
  protein_id.
- artifacts/sequence_stats.json: summary statistics computed by the
  submitter; every field is recomputable from the files above.

Your job: verify the internal consistency of this delivery using only these
files, decide whether it can be accepted, and report any defect you can
substantiate. Do not speculate beyond the files; if the evidence you can see
is not enough to decide, say so in the output instead of guessing.

Respond with exactly one JSON object, no extra text, with these fields:

{"verdict": "pass" | "warn" | "block" | "rollback",
 "observed_defect": "<empty string when nothing is wrong>",
 "root_cause": "none" | "cross_file_id_mismatch" | "coordinate_out_of_bounds" | "stats_conflict",
 "evidence": [{"pointer": "<file>:<line number or json key>", "note": "<what this shows>"}],
 "action": "<the concrete next step for the submitter>"}

Evidence pointers use the relative file paths exactly as listed above, for
example "artifacts/features.tsv:14" or "artifacts/sequence_stats.json:sequence_length".

Decision semantics:
- pass: internally consistent; accept the delivery.
- warn: minor issues that do not block acceptance; explain in action.
- block: reject until the defect is fixed.
- rollback: revert to a previously accepted version.
