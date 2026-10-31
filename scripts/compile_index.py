#!/usr/bin/env python3
"""Compile plugins/, skills/, agents/ into docs/index.json, the feed
AIAgentLayer reads straight from the main branch.

The shape matches AIAgentLayer's CommunityIndex model (its marketplace
community source). Run from the registry repo root:

    python3 scripts/compile_index.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPO = "revanth-ainl/ainexlayer-marketplace"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
TREE_BASE = f"https://github.com/{REPO}/tree/{BRANCH}"

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_frontmatter(text: str) -> dict[str, Any]:
    match = _FM_RE.match(text)
    if not match:
        return {}
    block = match.group(1)
    out: dict[str, Any] = {}
    key: str | None = None
    folded: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if key and (line.startswith("  ") or line.startswith("\t")):
            folded.append(line.strip())
            continue
        if key and folded:
            out[key] = " ".join(folded).strip()
            folded = []
            key = None
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if v in (">", "|", ">-", "|-"):
            key = k
            folded = []
            continue
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            out[k] = [p.strip().strip('"').strip("'") for p in inner.split(",") if p.strip()] if inner else []
            continue
        out[k] = v
    if key and folded:
        out[key] = " ".join(folded).strip()
    return out


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warn: skip {path}: {exc}", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def _owners() -> dict[str, Any]:
    path = ROOT / "registry.json"
    if not path.exists():
        return {}
    data = _load_json(path) or {}
    owners = data.get("owners")
    return owners if isinstance(owners, dict) else {}


def _publisher_for(name: str, kind: str) -> str:
    owners = _owners()
    entry = owners.get(name)
    if isinstance(entry, dict):
        pub = entry.get("publisher")
        if isinstance(pub, str) and pub.strip():
            return pub.strip()
    return "ainexlayer"


def compile_skills() -> list[dict[str, Any]]:
    skills_dir = ROOT / "skills"
    entries: list[dict[str, Any]] = []
    if not skills_dir.is_dir():
        return entries
    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        text = skill_md.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        name = str(fm.get("name") or skill_dir.name).strip().lower()
        if not _NAME_RE.fullmatch(name):
            print(f"warn: unsafe skill name {name!r}, skip", file=sys.stderr)
            continue
        description = str(fm.get("description") or "").strip()
        version = str(fm.get("version") or "1.0.0").strip()
        flavor = str(fm.get("flavor") or "portable").strip().lower()
        if flavor not in ("jarvis", "portable"):
            flavor = "portable"
        title = str(fm.get("title") or name.replace("-", " ").title()).strip()
        categories = fm.get("categories") if isinstance(fm.get("categories"), list) else []
        compatible = fm.get("compatible_agents") if isinstance(fm.get("compatible_agents"), list) else []
        rel = f"skills/{skill_dir.name}"
        entries.append(
            {
                "name": name,
                "title": title,
                "description": description,
                "publisher": _publisher_for(name, "skill"),
                "version": version,
                "published_at": _utc_now(),
                "categories": categories,
                "source_url": f"{TREE_BASE}/{rel}",
                "raw_url": f"{RAW_BASE}/{rel}/SKILL.md",
                "flavor": flavor,
                "compatible_agents": compatible,
            }
        )
    return entries


def compile_plugins() -> list[dict[str, Any]]:
    plugins_dir = ROOT / "plugins"
    entries: list[dict[str, Any]] = []
    if not plugins_dir.is_dir():
        return entries
    for plugin_dir in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
        plugin_json_path = plugin_dir / "plugin.json"
        if not plugin_json_path.is_file():
            continue
        plugin_json = _load_json(plugin_json_path)
        if not plugin_json:
            continue
        name = str(plugin_json.get("name") or plugin_dir.name).strip().lower()
        if not _NAME_RE.fullmatch(name):
            print(f"warn: unsafe plugin name {name!r}, skip", file=sys.stderr)
            continue
        mcp_path = plugin_dir / "mcp.json"
        mcp_json = _load_json(mcp_path) if mcp_path.is_file() else None
        usage = None
        for candidate in (
            plugin_dir / "usage_card.md",
            plugin_dir / "usage-card.md",
            plugin_dir / "io.github.ainexlayer" / "usage-card.md",
        ):
            if candidate.is_file():
                usage = candidate.read_text(encoding="utf-8")
                break
        version = str(plugin_json.get("version") or "1.0.0").strip()
        rel = f"plugins/{plugin_dir.name}"
        entries.append(
            {
                "name": name,
                "publisher": _publisher_for(name, "plugin"),
                "version": version,
                "published_at": _utc_now(),
                "source_url": f"{TREE_BASE}/{rel}",
                "plugin_json": plugin_json,
                "mcp_json": mcp_json,
                "usage_card": usage,
            }
        )
    return entries


def compile_agents() -> list[dict[str, Any]]:
    agents_dir = ROOT / "agents"
    entries: list[dict[str, Any]] = []
    if not agents_dir.is_dir():
        return entries
    for agent_dir in sorted(p for p in agents_dir.iterdir() if p.is_dir()):
        agent_path = agent_dir / "agent.json"
        if not agent_path.is_file():
            continue
        agent = _load_json(agent_path)
        if not agent:
            continue
        name = str(agent.get("name") or agent_dir.name).strip().lower()
        if not _NAME_RE.fullmatch(name):
            print(f"warn: unsafe agent name {name!r}, skip", file=sys.stderr)
            continue
        rel = f"agents/{agent_dir.name}"
        entries.append(
            {
                "name": name,
                "title": str(agent.get("title") or name.replace("-", " ").title()),
                "description": str(agent.get("description") or ""),
                "publisher": _publisher_for(name, "agent"),
                "version": str(agent.get("version") or "1.0.0"),
                "published_at": _utc_now(),
                "categories": agent.get("categories") if isinstance(agent.get("categories"), list) else [],
                "source_url": f"{TREE_BASE}/{rel}",
                "agent": agent,
            }
        )
    return entries


def _content_key(entry: dict[str, Any]) -> str:
    return f"{entry.get('name')}:{entry.get('version')}"


def _strip_volatile(index: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that change every compile without a package change."""
    def clean_list(items: list[Any]) -> list[Any]:
        cleaned = []
        for item in items:
            if not isinstance(item, dict):
                cleaned.append(item)
                continue
            copy = {k: v for k, v in item.items() if k != "published_at"}
            cleaned.append(copy)
        return cleaned

    return {
        "plugins": clean_list(list(index.get("plugins") or [])),
        "skills": clean_list(list(index.get("skills") or [])),
        "agents": clean_list(list(index.get("agents") or [])),
    }


