from pathlib import Path
import shutil
import subprocess
import sys


def test_demo_reset_backs_up_changes_and_preserves_tests(tmp_path):
    source = Path(__file__).resolve().parent.parent / "examples/interview-demo"
    for name in ("demo.py", "order_pricing.buggy.txt", "test_order_pricing.py"):
        shutil.copy2(source / name, tmp_path / name)
    implementation = b"# locally repaired implementation\n"
    (tmp_path / "order_pricing.py").write_bytes(implementation)
    tests = (tmp_path / "test_order_pricing.py").read_bytes()
    result = subprocess.run([sys.executable, str(tmp_path / "demo.py"), "reset"], capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "order_pricing.py").read_bytes() == (tmp_path / "order_pricing.buggy.txt").read_bytes()
    backups = list((tmp_path / ".pico/demo-backups").glob("*.py"))
    assert len(backups) == 1 and backups[0].read_bytes() == implementation
    assert (tmp_path / "test_order_pricing.py").read_bytes() == tests
    subprocess.run([sys.executable, str(tmp_path / "demo.py"), "reset"], check=True, capture_output=True)
    assert len(list((tmp_path / ".pico/demo-backups").glob("*.py"))) == 1
