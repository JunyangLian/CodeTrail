"""Small repository index: symbols + BM25 + real optional dense embeddings.

No synthetic semantic vectors. Dense failures are explicit lexical degradation.
Cached vectors are scoped by model/endpoint/chunk recipe and content hash.
"""
import ast
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import urllib.request

from .security import redact_text, sensitive_workspace_path
from .workspace import IGNORED_PATH_NAMES

SKIP = IGNORED_PATH_NAMES | {'node_modules', 'artifacts', 'benchmarks', 'validation', 'vendor', 'dist', 'build', '.codetrail'}
SUFFIXES = {'.py', '.js', '.ts', '.tsx', '.jsx', '.md', '.toml', '.json', '.yaml', '.yml', '.txt'}
SECRET = re.compile(r'(?i)\b(?:sk-|ghp_|github_pat_)[\w-]{12,}')


def tokens(text):
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', str(text))
    result = re.findall(r'[a-z0-9]+', text.lower().replace('_', ' '))
    for span in re.findall(r'[\u3400-\u9fff]+', text):
        result.extend(span[i:i+2] for i in range(max(1, len(span)-1)))
    return result


def normalized(vector, dimensions=None):
    if not isinstance(vector, list) or not vector or (dimensions is not None and len(vector) != dimensions):
        raise ValueError('Embedding dimension mismatch or empty vector')
    if any(type(x) not in {int, float} or not math.isfinite(x) for x in vector):
        raise ValueError('Embedding contains non-finite/non-numeric values')
    norm = math.sqrt(sum(x*x for x in vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('Embedding has invalid norm')
    return [x/norm for x in vector]


class SiliconFlowEmbedding:
    endpoint = 'https://api.siliconflow.cn/v1/embeddings'

    def __init__(self, model='BAAI/bge-m3', timeout=30, network_route='environment'):
        # Model is from the official embedding catalogue, not a chat model alias.
        if model not in {'BAAI/bge-m3', 'BAAI/bge-large-zh-v1.5', 'BAAI/bge-large-en-v1.5'}:
            raise ValueError('Unsupported explicitly configured embedding model')
        self.model, self.timeout = model, timeout
        if network_route not in {'environment', 'direct'}:
            raise ValueError('Unknown explicit network route')
        self.network_route = network_route
        self.identity = self.endpoint + '|' + model + '|normalized|code-chunks-v1'
        self.last_usage = None
        self.requests = 0

    def encode(self, texts):
        if not texts or any(not isinstance(t, str) or not t.strip() or len(t) > 6000 for t in texts):
            raise ValueError('Embedding input must be non-empty bounded text')
        key = os.environ.get('SILICONFLOW_API_KEY', '')
        if not key:
            raise ValueError('SILICONFLOW_API_KEY missing; no other credential is substituted')
        request = urllib.request.Request(self.endpoint, data=json.dumps({'model': self.model, 'input': texts,
            'encoding_format': 'float'}).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        self.requests += 1
        opener = urllib.request.urlopen if self.network_route == 'environment' else urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        with opener(request, timeout=self.timeout) as response:
            data = json.loads(response.read(4_000_000))
        if data.get('model') and data['model'] != self.model:
            raise ValueError('Embedding response model differs from requested ID')
        rows = data.get('data', [])
        if len(rows) != len(texts) or sorted(r.get('index', -1) for r in rows) != list(range(len(texts))):
            raise ValueError('Embedding result count/index mismatch')
        vectors = [normalized(r['embedding']) for r in sorted(rows, key=lambda r: r['index'])]
        if len({len(v) for v in vectors}) != 1:
            raise ValueError('Embedding dimensions changed within batch')
        self.last_usage = data.get('usage')
        return vectors


def chunk_file(relative, text):
    lines = text.splitlines()
    ranges = []
    if relative.endswith('.py'):
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    calls = sorted({ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)})[:20]
                    ranges.append((node.lineno, node.end_lineno, node.name, calls))
        except SyntaxError:
            pass
    # Include module preamble and uncovered code/documents, not only functions.
    covered = set()
    for start, end, _, _ in ranges:
        covered.update(range(start, end+1))
    for start in range(1, len(lines)+1, 45):
        end = min(start+44, len(lines))
        if any(n not in covered for n in range(start, end+1)):
            ranges.append((start, end, '<module>', []))
    result = []
    source_hash = hashlib.sha256(text.encode()).hexdigest()
    for start, end, symbol, calls in ranges:
        for offset in range(start, end+1, 45):
            stop = min(offset+44, end)
            content = '\n'.join(lines[offset-1:stop])[:4500]
            if not content.strip():
                continue
            context = f'File: {relative}\nSymbol: {symbol}\nLines: {offset}-{stop}\nCalls: {", ".join(calls)}\n{content}'
            result.append({'id': 'src_' + hashlib.sha256(context.encode()).hexdigest()[:24], 'path': relative,
                           'symbol': symbol, 'start': offset, 'end': stop, 'calls': calls,
                           'source_sha256': source_hash, 'text': context})
    return result


class RepositoryIndex:
    def __init__(self, root, embedding=None, path_filter=None):
        self.root = Path(root).resolve()
        self.embedding = embedding
        self.path_filter = path_filter
        self.database = self.root / '.pico/retrieval.sqlite3'
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.last_audit = {}
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE IF NOT EXISTS vectors (identity TEXT, hash TEXT, dimensions INTEGER, value TEXT, PRIMARY KEY(identity,hash))')
        self.chunks = []
        self.index_audit = {}

    def refresh(self):
        chunks, count, total, omitted = [], 0, 0, 0
        for base, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not (Path(base)/d).is_symlink() and not d.startswith('.')
                             and (not self.path_filter or self.path_filter(Path(base)/d))
                             and not (getattr((Path(base)/d).lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)))
            for name in sorted(files):
                path = Path(base) / name
                relative = path.relative_to(self.root)
                if (path.is_symlink() or path.suffix.lower() not in SUFFIXES or sensitive_workspace_path(relative)
                        or path.stat().st_nlink > 1 or (self.path_filter and not self.path_filter(path))):
                    continue
                if count >= 200 or total >= 2_000_000 or len(chunks) >= 800:
                    omitted += 1
                    continue
                try:
                    path.resolve().relative_to(self.root)
                    if path.stat().st_size > 131072:
                        omitted += 1
                        continue
                    text = path.read_bytes().decode('utf-8')
                except (OSError, UnicodeError, ValueError):
                    continue
                if redact_text(text) != text or SECRET.search(text):
                    omitted += 1
                    continue
                total += len(text.encode())
                count += 1
                chunks.extend(chunk_file(relative.as_posix(), text))
        self.chunks = chunks[:800]
        self.index_audit = {'files': count, 'chunks': len(self.chunks), 'omitted_files': omitted,
                            'source_bytes': total, 'recipe': 'code-chunks-v1'}
        return self.index_audit

    def _bm25(self, texts, query):
        documents = [Counter(tokens(t)) for t in texts]
        terms = set(tokens(query))
        average = sum(sum(d.values()) for d in documents) / max(1, len(documents)) or 1
        scores = []
        for index, document in enumerate(documents):
            score = 0
            length = sum(document.values())
            for term in terms:
                frequency = document.get(term, 0)
                df = sum(term in d for d in documents)
                idf = math.log(1 + (len(documents)-df+.5)/(df+.5))
                score += idf * frequency * 2.2 / (frequency + 1.2*(.25+.75*length/average))
            if score:
                scores.append((index, score))
        return sorted(scores, key=lambda x: (-x[1], x[0]))

    def _dense(self, texts, query):
        identity = self.embedding.identity
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts + [query]]
        vectors, missing = {}, []
        with sqlite3.connect(self.database) as db:
            for key, text in zip(keys, texts + [query]):
                row = db.execute('SELECT dimensions,value FROM vectors WHERE identity=? AND hash=?', (identity, key)).fetchone()
                if row:
                    vectors[key] = normalized(json.loads(row[1]), row[0])
                elif key not in {k for k, _ in missing}:
                    missing.append((key, text))
            existing = {row[0] for row in db.execute('SELECT DISTINCT dimensions FROM vectors WHERE identity=?', (identity,))}
            if len(existing) > 1:
                raise ValueError('Mixed embedding dimensions in index; create a new identity')
            dimension = next(iter(existing), None)
            for start in range(0, len(missing), 16):
                batch = missing[start:start+16]
                values = self.embedding.encode([t for _, t in batch])
                if len(values) != len(batch):
                    raise ValueError('Embedding provider returned wrong count')
                for (key, _), vector in zip(batch, values):
                    vector = normalized(vector, dimension)
                    dimension = len(vector)
                    vectors[key] = vector
                    db.execute('INSERT INTO vectors VALUES (?,?,?,?)', (identity, key, dimension, json.dumps(vector)))
        query_vector = vectors[keys[-1]]
        if any(len(v) != len(query_vector) for v in vectors.values()):
            raise ValueError('Embedding space mismatch')
        return sorted([(i, sum(a*b for a, b in zip(vectors[key], query_vector)))
                       for i, key in enumerate(keys[:-1])], key=lambda x: (-x[1], x[0]))

    def rank(self, records, query, limit=5):
        started = time.monotonic()
        if not isinstance(query, str) or not query.strip() or len(query) > 6000 or not 1 <= limit <= 20:
            raise ValueError('Bounded non-empty query and limit 1..20 required')
        texts = [r['text'] for r in records]
        lexical = self._bm25(texts, query)
        exact = [(i, 1) for i, r in enumerate(records) if (r.get('symbol') and r['symbol'] != '<module>' and
                  r['symbol'].casefold() in query.casefold()) or r.get('path', '\0') in query]
        ranks = [exact, lexical]
        degradation = None
        dense = []
        if self.embedding and records:
            try:
                dense = self._dense(texts, query)
                ranks.append(dense)
            except Exception as exc:
                degradation = {'type': type(exc).__name__, 'detail': redact_text(str(exc))[:300]}
        combined, channels = {}, {}
        for channel, ranked in zip(('exact', 'bm25', 'dense'), ranks):
            for position, (index, _) in enumerate(ranked[:40], 1):
                combined[index] = combined.get(index, 0) + 1/(60+position)
                channels.setdefault(index, []).append(channel)
        selected = sorted(combined, key=lambda i: (-combined[i], i))[:limit]
        result = [{**records[i], 'rrf_score': combined[i], 'channels': channels[i]} for i in selected]
        self.last_audit = {'query': query, 'candidates': len(records), 'lexical_hits': len(lexical),
            'dense_candidates': len(dense), 'selected_ids': [r['id'] for r in result],
            'embedding': self.embedding.identity if self.embedding else None,
            'mode': 'hybrid' if dense else 'lexical', 'degradation': degradation,
            'dense_configured': self.embedding is not None, 'duration_ms': round((time.monotonic()-started)*1000),
            'network_route': getattr(self.embedding, 'network_route', None),
            'provider_usage': getattr(self.embedding, 'last_usage', None)}
        return result

    def search(self, query, limit=5):
        self.refresh()
        result = self.rank(self.chunks, query, limit)
        self.last_audit.update(self.index_audit)
        return result

    def symbols(self, name):
        self.refresh()
        return [{k: c[k] for k in ('id', 'path', 'symbol', 'start', 'end', 'calls', 'source_sha256')}
                for c in self.chunks if c['symbol'] == name or name in c['calls']][:30]
