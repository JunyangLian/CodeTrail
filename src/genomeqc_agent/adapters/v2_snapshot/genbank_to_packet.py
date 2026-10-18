"""Convert one parsed GenBank record into a normalized organelle evidence packet.

Packet layout (the source-level unit; task packets are derived from it):
  source_<accession_version>/
    provenance.json          private metadata (never model-visible)
    canonical.gbff           raw record bytes, cached for traceability
    public/artifacts/
      genome.fa              single-record FASTA, header '>accession.version organism'
      features.tsv           feature_contract rows
      proteins.faa           CDS /translation values keyed by protein_id
      sequence_stats.json    ONLY fields recomputable from the public files

Metadata that cannot be recomputed from public bytes (organism, taxid,
topology, fetch info) lives in the private provenance, so a mutated public
packet is always checkable against itself.
"""
from __future__ import annotations

from pathlib import Path

# V3 snapshot patch (the ONLY change vs bench/organelle_v2/normalize/genbank_to_packet.py):
# three import lines are redirected to byte-identical snapshots so this module
# does not require the V2 repo on sys.path. See SNAPSHOT_MANIFEST.json.
from genomeqc_agent.adapters.v2_snapshot.v2_common import canonical, digest, read_json, write_json
from genomeqc_agent.adapters.v2_snapshot import feature_contract, seqio
from genomeqc_agent.adapters.v2_snapshot.genbank import GenBankRecord

CONVERTER_ID = 'organelle_v2.normalize.genbank_to_packet/v1'

STAT_DEFINITIONS = {
    'gc_percent': '100*(G+C)/(A+C+G+T) over uppercase genome.fa bases; non-ACGT excluded from the denominator',
    # Forward-only fix (2026-10-16, plan (c) of the prompt-v2 A/B report §4.3):
    # the declared convention must match what compute_sequence_stats actually
    # counts -- one row per join part. The previous text ("distinct feature_id
    # ... join parts counted once") contradicted the values in every case with
    # multi-part features (40/40 C1 packets) and, once the prompt-v2 appendix
    # told the model to normalize per this very text, it "verified" a phantom
    # counting defect. Frozen datasets keep the old text byte-identical; only
    # future batches pick this up.
    'feature_counts': 'feature rows per feature_type in features.tsv; a multi-part feature (part_count>1) contributes one row per join part',
    'protein_count': 'number of records in proteins.faa',
    'sequence_length': 'length of the single genome.fa record',
}


def compute_sequence_stats(sequence, feature_rows, protein_records):
    sequence = sequence.upper()
    acgt = sum(sequence.count(base) for base in 'ACGT')
    gc = sequence.count('G') + sequence.count('C')
    counts = {}
    for row in feature_rows:
        counts[row['feature_type']] = counts.get(row['feature_type'], 0) + 1
    stats = {
        'coordinate_system': feature_contract.COORDINATE_SYSTEM,
        'genome_record_count': 1,
        'sequence_length': len(sequence),
        'gc_percent': round(100.0 * gc / acgt, 2) if acgt else None,
        'feature_counts': dict(sorted(counts.items())),
        'protein_count': len(protein_records),
        'definitions': STAT_DEFINITIONS,
    }
    return stats


def build_packet(record: GenBankRecord, raw_bytes: bytes, provenance_extra=None):
    """Build the packet artifact map; raises on records the contract cannot carry."""
    if record.version:
        accession_version = record.version
    else:
        raise ValueError('record lacks a VERSION accession.version')
    if not record.sequence:
        raise ValueError('record has an empty sequence')

    feature_rows = feature_contract.rows_from_record(record)
    proteins, seen_protein_ids = [], set()
    for feature in record.features_of('CDS'):
        protein_id = (feature['qualifiers'].get('protein_id') or [''])[0]
        translation = (feature['qualifiers'].get('translation') or [''])[0]
        if not protein_id or not translation:
            raise ValueError(f'CDS {protein_id or "(no id)"} lacks protein_id or translation; '
                             'quarantine this record instead of flattening')
        if protein_id in seen_protein_ids:
            raise ValueError(f'duplicate CDS protein_id: {protein_id}')
        seen_protein_ids.add(protein_id)
        gene = (feature['qualifiers'].get('gene') or [''])[0]
        header = f'{protein_id} gene={gene}' if gene else protein_id
        proteins.append((header, translation.upper()))
    if not proteins:
        raise ValueError('record has no usable CDS translations')

    genome_fa = seqio.render_fasta([(f'{accession_version} {record.organism}'.strip(),
                                     record.sequence)])
    features_tsv = feature_contract.render_features_tsv(feature_rows)
    proteins_faa = seqio.render_fasta(proteins)
    stats = compute_sequence_stats(record.sequence, feature_rows,
                                   seqio.parse_fasta(proteins_faa.decode('utf-8')))
    provenance = {
        'accession_version': accession_version,
        'organism': record.organism,
        'taxonomy': record.taxonomy,
        'topology': record.topology,
        'molecule': record.molecule,
        'division': record.division,
        'genbank_date': record.date,
        'definition': record.definition,
        'organelle': (record.features[0]['qualifiers'].get('organelle') or [''])[0]
        if record.features and record.features[0]['key'] == 'source' else '',
        'raw_gbff_sha256': digest(raw_bytes),
        'converter': CONVERTER_ID,
        'notes': [],
    }
    if provenance_extra:
        provenance.update(provenance_extra)
    return {
        'accession_version': accession_version,
        'genome_fa': genome_fa,
        'features_tsv': features_tsv,
        'proteins_faa': proteins_faa,
        'sequence_stats': stats,
        'provenance': provenance,
    }


