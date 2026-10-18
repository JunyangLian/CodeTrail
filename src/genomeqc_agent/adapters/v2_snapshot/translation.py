"""NCBI translation tables and deterministic CDS translation.

Only the tables the V2 contract needs: table 2 (vertebrate mitochondrion,
the animal-mito domain) and table 1 (standard, for cross-checks). The
translation follows GenBank /translation conventions:
- codons translated per the table, '---' for gaps is unsupported here;
- the initiator codon is reported as 'M' when it is a recognized start codon;
- a terminal Stop is dropped before comparison with /translation values.
Incomplete terminal codons (1-2 bases) are reported, never silently trimmed.
"""
from __future__ import annotations

_BASES = 'TCAG'
_AAS = 'FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG'


def _codon_table():
    table = {}
    index = 0
    for first in _BASES:
        for second in _BASES:
            for third in _BASES:
                table[first + second + third] = _AAS[index]
                index += 1
    return table


_TABLE1 = _codon_table()
# Vertebrate mitochondrial code (NCBI table 2) differs from table 1 at:
# UGA=Stop->W, AUA=Ile->M, AGA/AGG=Arg->Stop.
_TABLE2 = dict(_TABLE1)
_TABLE2.update({'TGA': 'W', 'ATA': 'M', 'AGA': '*', 'AGG': '*'})

# Start codons recognized by table 2 (translation reports M for these).
_TABLE2_STARTS = {'ATT', 'ATC', 'ATA', 'ATG', 'GTG'}
# Table 11 (bacterial/plastid): codon assignments equal table 1; only the
# recognized initiators differ.
_TABLE11_STARTS = {'TTG', 'CTG', 'ATT', 'ATC', 'ATA', 'ATG', 'GTG'}

TABLES = {1: _TABLE1, 2: _TABLE2, 11: _TABLE1}
TABLE_STARTS = {1: {'TTG', 'CTG', 'ATT', 'ATC', 'ATA', 'ATG', 'GTG'},
                2: _TABLE2_STARTS,
                11: _TABLE11_STARTS}


def translate(sequence, table_id=2):
    """Translate a nucleotide string; returns (protein, notes).

    Notes report unknown codons and the trailing incomplete codon, if any.
    A terminal '*' stop is removed from the returned protein.
    """
    try:
        table = TABLES[table_id]
    except KeyError:
        raise ValueError(f'unsupported transl_table {table_id!r}') from None
    protein, notes = [], []
    trimmed = sequence.upper().replace(' ', '').replace('\n', '')
    if len(trimmed) % 3:
        notes.append(f'trailing_incomplete_codon:{len(trimmed) % 3}')
        trimmed = trimmed[:len(trimmed) - len(trimmed) % 3]
    for offset in range(0, len(trimmed), 3):
        codon = trimmed[offset:offset + 3]
        residue = table.get(codon)
        if residue is None:
            residue = 'X'
            notes.append(f'unknown_codon:{codon}:{offset + 1}')
        protein.append(residue)
    translated = ''.join(protein)
    if translated.endswith('*'):
        translated = translated[:-1]
    return translated, notes


def translate_cds(sequence, codon_start=1, table_id=2, complete=True):
    """Translate a CDS the way a GenBank /translation value is built.

    codon_start is 1/2/3 as in the GenBank qualifier. For a complete CDS the
    initiator residue is ALWAYS reported as 'M' - the ribosome starts with
    methionine regardless of the initiator codon, and GenBank /translation
    values follow that convention (this also covers documented unusual
    initiators such as ACG in some plastid psbL genes). Returns
    (protein, notes); TABLE_STARTS remains available for start-codon
    curation notes but does not gate the substitution.
    """
    if codon_start not in (1, 2, 3):
        raise ValueError(f'codon_start must be 1, 2 or 3, got {codon_start!r}')
    trimmed = sequence.upper().replace(' ', '').replace('\n', '')
    trimmed = trimmed[codon_start - 1:]
    protein, notes = translate(trimmed, table_id)
    if complete and protein:
        protein = 'M' + protein[1:]
    return protein, notes
