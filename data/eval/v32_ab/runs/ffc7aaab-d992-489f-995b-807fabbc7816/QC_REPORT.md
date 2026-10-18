# QC Report ffc7aaab-d992-489f-995b-807fabbc7816

- decision: **block**
- packet: `chloro_C1_NC_088762_1_CHLORO-META-05` (input sha256 `…`)
- domain: auto_detect_supported ｜ model: `deepseek-ai/DeepSeek-V4.1-Flash`

## Findings

### coordinate_bounds — pass
all feature coordinates lie within the sequence

### multipart_structure — pass
multi-part feature layout is consistent

### cross_file_protein_id — pass
CDS protein_id set equals proteins.faa record set

### stats_consistency — block
declared statistics contradict recomputation for: sequence_length

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
