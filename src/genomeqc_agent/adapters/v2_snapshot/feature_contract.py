"""The features.tsv contract for organelle_v2 evidence packets.

Coordinate system: 1-based inclusive on the sequence as delivered in
genome.fa (the explicit declaration travels in sequence_stats.json).
Multi-part locations (join) are one row per part sharing feature_id with
part_index/part_count; the full original location text stays in location_raw
and is never flattened. `strand` is PER PART: trans-spliced mixed-strand
constructs (e.g. plant chloroplast rps12) therefore carry rows with different
strand values under one feature_id, and the contract never silently
re-orients them. Cross-origin parts (start > end, circular wrap) are
represented as-is and flagged by the oracle, never normalized away.

The file itself is a plain header + TSV body with no comment lines, so the
existing V1 PublicFiles.table() reader can consume it unchanged.
"""
from __future__ import annotations

import csv
import io
import re

COORDINATE_SYSTEM = '1-based-inclusive'

FEATURE_COLUMNS = ('feature_id', 'feature_type', 'part_index', 'part_count',
                   'gene', 'locus_tag', 'product', 'protein_id', 'start',
                   'end', 'strand', 'codon_start', 'transl_table',
                   'partial_flags', 'location_raw')

# Types this contract actively interprets (translation, identity joins).
# Any other INSDC feature type is carried as-is: the TSV contract only
# requires a structural identifier, and the oracle reports unknown types
# informationally instead of guessing their semantics.
KNOWN_TYPES = {'source', 'gene', 'mRNA', 'exon', 'CDS', 'tRNA', 'rRNA', 'ncRNA',
               'D_loop', 'rep_origin', 'stem_loop', 'misc_feature',
               'misc_RNA', 'misc_difference', 'repeat_region'}

FEATURE_TYPE_RE = re.compile(r'^[^\s\t]+$')

PARTIAL_TOKENS = {'partial_start', 'partial_end'}


def partial_flags_text(location):
    tokens = []
    if location.parts and location.parts[0].get('partial_start'):
        tokens.append('partial_start')
    if location.parts and location.parts[-1].get('partial_end'):
        tokens.append('partial_end')
    return '+'.join(tokens) if tokens else 'none'


def feature_id_for(key, index):
    return f'{key}_{index:03d}'


def rows_from_record(record):
    """Deterministic feature rows from a parsed GenBankRecord.

    Rows follow the record's feature order; a feature_id groups the rows of
    one feature (including every part of a compound location).
    """
    counters = {}
    rows = []
    for feature in record.features:
        key = feature['key']
        counters[key] = counters.get(key, 0) + 1
        feature_id = feature_id_for(key, counters[key])
        location = feature['location']
        qualifiers = feature['qualifiers']
        gene = (qualifiers.get('gene') or [''])[0]
        locus_tag = (qualifiers.get('locus_tag') or [''])[0]
        product = (qualifiers.get('product') or [''])[0]
        protein_id = (qualifiers.get('protein_id') or [''])[0]
        codon_start = (qualifiers.get('codon_start') or [''])[0]
        transl_table = (qualifiers.get('transl_table') or [''])[0]
        parts = location.parts
        for part_index, part in enumerate(parts, start=1):
            rows.append({
                'feature_id': feature_id, 'feature_type': key,
                'part_index': str(part_index), 'part_count': str(len(parts)),
                'gene': gene, 'locus_tag': locus_tag, 'product': product,
                'protein_id': protein_id, 'start': str(part['start']),
                'end': str(part['end']), 'strand': part.get('strand', location.strand),
                'codon_start': codon_start, 'transl_table': transl_table,
                'partial_flags': partial_flags_text(location),
                'location_raw': location.raw,
            })
    return rows


def render_features_tsv(rows):
    """Canonical TSV bytes; raises when a value would break the row width."""
    lines = ['\t'.join(FEATURE_COLUMNS)]
    for row in rows:
        values = []
        for column in FEATURE_COLUMNS:
            value = str(row[column])
            if '\t' in value or '\n' in value or '\r' in value:
                raise ValueError(f'{column} value contains a tab or newline: {value!r}')
            values.append(value)
        lines.append('\t'.join(values))
    return ('\n'.join(lines) + '\n').encode('utf-8')


def parse_features_tsv(text):
    """Strict reader returning the list of row dicts; raises on any deviation."""
    reader = csv.DictReader(io.StringIO(text), delimiter='\t')
    if reader.fieldnames != list(FEATURE_COLUMNS):
        raise ValueError(f'features.tsv header differs from the contract: {reader.fieldnames}')
    rows = []
    for number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f'features.tsv line {number} has the wrong column count')
        if not FEATURE_TYPE_RE.match(row['feature_type'] or ''):
            raise ValueError(f'features.tsv line {number} has malformed feature_type {row["feature_type"]!r}')
        if row['strand'] not in {'+', '-'}:
            raise ValueError(f'features.tsv line {number} has invalid strand {row["strand"]!r}')
        for column in ('part_index', 'part_count', 'start', 'end'):
            if not row[column].isdigit():
                raise ValueError(f'features.tsv line {number} has non-integer {column}')
        if row['codon_start'] and row['codon_start'] not in {'1', '2', '3'}:
            raise ValueError(f'features.tsv line {number} has invalid codon_start')
        flags = row['partial_flags'].split('+') if row['partial_flags'] != 'none' else []
        if any(token not in PARTIAL_TOKENS for token in flags):
            raise ValueError(f'features.tsv line {number} has invalid partial_flags {row["partial_flags"]!r}')
        rows.append(row)
    if not rows:
        raise ValueError('features.tsv has no rows')
    return rows


def group_parts(rows):
    """Group rows by feature_id preserving file order; validates part layout."""
    grouped = {}
    order = []
    for row in rows:
        feature_id = row['feature_id']
        if feature_id not in grouped:
            grouped[feature_id] = []
            order.append(feature_id)
        grouped[feature_id].append(row)
    for feature_id in order:
        parts = grouped[feature_id]
        count = int(parts[0]['part_count'])
        if len(parts) != count:
            raise ValueError(f'feature {feature_id} has {len(parts)} rows but part_count {count}')
        if [int(row['part_index']) for row in parts] != list(range(1, count + 1)):
            raise ValueError(f'feature {feature_id} part_index sequence is broken')
        if len({row['feature_type'] for row in parts}) != 1:
            raise ValueError(f'feature {feature_id} mixes feature_type values')
        if len({row['location_raw'] for row in parts}) != 1:
            raise ValueError(f'feature {feature_id} rows disagree on location_raw')
    return [(feature_id, grouped[feature_id]) for feature_id in order]
