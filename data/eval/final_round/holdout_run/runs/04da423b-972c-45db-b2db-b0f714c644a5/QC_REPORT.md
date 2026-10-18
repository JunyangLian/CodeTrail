# QC Report 04da423b-972c-45db-b2db-b0f714c644a5

- decision: **block**
- packet: `chloro_C1_NC_083131_1_CHLORO-CDS-03` (input sha256 `…`)
- domain: auto_detect_supported ｜ model: `deepseek-ai/DeepSeek-V4.1-Flash`

## Findings

### coordinate_bounds — block
1 feature row(s) fall outside the genome sequence bounds (e.g. CDS_001 spans 1..121549 while the sequence length is 121412)

Evidence:
- `artifacts/features.tsv:4`

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
- model: deepseek-ai/DeepSeek-V4.1-Flash
- planner: deepseek-ai/DeepSeek-V4.1-Flash
- policy_guards: none
- prompt: promptv1
- prompt_sha256: f4f02d462a48182dee544ae1611377d640029063c53c2f8cca1a81c9133e4a1e
