"""Trusted external unittest entry point, launched with -I in a pristine copy."""
import contextlib
import io
import json
from pathlib import Path
import sys
import time
import unittest

def main():
    workspace = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(workspace))
    started = time.monotonic()
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            suite = unittest.defaultTestLoader.discover(str(workspace), pattern='test*.py')
            result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
        payload = {'successful':result.wasSuccessful(), 'tests_run':result.testsRun,
                   'failures':len(result.failures), 'errors':len(result.errors),
                   'skipped':len(result.skipped), 'expected_failures':len(result.expectedFailures),
                   'unexpected_successes':len(result.unexpectedSuccesses), 'output':output.getvalue()}
    except BaseException as exc:
        payload = {'successful':False, 'tests_run':None, 'error':f'{type(exc).__name__}: {exc}', 'output':output.getvalue()}
    payload['elapsed_ms'] = round((time.monotonic()-started)*1000)
    print(json.dumps(payload, ensure_ascii=True))
    raise SystemExit(0 if payload['successful'] else 1)

if __name__ == '__main__':
    main()
