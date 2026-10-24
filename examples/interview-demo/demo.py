"""Explicitly reset the exercise, backing up the current implementation first."""

import argparse
from datetime import datetime
from pathlib import Path
import shutil


def reset():
    root = Path(__file__).resolve().parent
    target = root / "order_pricing.py"
    template = root / "order_pricing.buggy.txt"
    if target.is_symlink() or template.is_symlink():
        raise ValueError("Refusing to reset a symlink")
    content = template.read_bytes()
    if target.exists() and target.read_bytes() != content:
        backups = root / ".pico/demo-backups"
        backups.mkdir(parents=True, exist_ok=True)
        backup = backups / ("order_pricing-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".py")
        shutil.copy2(target, backup)
        print("Previous implementation backed up to", backup)
    target.write_bytes(content)
    print("Reset order_pricing.py to the deliberate buggy version. Tests and history are unchanged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["reset"])
    parser.parse_args()
    reset()
