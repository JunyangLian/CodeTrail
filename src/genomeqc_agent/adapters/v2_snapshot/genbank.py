"""Minimal deterministic GenBank flat-file reader for animal mitogenomes.

Scope (deliberately narrow, mirroring the V2 contract):
- LOCUS / DEFINITION / ACCESSION / VERSION / SOURCE / ORGANISM header fields.
- FEATURES entries with span, join(...), complement(...) and complement(join(...))
  locations, including `<`/`>` partial markers.
- ORIGIN sequence section.
No network, no Biopython, no mutation of input text: the same bytes always
parse to the same structure. Anything outside this subset raises ValueError
so that callers can quarantine the record instead of silently flattening it.

Whitespace reconstruction rule for wrapped qualifier values: /translation is
joined with '' (amino acids must not contain spaces); every other qualifier
joins continuation lines with ' ' (cosmetic only; no join key or translation
depends on it).
"""
from __future__ import annotations

import re

_FEATURE_RE = re.compile(r'^ {5}(\S+)\s+(.+?)\s*$')
_CONTINUATION_WIDTH = 21


class Location:
    """Parsed GenBank location: ordered parts, each with its own strand.

    `strand` is the aggregate ('+'/'-' when all parts agree, 'mixed' for
    trans-spliced constructs such as plant chloroplast rps12). Coordinates
    always stay 1-based inclusive in sequence numbering, whatever the strand.
    """

    __slots__ = ('kind', 'strand', 'parts', 'raw')

    def __init__(self, kind, strand, parts, raw):
        self.kind = kind            # 'span' | 'join'
        self.strand = strand        # '+' | '-' | 'mixed'
        self.parts = parts          # dicts: start, end, partial_start, partial_end, strand
        self.raw = raw

    def as_dict(self):
        return {'kind': self.kind, 'strand': self.strand, 'parts': self.parts,
                'raw': self.raw}

    def is_simple_span(self):
        return self.kind == 'span' and len(self.parts) == 1

    def cross_origin(self):
        return any(part['start'] > part['end'] for part in self.parts)


def _split_top_level(text):
    pieces, depth, current = [], 0, []
    for char in text:
        if char == ',' and depth == 0:
            pieces.append(''.join(current))
            current = []
            continue
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                raise ValueError(f'unbalanced parentheses in location: {text!r}')
        current.append(char)
    if depth != 0:
        raise ValueError(f'unbalanced parentheses in location: {text!r}')
    pieces.append(''.join(current))
    return [piece.strip() for piece in pieces if piece.strip()]


def _parse_span(text, raw):
    match = re.fullmatch(r'<?(\d+)(?:\.\.>?\d+)?', text)
    if not match:
        raise ValueError(f'unsupported location element: {text!r} in {raw!r}')
    if '..' in text:
        start_text, end_text = text.split('..', 1)
    else:
        start_text = end_text = text
    return {'start': int(start_text.lstrip('<')),
            'end': int(end_text.lstrip('>')),
            'partial_start': start_text.startswith('<'),
            'partial_end': end_text.startswith('>')}


def _parse_part(text, raw):
    """Parse one join element: a span, optionally wrapped in complement()."""
    text = text.strip()
    strand = '+'
    if text.startswith('complement(') and text.endswith(')'):
        strand = '-'
        text = text[len('complement('):-1].strip()
        if 'complement(' in text or text.startswith('join('):
            raise ValueError(f'nested location inside a join part not supported: {raw!r}')
    part = _parse_span(text, raw)
    part['strand'] = strand
    return part


def parse_location(raw):
    text = raw.strip()
    if text.startswith('complement('):
        if not text.endswith(')'):
            raise ValueError(f'unbalanced complement in location: {raw!r}')
        inner = text[len('complement('):-1].strip()
        if inner.startswith('join('):
            inner_location = parse_location(inner)
            if inner_location.strand == 'mixed':
                raise ValueError(f'complement of a mixed-strand join not supported: {raw!r}')
            parts = [{**part, 'strand': '-'} for part in inner_location.parts]
            return Location(inner_location.kind, '-', parts, raw.strip())
        part = _parse_part(text, raw)
        return Location('span', '-', [part], raw.strip())
    if text.startswith('join(') and text.endswith(')'):
        inner = text[len('join('):-1].strip()
        if text.count('join(') != 1:
            raise ValueError(f'nested join not supported: {raw!r}')
        parts = [_parse_part(piece, raw) for piece in _split_top_level(inner)]
        if len(parts) < 2:
            raise ValueError(f'join with fewer than two parts: {raw!r}')
        strands = {part['strand'] for part in parts}
        aggregate = strands.pop() if len(strands) == 1 else 'mixed'
        return Location('join', aggregate, parts, raw.strip())
    part = _parse_part(text, raw)
    return Location('span', part['strand'], [part], raw.strip())


def _unquote(value):
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _parse_qualifiers(lines):
    """Parse /key=value lines; quoted values may wrap across lines."""
    qualifiers = {}
    key, chunks, quoted = None, [], False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('/'):
            if key is not None:
                qualifiers.setdefault(key, []).append(_unquote(''.join(chunks)))
            body = stripped[1:]
            if '=' in body:
                key, first = body.split('=', 1)
                key = key.strip()
            else:
                key, first = body.strip(), ''
            if first.startswith('"'):
                quoted = not (len(first) >= 2 and first.endswith('"'))
                chunks = [first]
            else:
                quoted = False
                chunks = [first]
        elif key is not None:
            text = stripped
            if quoted:
                if text.endswith('"') and not text.endswith('\\"'):
                    quoted = False
                chunks.append(text)
            else:
                raise ValueError(f'unexpected feature continuation: {line!r}')
    if key is not None:
        qualifiers.setdefault(key, []).append(_unquote(''.join(chunks)))
    return qualifiers


