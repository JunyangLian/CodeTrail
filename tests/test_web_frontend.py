"""Execute the shipped polling code under Node; browser evidence is recorded separately."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_polling_recovery():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for frontend behavioral tests')
    result = subprocess.run([node, '--test', str(Path(__file__).with_name('web_poll.test.cjs'))],
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
