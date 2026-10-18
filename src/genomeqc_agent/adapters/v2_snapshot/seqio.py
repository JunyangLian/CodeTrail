"""Small deterministic FASTA readers/writers for the organelle packet contract."""
from __future__ import annotations


def parse_fasta(text):
    """Parse FASTA text into ordered {id: (description, sequence)}.

    Mirrors the V1 PublicFiles.fasta() rules: first whitespace token is the id,
    ids must be unique, sequence lines contain no whitespace.
    """
    records, name, description, chunks = {}, None, '', []
    for line in text.splitlines():
        if line.startswith('>'):
            if name is not None:
                records[name] = (description, ''.join(chunks))
            tokens = line[1:].split()
            if not tokens:
                raise ValueError('FASTA header is empty')
            name = tokens[0]
            if name in records:
                raise ValueError(f'duplicate FASTA id: {name}')
            description = line[1:].strip()
            chunks = []
        elif line.strip():
            if name is None:
                raise ValueError('sequence data before first FASTA header')
            if any(character.isspace() for character in line.strip()):
                raise ValueError(f'FASTA sequence line contains whitespace: {name}')
            chunks.append(line.strip())
    if name is not None:
        records[name] = (description, ''.join(chunks))
    if not records:
        raise ValueError('FASTA has no records')
    return records


def render_fasta(records, width=60):
    """Render [(header, sequence)] as FASTA bytes with fixed wrapping."""
    lines = []
    for header, sequence in records:
        if '\t' in header or '\n' in header:
            raise ValueError(f'invalid FASTA header: {header!r}')
        lines.append('>' + header)
        sequence = sequence.upper()
        for offset in range(0, len(sequence), width):
            lines.append(sequence[offset:offset + width])
        if not sequence:
            raise ValueError(f'empty sequence for record: {header!r}')
    return ('\n'.join(lines) + '\n').encode('utf-8')
