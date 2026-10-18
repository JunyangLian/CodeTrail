"""Pre-commit secret scan (P0): fails loudly if key material could be committed.

Run before every commit:  python scripts/check_secrets.py
Scans every file that WOULD be tracked (respects .gitignore via git).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "SiliconFlow/OpenAI-style key"),
    (re.compile(r"(?i)api[_-]?key\s*[=:]\s*[\"'][A-Za-z0-9_-]{16,}[\"']"), "inline api key"),
    (re.compile(r"(?i)(authorization\s*[:=]\s*[\"']\s*Bearer\s+)sk-"), "bearer key"),
    (re.compile(r"(?i)\b(password|passwd|secret)\b\s*[=:]\s*[\"'][^\"']{8,}[\"']"),
     "hardcoded secret"),
]

ALLOWED_MARKERS = ("sk-invalid", "<api_key>", "your-key", "example")


def tracked_files() -> list[Path]:
    """Files git would track (respects .gitignore); empty before git init."""
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others",
                              "--exclude-standard"],
                             capture_output=True, text=True, check=True)
        return [Path(line) for line in out.stdout.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        # no git yet: scan everything except the known-ignored paths;
        # .env is credential-bearing by design and is never scanned/committed
        ignored = {".venv", "data", "__pycache__", ".pytest_cache",
                   ".ruff_cache", ".git", ".env"}
        return [p for p in Path(".").rglob("*") if p.is_file()
                and not (ignored & set(p.parts)) and p.name != "uv.lock"]


def main() -> int:
    violations = []
    scanned = 0
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for pattern, label in PATTERNS:
            for match in pattern.finditer(text):
                if any(marker in match.group(0) for marker in ALLOWED_MARKERS):
                    continue
                line_no = text[:match.start()].count("\n") + 1
                violations.append(f"{path}:{line_no}: {label}")
    print(f"scanned {scanned} tracked files")
    if violations:
        print("SECRET VIOLATIONS:")
        print("\n".join(violations))
        return 1
    print("CLEAN: no credential material in tracked files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
