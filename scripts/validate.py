#!/usr/bin/env python3
"""Basic registry validation before compile / merge."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"
)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def check_skills() -> None:
    skills = ROOT / "skills"
    if not skills.is_dir():
        return
    for d in skills.iterdir():
        if not d.is_dir():
            continue
        md = d / "SKILL.md"
        if not md.is_file():
            fail(f"skill {d.name}: missing SKILL.md")
        if not _NAME_RE.fullmatch(d.name):
            fail(f"skill dir name unsafe: {d.name}")
        text = md.read_text(encoding="utf-8")
        if _SECRET_RE.search(text):
            fail(f"skill {d.name}: looks like a secret in SKILL.md")
        if len(text.encode("utf-8")) > 200_000:
            fail(f"skill {d.name}: SKILL.md too large")


def check_plugins() -> None:
    plugins = ROOT / "plugins"
    if not plugins.is_dir():
        return
    for d in plugins.iterdir():
        if not d.is_dir():
            continue
        pj = d / "plugin.json"
        if not pj.is_file():
            fail(f"plugin {d.name}: missing plugin.json")
        if not _NAME_RE.fullmatch(d.name):
            fail(f"plugin dir name unsafe: {d.name}")
        data = json.loads(pj.read_text(encoding="utf-8"))
        name = str(data.get("name") or "")
        if name and not _NAME_RE.fullmatch(name):
            fail(f"plugin {d.name}: unsafe name {name!r}")
        for path in d.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".json", ".txt", ".yml", ".yaml"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                if _SECRET_RE.search(text):
                    fail(f"plugin {d.name}: possible secret in {path.relative_to(ROOT)}")


def main() -> int:
    check_skills()
    check_plugins()
    print("validate: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
