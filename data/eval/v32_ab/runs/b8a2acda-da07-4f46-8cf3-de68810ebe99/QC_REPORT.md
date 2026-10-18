# QC Report b8a2acda-da07-4f46-8cf3-de68810ebe99

- decision: **block**
- packet: `chloro_C1_NC_088771_1_CHLORO-ID-01` (input sha256 `…`)
- domain: auto_detect_supported ｜ model: `deepseek-ai/DeepSeek-V4.1-Flash`

## Findings

### coordinate_bounds — pass
all feature coordinates lie within the sequence

### multipart_structure — pass
multi-part feature layout is consistent

### cross_file_protein_id — block
CDS/protein ID set mismatch: 1 CDS protein_id(s) without a proteins.faa record and 1 FASTA record(s) without a CDS (e.g. YP_011108125.2, YP_011108125.1)

Evidence:
- `artifacts/features.tsv:5`
- `artifacts/features.tsv:6`
- `artifacts/features.tsv:7`
- `artifacts/proteins.faa:1`

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
