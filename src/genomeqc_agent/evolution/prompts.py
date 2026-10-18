"""P0/P1: immutable prompt version registry with SHA256 and diffs.

A prompt version, once registered, can never be mutated: the content hash is
re-verified on every read. Strategy candidates may only REFERENCE registered
versions - they cannot rewrite Python source, the Oracle, the Scorer or any
frozen data.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

SIMULATION_MARK = "offline_simulation"


class PromptIntegrityError(RuntimeError):
    """A registered prompt's content no longer matches its pinned hash."""


class PromptRegistry:
    def __init__(self, registry_path: Path):
        self.registry_path = Path(registry_path)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.versions: dict[str, dict] = {}
        if self.registry_path.is_file():
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            self.versions = data.get("versions", {})

    @staticmethod
    def sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def register(self, version: str, content: str, notes: str = "",
                 author: str = "system") -> dict:
        if version in self.versions:
            pinned = self.versions[version]
            if pinned["sha256"] != self.sha256(content):
                raise PromptIntegrityError(
                    f"prompt {version} already registered with a different "
                    f"immutable content (sha256 mismatch)")
            return pinned  # idempotent re-registration of identical content
        record = {
            "version": version,
            "sha256": self.sha256(content),
            "content": content,
            "notes": notes,
            "author": author,
            "registered_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "simulation": SIMULATION_MARK,
        }
        self.versions[version] = record
        self.save()
        return record

    def get(self, version: str) -> dict:
        record = self.versions.get(version)
        if record is None:
            raise KeyError(f"unregistered prompt version: {version}")
        if record["sha256"] != self.sha256(record["content"]):
            raise PromptIntegrityError(
                f"prompt {version} content drifted from its pinned sha256")
        return record

    def diff(self, base_version: str, candidate_version: str) -> str:
        base = self.get(base_version)["content"].splitlines(keepends=True)
        cand = self.get(candidate_version)["content"].splitlines(keepends=True)
        return "".join(difflib.unified_diff(
            base, cand, fromfile=f"prompt/{base_version}",
            tofile=f"prompt/{candidate_version}"))

    def save(self) -> None:
        self.registry_path.write_text(
            json.dumps({"registry_version": "prompt_registry.v1",
                        "simulation": SIMULATION_MARK,
                        "versions": self.versions},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
