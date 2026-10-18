"""The sole case-input whitelist, shared by validation and the future harness."""
from __future__ import annotations

import json
from pathlib import Path


def visible_files(case: Path) -> list[Path]:
    case = case.resolve(strict=True)
    task, artifacts = case / "task.md", case / "artifacts"
    if task.is_symlink() or artifacts.is_symlink() or not task.is_file() or not artifacts.is_dir():
        raise ValueError("case must contain a regular task and artifacts directory")
    paths = [task]
    for p in sorted(artifacts.rglob("*")):
        if p.is_symlink():
            raise ValueError("model input cannot contain symlinks")
        if p.is_file():
            if artifacts not in p.resolve().parents:
                raise ValueError("artifact leaves input whitelist")
            if p.name.lower() in {"meta.json", "expected.json"}:
                raise ValueError("private filename inside artifacts")
            paths.append(p)
    return paths


def packet(case: Path) -> dict:
    case = case.resolve(strict=True)
    paths = visible_files(case)
    artifacts = []
    for p in paths[1:]:
        data = p.read_bytes()
        if p.suffix == ".gz":
            # Text-only v1 uses the separately recorded actual reader diagnostic.
            encoding, content = "binary-summary", f"gzip 文件，字节数：{len(data)}；读取诊断见 integrity.json。"
        else:
            encoding, content = "utf-8", data.decode("utf-8")
        artifacts.append({"filename": p.relative_to(case).as_posix(), "encoding": encoding, "content": content})
    return {"task": paths[0].read_text(encoding="utf-8"), "artifacts": artifacts}


def serialize(case: Path) -> str:
    return json.dumps(packet(case), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def estimated_tokens(case: Path) -> int:
    # Conservative byte-token upper bound, including base64 and packet framing.
    return len(serialize(case).encode("utf-8"))
