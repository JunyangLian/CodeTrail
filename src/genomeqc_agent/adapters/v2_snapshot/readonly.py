"""Bounded tools over immutable public bytes; no model-selected filesystem IO.

These supply facts for a future tool condition, never new rules for group A.
No shell, eval, network, write operation, target or standard answer is exposed.
"""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
from types import MappingProxyType

# V3 snapshot patch (the ONLY change vs bench/inspect_adapter/readonly.py):
# the V2 import `from bench.v2.runtime import visible_files` is redirected to
# the byte-identical snapshot of bench/visible_input.py, so this module does
# not pull V2's runtime/harness chain. See SNAPSHOT_MANIFEST.json.
from genomeqc_agent.adapters.v2_snapshot.visible_input import visible_files


class PublicFiles:
    def __init__(self, case):
        case = Path(case).resolve(strict=True)
        data = {}
        for path in visible_files(case):
            content = path.read_bytes()
            if len(content) > 128_000:
                raise ValueError('public file exceeds prototype size limit')
            content.decode('utf-8')  # This prototype accepts plain text only.
            data[path.relative_to(case).as_posix()] = content
        if sum(map(len, data.values())) > 256_000:
            raise ValueError('public packet exceeds prototype size limit')
        self._data = MappingProxyType(data)

    def manifest(self):
        return [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                for name, data in sorted(self._data.items())]

    def text(self, path):
        if (not isinstance(path, str) or '\\' in path or '\x00' in path or
            PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or
            PurePosixPath(path).as_posix() != path or path not in self._data):
            raise ValueError('path is outside this sample public whitelist')
        return self._data[path].decode('utf-8')

    def read_lines(self, path, start_line=1, line_count=80):
        if type(start_line) is not int or type(line_count) is not int or start_line < 1 or not 1 <= line_count <= 200:
            raise ValueError('line range must start at 1 or later and contain 1..200 lines')
        text = self.text(path)
        lines = text.splitlines()
        selected = lines[start_line - 1:start_line - 1 + line_count]
        numbered, count = [], 0
        for number, line in enumerate(selected, start_line):
            encoded = len(line.encode('utf-8'))
            if count + encoded > 16_000:
                if not numbered:
                    raise ValueError('single line exceeds prototype output limit')
                break
            numbered.append({'line': number, 'text': line})
            count += encoded
        next_line = start_line + len(numbered)
        truncated = bool(lines) and next_line <= len(lines)
        return {'path': path, 'bytes': len(text.encode('utf-8')), 'total_lines': len(lines),
                'lines': numbered, 'truncated': truncated,
                'next_line': next_line if truncated else None}

    def fasta(self, path):
        records, name, chunks = {}, None, []
        for line in self.text(path).splitlines():
            if line.startswith('>'):
                if name is not None:
                    records[name] = ''.join(chunks)
                tokens = line[1:].split()
                if not tokens or tokens[0] in records or tokens[0] == name:
                    raise ValueError('FASTA header is empty or duplicated')
                name, chunks = tokens[0], []
            elif line.strip():
                if name is None or any(c.isspace() for c in line.strip()):
                    raise ValueError('invalid FASTA record layout')
                chunks.append(line.strip())
        if name is not None:
            records[name] = ''.join(chunks)
        if not records or any(not sequence for sequence in records.values()):
            raise ValueError('FASTA has no complete nonempty records')
        return records

    def table(self, path):
        reader = csv.DictReader(io.StringIO(self.text(path)), delimiter='\t')
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError('TSV header is empty or duplicated')
        rows = list(reader)
        if any(None in row or None in row.values() for row in rows):
            raise ValueError('TSV row width differs from header')
        return reader.fieldnames, rows

    def stats(self, path):
        text = self.text(path)
        suffix = PurePosixPath(path).suffix
        result = {'path': path, 'bytes': len(text.encode('utf-8'))}
        if suffix in {'.fa', '.faa', '.fasta'}:
            records = self.fasta(path)
            result.update(format='fasta', record_count=len(records), records=[
                {'id': name, 'length': len(seq), 'lowercase_acgt': sum(c in 'acgt' for c in seq),
                 'N': seq.count('N'), 'n': seq.count('n')}
                for name, seq in records.items()])
        elif suffix == '.tsv':
            header, rows = self.table(path)
            result.update(format='tsv', columns=header, row_count=len(rows))
        elif suffix in {'.gff', '.gff3', '.gtf'}:
            rows = [line.split('\t') for line in text.splitlines() if line and not line.startswith('#')]
            if any(len(row) != 9 for row in rows):
                raise ValueError('feature file has a row with other than nine fields')
            result.update(format='features', feature_rows=len(rows),
                          feature_counts=dict(Counter(row[2] for row in rows)))
        else:
            result.update(format='text', line_count=len(text.splitlines()))
        return result

    def annotation_counts(self, query_path, table_path):
        ids = self.fasta(query_path)
        header, rows = self.table(table_path)
        flags = [key for key in header if key.endswith('-Annotated')]
        if 'query' not in header or not flags or any(row[key] not in {'0', '1'} for row in rows for key in flags):
            raise ValueError('query column or binary annotation columns are invalid')
        counts = Counter(row['query'] for row in rows)
        annotated = {row['query'] for row in rows if any(row[key] == '1' for key in flags)} & set(ids)
        matched = set(ids) & set(counts)
        return {'query_path': query_path, 'table_path': table_path, 'query_records': len(ids),
                'table_rows': len(rows), 'matched_ids': len(matched),
                'queries_without_row': sorted(set(ids) - set(counts)),
                'table_ids_without_query': sorted(set(counts) - set(ids)),
                'duplicate_table_ids': sorted(name for name, count in counts.items() if count > 1),
                'annotated_query_records': len(annotated), 'annotation_columns': flags,
                'coverage_percent': 100 * len(annotated) / len(ids),
                'definition': 'exact first FASTA token joins query; any annotation flag equals 1'}

    def interval_counts(self, sequence_path, regions_path):
        sequences = self.fasta(sequence_path)
        header, rows = self.table(regions_path)
        if not {'seqid', 'start', 'end'} <= set(header):
            raise ValueError('regions require seqid, start and end columns')
        result = []
        for row in rows:
            try:
                start, end = int(row['start']), int(row['end'])
                seq = sequences[row['seqid']]
            except (ValueError, KeyError):
                raise ValueError('region has an invalid coordinate or unknown sequence') from None
            if not 1 <= start <= end <= len(seq):
                raise ValueError('region is outside the sequence')
            window = seq[start - 1:end]
            result.append({'seqid': row['seqid'], 'start': start, 'end': end, 'length': len(window),
                           'lowercase_acgt': sum(c in 'acgt' for c in window),
                           'N': window.count('N'), 'n': window.count('n'),
                           'other': sum(c not in 'acgtNn' for c in window)})
        return {'sequence_path': sequence_path, 'regions_path': regions_path,
                'coordinates': 'one-based closed', 'regions': result}


