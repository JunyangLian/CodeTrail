# QC Report 79e23fe8-cd6f-4c16-82e8-4b0b8eef8534

- decision: **block**
- packet: `chloro_C1_NC_013991_2_CHLORO-CDS-03` (input sha256 `…`)
- domain: auto_detect_supported ｜ model: `zai-org/GLM-5.3-Flash`

## Findings

### coordinate_bounds — block
1 feature row(s) fall outside the genome sequence bounds (e.g. CDS_003 spans 148..158599 while the sequence length is 158462)

Evidence:
- `artifacts/features.tsv:13`

### multipart_structure — pass
multi-part feature layout is consistent

### cross_file_protein_id — pass
CDS protein_id set equals proteins.faa record set

### stats_consistency — pass
every declared statistic matches recomputation

Evidence:
- `artifacts/sequence_stats.json:coordinate_system`
- `artifacts/sequence_stats.json:feature_counts`
- `artifacts/sequence_stats.json:gc_percent`
- `artifacts/sequence_stats.json:genome_record_count`
- `artifacts/sequence_stats.json:protein_count`
- `artifacts/sequence_stats.json:sequence_length`

## Not checked
- translation correctness against the genetic code (tool computes presence, not semantics)
- annotation semantics versus external references (no external database access)
- cross-packet provenance (single packet is self-checked only)
- biological plausibility of feature content

## Versions
- agent: v3-g1 (0.1.0)
- graph: qc-stategraph-v1
- model: zai-org/GLM-5.3-Flash
- planner: zai-org/GLM-5.3-Flash
- policy_guards: none
- prompt: promptv1
- prompt_sha256: f4f02d462a48182dee544ae1611377d640029063c53c2f8cca1a81c9133e4a1e
