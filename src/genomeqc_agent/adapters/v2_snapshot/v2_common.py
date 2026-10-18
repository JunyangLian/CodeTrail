"""Deterministic canonical serialization and digest helpers (V1 house style).

Every persisted artifact in organelle_v2 is canonical UTF-8 JSON:
sorted keys, compact separators, trailing newline. Same inputs must always
produce byte-identical outputs so that reruns are auditable by hash.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical(value) -> bytes:
    """Return the canonical UTF-8 JSON bytes for a JSON-compatible value."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')) + '\n').encode('utf-8')


def digest(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_bytes())


def write_json(path: Path, value) -> bytes:
    data = canonical(value)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)
    return data


def digest_json(value) -> str:
    return digest(canonical(value))


def digest_file(path: Path) -> str:
    return digest(Path(path).read_bytes())


def sha_of_tree(root: Path) -> dict:
    """Map of POSIX relative path -> sha256 for every regular file under root.

    Symlinks are rejected: bundled artifacts must be regular files so that
    cross-platform extraction cannot smuggle paths outside the bundle.
    """
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'symlink not allowed in artifact tree: {path}')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest_file(path)
    return result