class GenBankRecord:
    __slots__ = ('locus_name', 'length', 'molecule', 'topology', 'division',
                 'date', 'definition', 'accession', 'version', 'organism',
                 'taxonomy', 'features', 'sequence')

    def __init__(self, **fields):
        for name in self.__slots__:
            setattr(self, name, fields[name])

    def as_dict(self):
        return {
            'locus_name': self.locus_name, 'length': self.length,
            'molecule': self.molecule, 'topology': self.topology,
            'division': self.division, 'date': self.date,
            'definition': self.definition, 'accession': self.accession,
            'version': self.version, 'organism': self.organism,
            'taxonomy': self.taxonomy,
            'features': [{'key': feature['key'],
                          'location': feature['location'].as_dict(),
                          'qualifiers': feature['qualifiers']}
                         for feature in self.features],
            'sequence': self.sequence,
        }

    def features_of(self, key):
        return [feature for feature in self.features if feature['key'] == key]


def parse_genbank(text):
    """Parse one complete GenBank flat-file record; raise on anything unsupported."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith('LOCUS'):
        raise ValueError('record does not start with a LOCUS line')
    header = lines[0].split()
    if len(header) < 8:
        raise ValueError(f'unexpected LOCUS layout: {lines[0]!r}')
    locus_name = header[1]
    try:
        bp_index = header.index('bp')
        length = int(header[bp_index - 1])
    except (ValueError, IndexError):
        raise ValueError(f'cannot read sequence length from LOCUS: {lines[0]!r}') from None
    molecule = header[bp_index + 1] if bp_index + 1 < len(header) else ''
    rest = header[bp_index + 2:]
    topology = 'circular'
    if 'circular' not in rest:
        if 'linear' in rest:
            topology = 'linear'
        else:
            raise ValueError(f'LOCUS line lacks topology: {lines[0]!r}')
    if topology == 'circular':
        rest = rest[rest.index('circular') + 1:]
    else:
        rest = rest[rest.index('linear') + 1:]
    if not rest:
        raise ValueError(f'LOCUS line lacks division and date: {lines[0]!r}')
    division, date = rest[0], rest[-1]

    definition = accession = version = organism = ''
    taxonomy_lines, taxonomy = [], False
    index, total = 1, len(lines)
    while index < total and not lines[index].startswith('FEATURES'):
        line = lines[index]
        if line.startswith('DEFINITION'):
            definition = line[12:].strip()
        elif line.startswith('ACCESSION'):
            accession = line[12:].split()[0] if line[12:].split() else ''
        elif line.startswith('VERSION'):
            version = line[12:].split()[0] if line[12:].split() else ''
        elif line.startswith('SOURCE'):
            organism = line[12:].strip()
        elif line.startswith('  ORGANISM'):
            taxonomy = True
            organism = line[12:].strip() or organism
        elif taxonomy and line.startswith('            '):
            taxonomy_lines.append(line.strip())
        elif line.startswith(('KEYWORDS', 'COMMENT', 'PRIMARY', 'DBLINK',
                              'REFERENCE', 'CONSRTM', 'MGLOAD')):
            taxonomy = False  # later header sections may carry 12-space lines too
        elif line.strip() and not line.startswith(' '):
            taxonomy = False
            raise ValueError(f'unsupported header section: {line.split()[0]!r}')
        index += 1
    taxonomy = '; '.join(part.rstrip(';') for part in taxonomy_lines if part and part != '.')

    if index >= total or not lines[index].startswith('FEATURES'):
        raise ValueError('record has no FEATURES section')
    index += 1

    features = []
    while index < total and not lines[index].startswith('ORIGIN'):
        line = lines[index]
        if not line.strip() or line.startswith('//'):
            index += 1
            continue
        match = _FEATURE_RE.match(line)
        if not match:
            raise ValueError(f'unsupported FEATURES line: {line!r}')
        key, first_location = match.group(1), match.group(2)
        entry_lines = [line]
        index += 1
        while index < total:
            follow = lines[index]
            if _FEATURE_RE.match(follow) or follow.startswith('ORIGIN') or follow.startswith('//'):
                break
            if not follow.strip():
                break
            entry_lines.append(follow)
            index += 1
        location_chunks = [first_location]
        qualifier_lines = []
        for entry in entry_lines[1:]:
            # Location continuation lines only occur before the first
            # qualifier; everything after belongs to /qualifiers, including
            # wrapped quoted values such as /translation.
            if not qualifier_lines and not entry.strip().startswith('/'):
                location_chunks.append(entry[_CONTINUATION_WIDTH:].strip())
            else:
                qualifier_lines.append(entry)
        location_raw = ' '.join(chunk for chunk in location_chunks if chunk)
        features.append({'key': key, 'location': parse_location(location_raw),
                         'qualifiers': _parse_qualifiers(qualifier_lines)})

    if index >= total or not lines[index].startswith('ORIGIN'):
        raise ValueError('record has no ORIGIN section')
    chunks = []
    for line in lines[index + 1:]:
        if line.startswith('//'):
            break
        chunks.append(re.sub(r'[\d\s]', '', line).lower())
    sequence = ''.join(chunks)
    if len(sequence) != length:
        raise ValueError(f'ORIGIN length {len(sequence)} differs from LOCUS length {length}')
    return GenBankRecord(locus_name=locus_name, length=length, molecule=molecule,
                         topology=topology, division=division, date=date,
                         definition=definition, accession=accession,
                         version=version, organism=organism, taxonomy=taxonomy,
                         features=features, sequence=sequence)
