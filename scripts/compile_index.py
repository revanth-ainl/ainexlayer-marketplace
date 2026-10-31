#!/usr/bin/env python3
"""Compile plugins/, skills/, agents/ into docs/index.json, the feed
AIAgentLayer reads from the raw GitHub URL of the main branch.

The shape is AIAgentLayer's ``CommunityIndex`` (dodaios/marketplace/
community_source.py). Every field it reads keeps its name, value and URL
format; the fields added here (``publisher_id``, ``trust``, hashes,
``transport``, ``bundled_skills``, ``schema``) are extra metadata the app's
tolerant model keeps without acting on.

Run by hand from the repository root:

    python3 scripts/compile_index.py           # write docs/index.json if anything changed
    python3 scripts/compile_index.py --check   # exit 1 if docs/index.json is out of date

Every package is validated first (scripts/validate.py rules); one invalid
package stops the compile instead of being skipped. The output depends only
on the files: entries sorted by name, ``published_at`` kept for a version
already in the feed, and ``revision`` / ``generated_at`` changed only when
the content changes (``revision`` then goes up by one). New timestamps use
``SOURCE_DATE_EPOCH`` when it is set, otherwise the current UTC time.
Nothing here touches the network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate
from ainexlayer_marketplace import registry, submission
from ainexlayer_marketplace.hashing import content_sha256, file_sha256
from ainexlayer_marketplace.rules import DEFAULT_SKILL_FLAVOR, FieldError

REPO = "revanth-ainl/ainexlayer-marketplace"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
TREE_BASE = f"https://github.com/{REPO}/tree/{BRANCH}"
FEED_PATH = Path("docs") / "index.json"
SECTIONS = {"plugin": "plugins", "skill": "skills", "agent": "agents"}

__all__ = ["CompileError", "build_index", "content_sha256", "main", "render"]


class CompileError(Exception):
    """The registry has problems; ``errors`` lists them."""

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__(f"{len(errors)} problem(s)")
        self.errors = errors


def _timestamp(now: str | None) -> str:
    if now:
        return now
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    moment = datetime.fromtimestamp(int(epoch), timezone.utc) if epoch.isdigit() else datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ownership(owners: dict[str, Any], name: str, trusted: dict[int, Any]) -> tuple[str, dict[str, Any]]:
    owner = owners.get(name) if isinstance(owners.get(name), dict) else {}
    publisher = owner.get("publisher") if isinstance(owner.get("publisher"), str) else ""
    extra: dict[str, Any] = {}
    if registry.is_publisher_id(owner.get("publisher_id")):
        extra["publisher_id"] = owner["publisher_id"]
    extra["trust"] = registry.trust_for(owner.get("publisher_id"), trusted)
    return publisher, extra


def _skill_entry(root: Path, doc: dict[str, Any], publisher: str, extra: dict[str, Any]) -> dict[str, Any]:
    rel = f"skills/{doc['name']}"
    folder = root / rel
    categories = doc.get("categories")
    compatible = doc.get("compatible_agents")
    return {
        "name": doc["name"],
        "title": doc["title"],
        "description": doc["description"],
        "publisher": publisher,
        "version": doc["version"],
        "published_at": None,
        "categories": categories if isinstance(categories, list) else [],
        "source_url": f"{TREE_BASE}/{rel}",
        "raw_url": f"{RAW_BASE}/{rel}/SKILL.md",
        "flavor": doc.get("flavor") or DEFAULT_SKILL_FLAVOR,
        "compatible_agents": compatible if isinstance(compatible, list) else [],
        **extra,
        "content_sha256": content_sha256(folder),
        "raw_sha256": file_sha256(folder / "SKILL.md"),
    }


def _transport(mcp_json: Any) -> str | None:
    if isinstance(mcp_json, dict) and isinstance(mcp_json.get("mcpServers"), dict):
        for server in mcp_json["mcpServers"].values():
            if isinstance(server, dict) and isinstance(server.get("type"), str):
                return server["type"]
    return None


def _plugin_entry(root: Path, doc: dict[str, Any], publisher: str, extra: dict[str, Any]) -> dict[str, Any]:
    rel = f"plugins/{doc['name']}"
    folder = root / rel
    bundled = []
    for skill in doc.get("skills") or []:
        meta = submission.parse_frontmatter(skill["skill_md"].lstrip())
        bundled.append(
            {
                "name": skill["name"],
                "description": meta.get("description", ""),
                "sha256": file_sha256(folder / "skills" / skill["name"] / "SKILL.md"),
            }
        )
    return {
        "name": doc["name"],
        "publisher": publisher,
        "version": doc["version"],
        "published_at": None,
        "source_url": f"{TREE_BASE}/{rel}",
        "plugin_json": doc["plugin_json"],
        "mcp_json": doc.get("mcp_json"),
        "usage_card": doc.get("usage_card"),
        **extra,
        "content_sha256": content_sha256(folder),
        "transport": _transport(doc.get("mcp_json")),
        "bundled_skills": bundled,
    }


def _agent_entry(root: Path, doc: dict[str, Any], publisher: str, extra: dict[str, Any]) -> dict[str, Any]:
    rel = f"agents/{doc['name']}"
    agent = doc["agent"]
    categories = doc.get("categories")
    return {
        "name": doc["name"],
        "title": doc["title"],
        "description": doc["description"],
        "publisher": publisher,
        "version": doc["version"],
        "published_at": None,
        "categories": categories if isinstance(categories, list) else [],
        "source_url": f"{TREE_BASE}/{rel}",
        "agent": agent,
        **extra,
        "content_sha256": content_sha256(root / rel),
        "schema": agent.get("schema") if isinstance(agent, dict) else None,
    }


_BUILDERS = {"skill": _skill_entry, "plugin": _plugin_entry, "agent": _agent_entry}


def _without_volatile(index: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in index.items() if k not in ("revision", "generated_at")}


def build_index(root: Path, previous: dict[str, Any] | None = None, *, now: str | None = None) -> dict[str, Any]:
    """The feed for the registry checkout at ``root``.

    ``previous`` is the feed currently on disk (or ``None``); it supplies
    ``published_at`` for versions already listed and the ``revision`` /
    ``generated_at`` pair when nothing changed. Raises :class:`CompileError`
    when any package or registry file fails validation.
    """
    root = Path(root)
    problems = [e for e in validate.validate_tree(root) if e.is_error]
    if problems:
        raise CompileError(problems)
    previous = previous if isinstance(previous, dict) else {}
    stamp = _timestamp(now)
    reg = registry.load(root / "registry.json")
    owners = reg.get("owners", {})
    trusted = registry.load_trusted(root / "trusted-publishers.json")

    sections: dict[str, list[dict[str, Any]]] = {section: [] for section in SECTIONS.values()}
    for kind, name in sorted(validate.find_packages(root), key=lambda item: (item[0], item[1])):
        doc = submission.from_tree(root, kind, name)
        publisher, extra = _ownership(owners, name, trusted)
        entry = _BUILDERS[kind](root, doc, publisher, extra)
        prior = {
            (e.get("name"), e.get("version")): e.get("published_at")
            for e in previous.get(SECTIONS[kind]) or []
            if isinstance(e, dict)
        }
        entry["published_at"] = prior.get((entry["name"], entry["version"])) or stamp
        sections[SECTIONS[kind]].append(entry)
    for entries in sections.values():
        entries.sort(key=lambda e: e["name"])

    index: dict[str, Any] = {"revision": 0, "generated_at": None, **sections}
    old_revision = previous.get("revision")
    old_revision = old_revision if isinstance(old_revision, int) and not isinstance(old_revision, bool) else 0
    if previous and _without_volatile(previous) == _without_volatile(index) and previous.get("generated_at"):
        index["revision"] = old_revision
        index["generated_at"] = previous["generated_at"]
    else:
        index["revision"] = old_revision + 1
        index["generated_at"] = stamp
    return index


def render(index: dict[str, Any]) -> str:
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def _read_previous(path: Path) -> tuple[dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}, ""
    return (data if isinstance(data, dict) else {}), text


def _changed_entries(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    lines = []
    for section in SECTIONS.values():
        before = {e.get("name"): e for e in old.get(section) or [] if isinstance(e, dict)}
        after = {e.get("name"): e for e in new.get(section) or [] if isinstance(e, dict)}
        for name in sorted(set(before) | set(after), key=str):
            if name not in after:
                lines.append(f"  removed {section}/{name}")
            elif name not in before:
                lines.append(f"  added   {section}/{name} {after[name].get('version')}")
            elif before[name] != after[name]:
                lines.append(f"  changed {section}/{name} {after[name].get('version')}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="registry checkout (default: this repository)")
    parser.add_argument("--output", type=Path, help="feed file (default: <root>/docs/index.json)")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if the feed is out of date")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    out_path = args.output or root / FEED_PATH
    previous, previous_text = _read_previous(out_path)
    try:
        index = build_index(root, previous)
    except CompileError as exc:
        for problem in exc.errors:
            print(f"FAIL: {problem.message}", file=sys.stderr)
        print("compile_index: not compiled; fix the problems above (python3 scripts/validate.py)", file=sys.stderr)
        return 1
    text = render(index)
    counts = " ".join(f"{section}={len(index[section])}" for section in SECTIONS.values())
    shown = out_path.relative_to(root) if out_path.is_relative_to(root) else out_path
    if args.check:
        if text == previous_text:
            print(f"compile_index: {shown} is up to date ({counts} revision={index['revision']})")
            return 0
        print(f"compile_index: {shown} is out of date; run python3 scripts/compile_index.py", file=sys.stderr)
        for line in _changed_entries(previous, index) or ["  formatting or top-level fields differ"]:
            print(line, file=sys.stderr)
        return 1
    if text == previous_text:
        print(f"compile_index: {shown} unchanged ({counts} revision={index['revision']})")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out_path)
    print(f"compile_index: wrote {shown} ({counts} revision={index['revision']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