def _preserve_published_at(
    fresh: list[dict[str, Any]], previous: list[dict[str, Any]]
) -> None:
    prior = {
        _content_key(item): item.get("published_at")
        for item in previous
        if isinstance(item, dict)
    }
    for item in fresh:
        key = _content_key(item)
        if key in prior and prior[key]:
            item["published_at"] = prior[key]


def main() -> int:
    plugins = compile_plugins()
    skills = compile_skills()
    agents = compile_agents()

    out_dir = ROOT / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.json"
    previous: dict[str, Any] = {}
    if out_path.is_file():
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, json.JSONDecodeError):
            previous = {}

    _preserve_published_at(plugins, list(previous.get("plugins") or []))
    _preserve_published_at(skills, list(previous.get("skills") or []))
    _preserve_published_at(agents, list(previous.get("agents") or []))

    index: dict[str, Any] = {
        "revision": 1,
        "generated_at": _utc_now(),
        "plugins": plugins,
        "skills": skills,
        "agents": agents,
    }
    fingerprint = json.dumps(_strip_volatile(index), sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    index["revision"] = (int(digest[:8], 16) % 10_000_000) or 1

    prior_fp = json.dumps(_strip_volatile(previous), sort_keys=True, ensure_ascii=False) if previous else ""
    if prior_fp == fingerprint and previous.get("generated_at"):
        index["generated_at"] = previous["generated_at"]
        if isinstance(previous.get("revision"), int):
            index["revision"] = previous["revision"]

    out_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {out_path.relative_to(ROOT)} "
        f"(plugins={len(index['plugins'])} skills={len(index['skills'])} "
        f"agents={len(index['agents'])} revision={index['revision']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
