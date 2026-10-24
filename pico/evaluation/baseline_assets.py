"""Verify immutable v1 data independently of an intentionally upgraded runtime."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[2]
BASELINE=ROOT/'benchmarks/repair_suite_v1'
ARCHIVE=ROOT/'benchmarks/results/repair-suite-v1-2026-10-24/evidence.zip'
ARCHIVE_SHA='7005cad4f26ea152ebb6ac533f495b1af9b56db49615d615f47e6fe2409a3c71'
FREEZE_SHA='bff6a0b10537b8715e85265eae956ee90553c1d6e7a3d765b05305f2e8933b01'

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def verify_baseline_assets():
    assert digest(BASELINE/'freeze.json')==FREEZE_SHA,'Original baseline freeze changed'
    assert digest(ARCHIVE)==ARCHIVE_SHA,'Original baseline evidence archive changed'
    freeze=json.loads((BASELINE/'freeze.json').read_text(encoding='utf-8'))
    drift=[]
    with zipfile.ZipFile(ARCHIVE) as archive:
        for relative,expected in freeze['hashes'].items():
            assert hashlib.sha256(archive.read(relative)).hexdigest()==expected,relative
            current=ROOT/relative
            if relative.startswith('benchmarks/repair_suite_v1/') or relative=='scripts/repair_external_verifier.py':
                assert digest(current)==expected,relative
            elif not current.exists() or digest(current)!=expected:
                drift.append(relative)
    return {'freeze_sha256':FREEZE_SHA,'baseline_archive_sha256':ARCHIVE_SHA,
            'original_runtime_verified_in_archive':True,'current_runtime_matches_v1':not drift,
            'upgraded_runtime_paths':drift}
