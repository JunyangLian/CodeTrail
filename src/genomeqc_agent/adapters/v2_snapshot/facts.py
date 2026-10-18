"""Public-byte computations for organelle QC; no oracle/private dependencies."""
from __future__ import annotations

import json
from collections import Counter, defaultdict

FACTS_VERSION = 'organelle-public-facts/v1'


def public_facts(files):
    """Return measurements and locators, never a verdict or expected answer."""
    genome = files.fasta('artifacts/genome.fa')
    proteins = files.fasta('artifacts/proteins.faa')
    columns, rows = files.table('artifacts/features.tsv')
    required = {'feature_type', 'feature_id', 'part_index', 'part_count',
                'protein_id', 'start', 'end'}
    if not required <= set(columns) or len(genome) != 1:
        raise ValueError('unsupported organelle public table/genome structure')
    sequence = next(iter(genome.values())).upper()
    grouped = defaultdict(list)
    counts = Counter()
    distinct = defaultdict(set)
    coordinate_violations, multipart_issues, cds_ids = [], [], set()
    cds_locations = defaultdict(list)
    for line, row in enumerate(rows, 2):
        counts[row['feature_type']] += 1
        distinct[row['feature_type']].add(row['feature_id'])
        grouped[row['feature_id']].append((line, row))
        start, end = int(row['start']), int(row['end'])
        if not 1 <= start <= end <= len(sequence):
            coordinate_violations.append({'feature_id': row['feature_id'],
                'start': start, 'end': end, 'actual_sequence_length': len(sequence),
                'pointer': f'artifacts/features.tsv:{line}'})
        if row['feature_type'] == 'CDS' and row['protein_id']:
            cds_ids.add(row['protein_id'])
            cds_locations[row['protein_id']].append(line)
    for feature_id, parts in grouped.items():
        declared = {int(row['part_count']) for _, row in parts}
        indices = sorted(int(row['part_index']) for _, row in parts)
        if len(declared) != 1 or indices != list(range(1, len(parts)+1)) or declared != {len(parts)}:
            multipart_issues.append({'feature_id': feature_id,
                'part_indices': indices, 'declared_part_counts': sorted(declared),
                'pointer': f'artifacts/features.tsv:{parts[0][0]}'})
    stats = json.loads(files.text('artifacts/sequence_stats.json'))
    acgt = sum(sequence.count(base) for base in 'ACGT')
    recomputed = {'coordinate_system': '1-based-inclusive', 'genome_record_count': len(genome),
        'sequence_length': len(sequence), 'protein_count': len(proteins),
        'gc_percent': round(100*(sequence.count('G')+sequence.count('C'))/acgt, 2) if acgt else None,
        'feature_counts': dict(sorted(counts.items()))}
    stat_pairs = {key: {'claimed': stats.get(key), 'computed': val,
                        'equal': stats.get(key) == val,
                        'pointer': f'artifacts/sequence_stats.json:{key}'}
                  for key, val in recomputed.items()}
    fasta_lines = {line[1:].split()[0]: number
                   for number, line in enumerate(files.text('artifacts/proteins.faa').splitlines(), 1)
                   if line.startswith('>')}
    return {'facts_version': FACTS_VERSION,
        'scope': 'Only the public artifact bytes; no translation or external biological validation.',
        'count_convention': 'feature_counts computed as rows per feature_type, including each join part',
        'declared_count_definition': stats.get('definitions', {}).get('feature_counts'),
        'feature_rows_by_type': dict(sorted(counts.items())),
        'distinct_feature_ids_by_type': {key: len(val) for key, val in sorted(distinct.items())},
        'multipart_feature_count': sum(len(parts)>1 for parts in grouped.values()),
        'multipart_structure_issues': multipart_issues,
        'cds_protein_ids_without_fasta_record': [
            {'protein_id': pid, 'pointers': [f'artifacts/features.tsv:{line}' for line in cds_locations[pid]]}
            for pid in sorted(cds_ids-set(proteins))],
        'fasta_protein_ids_without_cds': [
            {'protein_id': pid, 'pointer': f'artifacts/proteins.faa:{fasta_lines[pid]}'}
            for pid in sorted(set(proteins)-cds_ids)],
        'coordinate_violations': coordinate_violations,
        'statistics': stat_pairs,
        'measurement_sources': {'feature_counts': 'artifacts/features.tsv:1',
            'sequence_length': 'computed from all genome.fa sequence bases, not its header',
            'gc_percent': '100*(G+C)/(A+C+G+T) computed from all genome.fa bases'}}


def facts_tool(files):
    from inspect_ai.tool import tool, ToolError

    @tool
    def organelle_summary():
        async def execute():
            """Compute counts, multipart groups, CDS/protein ID differences, coordinate bounds,
            and claimed versus recomputed statistics from all public organelle artifacts.
            Returns measurements with evidence pointers, not a QC decision.
            """
            try:
                return json.dumps(public_facts(files), ensure_ascii=False, sort_keys=True)
            except (ValueError, KeyError) as error:
                raise ToolError(str(error)) from None
        return execute
    return organelle_summary()
