"""Atomic JSON replacement with bounded, observable Windows sharing retries."""
import errno
import json
import os
from pathlib import Path
import tempfile
import time

REPLACE_DELAYS = (0.025, 0.05, 0.1, 0.2)


def write_json_atomic(path, payload, audit_path=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    audit_path = Path(audit_path or path.parent / 'persistence.jsonl')
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n', delete=False,
                                    dir=path.parent, prefix=path.name + '.', suffix='.tmp') as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    started = time.monotonic()
    failed = False
    def audit(event, **details):
        # Do not serialize state contents or credentials. Audit failures fail closed.
        entry = {'event': event, 'target': str(path), 'temporary': str(temporary),
                 'at_unix_ns': time.time_ns(),
                 'elapsed_ms': round((time.monotonic() - started) * 1000), **details}
        with audit_path.open('a', encoding='utf-8', newline='\n') as output:
            output.write(json.dumps(entry, ensure_ascii=True) + '\n')
    for attempt in range(len(REPLACE_DELAYS) + 1):
        try:
            temporary.replace(path)
        except OSError as exc:
            transient = isinstance(exc, PermissionError) and (
                getattr(exc, 'winerror', None) in {5, 32, 33} or
                (os.name == 'nt' and exc.errno == errno.EACCES))
            retry = transient and attempt < len(REPLACE_DELAYS)
            audit('persistence_retry' if retry else 'persistence_failed', attempt=attempt + 1,
                  error_type=type(exc).__name__, winerror=getattr(exc, 'winerror', None),
                  errno=exc.errno, will_retry=retry,
                  delay_seconds=REPLACE_DELAYS[attempt] if retry else 0)
            if not retry:
                # Preserve both the old target and failed temp for diagnosis.
                raise
            failed = True
            time.sleep(REPLACE_DELAYS[attempt])
        else:
            if failed:
                audit('persistence_recovered', attempt=attempt + 1)
            return path