PUBLIC_ARTIFACTS = ('genome.fa', 'features.tsv', 'proteins.faa', 'sequence_stats.json')


def write_packet(directory: Path, packet, raw_gbff: bytes = None):
    directory = Path(directory)
    (directory / 'public' / 'artifacts').mkdir(parents=True, exist_ok=True)
    if raw_gbff is not None:
        (directory / 'canonical.gbff').write_bytes(raw_gbff)
    (directory / 'public' / 'artifacts' / 'genome.fa').write_bytes(packet['genome_fa'])
    (directory / 'public' / 'artifacts' / 'features.tsv').write_bytes(packet['features_tsv'])
    (directory / 'public' / 'artifacts' / 'proteins.faa').write_bytes(packet['proteins_faa'])
    write_json(directory / 'public' / 'artifacts' / 'sequence_stats.json', packet['sequence_stats'])
    write_json(directory / 'provenance.json', packet['provenance'])
    return packet['provenance']


def read_artifacts(artifacts_dir: Path):
    """Read an artifacts directory (the model-visible view) into a packet map."""
    artifacts_dir = Path(artifacts_dir)
    missing = [name for name in PUBLIC_ARTIFACTS if not (artifacts_dir / name).is_file()]
    if missing:
        raise ValueError(f'artifact directory {artifacts_dir} is missing: {missing}')
    genome_text = (artifacts_dir / 'genome.fa').read_text(encoding='utf-8')
    genome = seqio.parse_fasta(genome_text)
    if len(genome) != 1:
        raise ValueError(f'genome.fa must hold exactly one record, got {len(genome)}')
    name, (description, sequence) = next(iter(genome.items()))
    features_tsv = (artifacts_dir / 'features.tsv').read_text(encoding='utf-8')
    feature_rows = feature_contract.parse_features_tsv(features_tsv)
    proteins_text = (artifacts_dir / 'proteins.faa').read_text(encoding='utf-8')
    protein_records = {pid: seq for pid, (_, seq) in seqio.parse_fasta(proteins_text).items()}
    stats = read_json(artifacts_dir / 'sequence_stats.json')
    return {
        'artifacts_dir': artifacts_dir,
        'sequence_id': name,
        'sequence_description': description,
        'sequence': sequence,
        'features_tsv': features_tsv,
        'feature_rows': feature_rows,
        'proteins_faa': proteins_text,
        'protein_records': protein_records,
        'sequence_stats': stats,
    }


def read_packet(directory: Path):
    """Read a normalized source packet (public/artifacts layout + provenance)."""
    directory = Path(directory)
    packet = read_artifacts(directory / 'public' / 'artifacts')
    packet['directory'] = directory
    provenance_path = directory / 'provenance.json'
    packet['provenance'] = read_json(provenance_path) if provenance_path.is_file() else None
    return packet


def read_case(case_dir: Path):
    """Read a generated task case (task.md + artifacts layout, no provenance)."""
    case_dir = Path(case_dir)
    packet = read_artifacts(case_dir / 'artifacts')
    packet['directory'] = case_dir
    packet['provenance'] = None
    packet['task_md'] = (case_dir / 'task.md').read_text(encoding='utf-8')
    return packet


def convert_file(raw_path: Path, out_root: Path, provenance_extra=None):
    """Convert one .gbff file into a normalized source packet directory."""
    raw_path = Path(raw_path)
    raw_bytes = raw_path.read_bytes()
    from genomeqc_agent.adapters.v2_snapshot.genbank import parse_genbank
    record = parse_genbank(raw_bytes.decode('utf-8'))
    packet = build_packet(record, raw_bytes, provenance_extra)
    directory = Path(out_root) / f"source_{packet['accession_version']}"
    write_packet(directory, packet, raw_gbff=raw_bytes)
    return directory, packet


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='directory of .gbff files')
    parser.add_argument('--out', required=True, type=Path, help='normalized packet output root')
    args = parser.parse_args(argv)
    converted, rejected = [], []
    for raw_path in sorted(args.input.glob('*.gbff')):
        try:
            directory, packet = convert_file(raw_path, args.out)
            converted.append({'file': raw_path.name,
                              'accession_version': packet['accession_version'],
                              'organism': packet['provenance']['organism'],
                              'directory': str(directory)})
            print(f"converted {raw_path.name} -> {directory.name}")
        except ValueError as error:
            rejected.append({'file': raw_path.name, 'reason': str(error)})
            print(f"REJECTED {raw_path.name}: {error}")
    print(f'converted={len(converted)} rejected={len(rejected)}')
    return 0 if converted else 1


if __name__ == '__main__':
    raise SystemExit(main())