def inspect_tools(files):
    """Inspect functions only see the public snapshot of one sample."""
    from inspect_ai.tool import tool, ToolError

    def result(function, *args):
        try:
            return json.dumps(function(*args), ensure_ascii=False, sort_keys=True)
        except ValueError as error:
            raise ToolError(str(error)) from None

    @tool
    def list_files():
        async def execute():
            """List this sample's available public relative paths and byte sizes."""
            return result(files.manifest)
        return execute

    @tool
    def read_file():
        async def execute(path: str, start_line: int = 1, line_count: int = 80):
            """Read a bounded page of a public file with one-based line numbers.

            Args:
                path: Exact relative path from list_files.
                start_line: First line to read, starting at one.
                line_count: Number of lines, between one and 200.
            """
            return result(files.read_lines, path, start_line, line_count)
        return execute

    @tool
    def file_stats():
        async def execute(path: str):
            """Count FASTA records, TSV rows or GFF features from actual bytes.

            Args:
                path: Exact public file path.
            """
            return result(files.stats, path)
        return execute

    @tool
    def annotation_counts():
        async def execute(query_path: str, table_path: str):
            """Recompute exact protein/table ID joins and annotation counts.

            Args:
                query_path: Public protein FASTA path.
                table_path: Public TSV with query and binary annotation columns.
            """
            return result(files.annotation_counts, query_path, table_path)
        return execute

    @tool
    def interval_counts():
        async def execute(sequence_path: str, regions_path: str):
            """Count lowercase bases and N in one-based closed sequence intervals.

            Args:
                sequence_path: Public delivery FASTA path.
                regions_path: Public TSV with seqid, start and end columns.
            """
            return result(files.interval_counts, sequence_path, regions_path)
        return execute

    return [list_files(), read_file(), file_stats(), annotation_counts(), interval_counts()]
