"""One package, checked as a whole: the submission format and the folder layout.

A *submission* is the JSON object AIAgentLayer builds when someone presses
Publish (``kind``, ``name``, ``version`` and the kind's own fields). A
package already in this repository is read back into the same shape by
:func:`from_tree`, so a package added by hand and one added from a
submission meet exactly the same rules.

Folder layout read by :func:`from_tree`:

- skill: ``skills/<name>/SKILL.md``; the listing (version, title,
  categories, flavor, compatible_agents) comes from its frontmatter.
- plugin: ``plugins/<name>/plugin.json`` plus optional ``mcp.json``, one
  usage card (``usage_card.md``, ``usage-card.md`` or
  ``<namespace>/usage-card.md``) and bundled ``skills/<skill>/SKILL.md``.
- agent: ``agents/<name>/agent.json`` (the template, nothing else) and
  ``agents/<name>/listing.json`` (``version``, ``title``, ``description``,
  ``categories``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import compat, security
from .rules import (
    EXTENSION_NAMESPACES,
    FORBIDDEN_SKILL_FRONTMATTER,
    MAX_BUNDLED_SKILLS,
    MAX_CATEGORIES,
    MAX_COMPATIBLE_AGENT_CHARS,
    MAX_COMPATIBLE_AGENTS,
    MAX_DESCRIPTION_CHARS,
    MAX_FILE_BYTES,
    MAX_SKILL_MD_BYTES,
    MAX_TITLE_CHARS,
    REQUIRED_SKILL_FRONTMATTER,
    SKILL_FLAVORS,
    FieldError,
    is_semver,
    is_valid_name,
    reserved_reason,
    utf16_len,
    warning,
)

MAX_CATEGORY_CHARS = 40

#: Fields each kind may carry. ``publisher`` and ``publisher_id`` are never
#: taken from a submission (ownership comes from registry.json), so they are
#: accepted only to be reported as ignored.
_COMMON_KEYS = frozenset({"kind", "name", "version", "publisher", "publisher_id"})
ALLOWED_KEYS = {
    "skill": _COMMON_KEYS | {"title", "description", "categories", "skill_md", "flavor", "compatible_agents"},
    "plugin": _COMMON_KEYS | {"plugin_json", "mcp_json", "usage_card", "skills"},
    "agent": _COMMON_KEYS | {"title", "description", "categories", "agent"},
}

#: Where a plugin folder's usage card may live (first match wins).
USAGE_CARD_PATHS = (
    "usage_card.md",
    "usage-card.md",
    *(f"{ns}/usage-card.md" for ns in (*EXTENSION_NAMESPACES, "io.github.ainexlayer")),
)

_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:", re.MULTILINE)


class PackageLayoutError(ValueError):
    """A package folder is missing a file or holds one it should not."""


# ---------------------------------------------------------------------------
# SKILL.md
# ---------------------------------------------------------------------------


def frontmatter_block(skill_md: str) -> str:
    """The raw frontmatter text, found exactly the way the app finds it."""
    if not skill_md.startswith("---"):
        return ""
    _, _, rest = skill_md.partition("---")
    block, sep, _ = rest.partition("\n---")
    return block if sep else ""


def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def parse_frontmatter(skill_md: str) -> dict[str, Any]:
    """Top-level frontmatter values: plain scalars, inline ``[a, b]`` lists,
    ``- item`` block lists and folded ``>`` / ``|`` text. Enough for the
    listing fields; not a general YAML parser."""
    out: dict[str, Any] = {}
    key: str | None = None
    mode = ""
    buffer: list[str] = []

    def flush() -> None:
        if key is None:
            return
        if mode == "list":
            out[key] = buffer[:]
        elif mode == "text":
            out[key] = " ".join(buffer).strip()

    for raw in frontmatter_block(skill_md).splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if key is not None and (line.startswith((" ", "\t"))):
            stripped = line.strip()
            if stripped.startswith("- ") or stripped == "-":
                mode = "list" if mode in ("", "list") else mode
                buffer.append(_scalar(stripped[1:]))
            else:
                mode = "text" if mode in ("", "text") else mode
                buffer.append(stripped)
            continue
        flush()
        key, mode, buffer = None, "", []
        match = _TOP_LEVEL_KEY_RE.match(line)
        if not match:
            continue
        name = match.group(1)
        value = line[match.end() :].strip()
        if value in ("", ">", "|", ">-", "|-"):
            key = name
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            out[name] = [_scalar(p) for p in inner.split(",") if p.strip()] if inner else []
        else:
            out[name] = _scalar(value)
    flush()
    return out


def check_skill_md(name: str, skill_md: Any, *, field: str, plugin_name: str | None = None) -> list[FieldError]:
    """One SKILL.md, as the app checks it before installing it.

    The frontmatter must open the file, declare ``name`` and ``description``
    and never ``risk_policy``. Its ``name`` must match the folder it
    installs into.
    """
    if not isinstance(skill_md, str) or not skill_md.strip():
        return [FieldError(f"skill {name!r}: SKILL.md is empty", field)]
    text = skill_md.lstrip()
    errors: list[FieldError] = []
    if len(text.encode("utf-8")) > MAX_SKILL_MD_BYTES:
        errors.append(FieldError(f"skill {name!r}: SKILL.md is larger than {MAX_SKILL_MD_BYTES // 1024} KB", field))
    block = frontmatter_block(text)
    if not block.strip():
        return errors + [FieldError(f"skill {name!r}: SKILL.md must open with a --- frontmatter block", field)]
    declared = {m.group(1) for m in _TOP_LEVEL_KEY_RE.finditer(block)}
    for key in FORBIDDEN_SKILL_FRONTMATTER:
        if key in declared:
            errors.append(
                FieldError(
                    f"skill {name!r}: {key!r} may not be declared by a marketplace skill (it decides which "
                    "tools run without asking)",
                    field,
                )
            )
    for key in REQUIRED_SKILL_FRONTMATTER:
        if key not in declared:
            errors.append(FieldError(f"skill {name!r}: frontmatter is missing {key!r}", field))
    meta = parse_frontmatter(text)
    declared_name = meta.get("name")
    if isinstance(declared_name, str) and declared_name and declared_name != name:
        errors.append(
            FieldError(f"skill {name!r}: frontmatter name is {declared_name!r}; it must equal {name!r}", field)
        )
    description = meta.get("description")
    if isinstance(description, str) and utf16_len(description) > MAX_DESCRIPTION_CHARS:
        errors.append(
            FieldError(f"skill {name!r}: description is longer than {MAX_DESCRIPTION_CHARS} characters", field)
        )
    errors.extend(_listing_meta_errors(meta, field))
    return errors


def _listing_meta_errors(meta: Mapping[str, Any], field: str) -> list[FieldError]:
    errors: list[FieldError] = []
    flavor = meta.get("flavor")
    if flavor is not None and (not isinstance(flavor, str) or flavor.strip().lower() not in SKILL_FLAVORS):
        errors.append(FieldError(f"flavor must be one of {', '.join(SKILL_FLAVORS)}", field))
    agents = meta.get("compatible_agents")
    if agents is not None:
        if not isinstance(agents, list) or not all(isinstance(a, str) and a.strip() for a in agents):
            errors.append(FieldError("compatible_agents must be a list of agent names", field))
        else:
            if len(agents) > MAX_COMPATIBLE_AGENTS:
                errors.append(FieldError(f"compatible_agents has more than {MAX_COMPATIBLE_AGENTS} entries", field))
            long = [a for a in agents if len(" ".join(a.split())) > MAX_COMPATIBLE_AGENT_CHARS]
            if long:
                errors.append(
                    FieldError(
                        f"compatible_agents entries must be at most {MAX_COMPATIBLE_AGENT_CHARS} characters", field
                    )
                )
    version = meta.get("version")
    if version is not None and not is_semver(version):
        errors.append(FieldError("frontmatter version must be plain SemVer, e.g. 1.0.0", field))
    errors.extend(_category_errors(meta.get("categories"), field))
    return errors


def _category_errors(categories: Any, field: str = "categories") -> list[FieldError]:
    if categories is None:
        return []
    if not isinstance(categories, list) or not all(isinstance(c, str) and c.strip() for c in categories):
        return [FieldError("categories must be a list of words", field)]
    errors: list[FieldError] = []
    if len(categories) > MAX_CATEGORIES:
        errors.append(FieldError(f"at most {MAX_CATEGORIES} categories", field))
    if any(len(c) > MAX_CATEGORY_CHARS for c in categories):
        errors.append(FieldError(f"each category must be at most {MAX_CATEGORY_CHARS} characters", field))
    return errors


def check_bundled_skills(raw: Any, *, plugin_name: str) -> list[FieldError]:
    """A plugin's ``skills: [{name, skill_md}]`` block."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        return [FieldError("skills must be a list of {name, skill_md}", "skills")]
    if len(raw) > MAX_BUNDLED_SKILLS:
        return [FieldError(f"a plugin may bundle at most {MAX_BUNDLED_SKILLS} skills", "skills")]
    errors: list[FieldError] = []
    seen: list[str] = []
    for item in raw:
        if not isinstance(item, Mapping):
            errors.append(FieldError("each bundled skill must be an object {name, skill_md}", "skills"))
            continue
        name = item.get("name")
        if not is_valid_name(name):
            errors.append(FieldError(f"bundled skill name {name!r} must follow the package name rules", "skills"))
            continue
        if name in seen:
            errors.append(FieldError(f"the skill {name!r} is bundled twice", "skills"))
            continue
        seen.append(name)
        reason = reserved_reason("skill", name)
        if reason:
            errors.append(FieldError(reason, "skills"))
        errors.extend(check_skill_md(name, item.get("skill_md"), field="skills", plugin_name=plugin_name))
    if plugin_name in seen and len(seen) > 1:
        errors.append(
            FieldError(
                f"a bundled skill may be named {plugin_name!r} only when it is the plugin's only skill", "skills"
            )
        )
    return errors


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------

_FENCED_JSON_RE = re.compile(r"^```json[ \t]*\r?\n(.*?)\r?\n```[ \t]*$", re.DOTALL | re.MULTILINE)


def extract_submission(text: str) -> Any:
    """The submission in ``text``: a bare JSON object, or the one fenced
    ```` ```json ```` block of an issue body AIAgentLayer opened.

    The text is only ever parsed as JSON, never run. Raises ``ValueError``
    when there is not exactly one object to read.
    """
    stripped = text.strip()
    if stripped.startswith("{"):
        candidate = stripped
    else:
        blocks = _FENCED_JSON_RE.findall(text)
        if len(blocks) != 1:
            raise ValueError(f"expected exactly one ```json block, found {len(blocks)}")
        candidate = blocks[0]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"the submission is not valid JSON ({exc})") from exc
    if not isinstance(obj, dict):
        raise ValueError("the submission must be a JSON object")  # noqa: TRY004 - callers handle ValueError for every bad submission
    return obj


def validate(obj: Any) -> list[FieldError]:
    """Every problem with one submission. An empty list means it passes.

    Warnings (``severity == "warning"``) are included; they do not refuse
    the package.
    """
    if not isinstance(obj, Mapping):
        return [FieldError("a submission is a JSON object", None)]
    kind = obj.get("kind")
    if kind not in ALLOWED_KEYS:
        return [FieldError('kind must be "plugin", "skill" or "agent"', "kind")]
    errors: list[FieldError] = []
    for key in obj:
        if key not in ALLOWED_KEYS[kind]:
            errors.append(FieldError(f"{key!r} is not a {kind} submission field", key))
    for key in ("publisher", "publisher_id"):
        if key in obj:
            errors.append(warning(f"{key} is ignored; ownership comes from registry.json", key))

    name = obj.get("name")
    if not is_valid_name(name):
        errors.append(
            FieldError("name must be 1-64 chars of a-z 0-9 - . (no leading/trailing separators, no .. or --)", "name")
        )
        name = None
    else:
        reason = reserved_reason(kind, name)
        if reason:
            errors.append(FieldError(reason, "name"))
    if not is_semver(obj.get("version")):
        errors.append(FieldError("version must be plain SemVer, e.g. 1.0.0", "version"))

    if kind == "skill":
        errors.extend(_check_skill(obj, name))
    elif kind == "plugin":
        errors.extend(_check_plugin(obj, name))
    else:
        errors.extend(_check_agent(obj))

    serialized = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    if len(serialized.encode("utf-8")) > MAX_FILE_BYTES:
        errors.append(FieldError(f"the submission is larger than {MAX_FILE_BYTES} bytes", None))
    errors.extend(security.scan_text(serialized, "submission", strict=kind == "agent"))
    return errors


def _title_and_description(obj: Mapping[str, Any], kind: str) -> list[FieldError]:
    errors: list[FieldError] = []
    title = obj.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(FieldError(f"title is required for a {kind}", "title"))
    elif len(title) > MAX_TITLE_CHARS:
        errors.append(FieldError(f"title must be at most {MAX_TITLE_CHARS} characters", "title"))
    description = obj.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(FieldError(f"description is required for a {kind}", "description"))
    elif utf16_len(description) > MAX_DESCRIPTION_CHARS:
        errors.append(FieldError(f"description is longer than {MAX_DESCRIPTION_CHARS} characters", "description"))
    errors.extend(_category_errors(obj.get("categories")))
    return errors


def _check_skill(obj: Mapping[str, Any], name: str | None) -> list[FieldError]:
    errors = _title_and_description(obj, "skill")
    errors.extend(_listing_meta_errors({k: obj[k] for k in ("flavor", "compatible_agents") if k in obj}, "skill_md"))
    skill_md = obj.get("skill_md")
    if not isinstance(skill_md, str) or not skill_md.strip():
        errors.append(FieldError("skill_md is required for a skill", "skill_md"))
    elif name:
        errors.extend(check_skill_md(name, skill_md, field="skill_md"))
        meta = parse_frontmatter(skill_md.lstrip())
        fm_version = meta.get("version")
        if isinstance(fm_version, str) and is_semver(obj.get("version")) and fm_version != obj["version"]:
            errors.append(
                FieldError(
                    f"frontmatter version {fm_version} differs from the submission version {obj['version']}", "skill_md"
                )
            )
    return errors


def _check_plugin(obj: Mapping[str, Any], name: str | None) -> list[FieldError]:
    plugin_json = obj.get("plugin_json")
    errors = compat.check_plugin_manifest(plugin_json, expected_name=name)
    if isinstance(plugin_json, Mapping):
        manifest_version = plugin_json.get("version")
        if isinstance(manifest_version, str) and is_semver(obj.get("version")) and manifest_version != obj["version"]:
            errors.append(
                FieldError(
                    f"plugin_json.version {manifest_version} differs from the submission version {obj['version']}",
                    "plugin_json",
                )
            )
    mcp_json = obj.get("mcp_json")
    if mcp_json is not None:
        errors.extend(compat.check_mcp(mcp_json, plugin_json))
    usage_card = obj.get("usage_card")
    if usage_card is not None and not isinstance(usage_card, str):
        errors.append(FieldError("usage_card must be Markdown text", "usage_card"))
    if name:
        errors.extend(check_bundled_skills(obj.get("skills"), plugin_name=name))
    return errors


def _check_agent(obj: Mapping[str, Any]) -> list[FieldError]:
    errors = _title_and_description(obj, "agent")
    # The app checks the summary length together with the template; the
    # description errors above already cover it, so pass no listing here.
    errors.extend(compat.check_agent_template(obj.get("agent")))
    return errors


# ---------------------------------------------------------------------------
# Folders in this repository
# ---------------------------------------------------------------------------

_KIND_DIRS = {"skill": "skills", "plugin": "plugins", "agent": "agents"}


def package_dir(root: Path, kind: str, name: str) -> Path:
    return root / _KIND_DIRS[kind] / name


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageLayoutError(f"{path.name}: not valid JSON ({exc})") from exc


def _title_from_name(name: str) -> str:
    return name.replace("-", " ").title()


def from_tree(root: Path, kind: str, name: str) -> dict[str, Any]:
    """Read ``<kind dir>/<name>/`` back into a submission object.

    Raises :class:`PackageLayoutError` when a required file is missing or
    an unexpected file is present. Run :func:`security.check_tree` on the
    folder first; this function assumes no symlinks or oversized files.
    """
    folder = package_dir(root, kind, name)
    if not folder.is_dir():
        raise PackageLayoutError(f"{folder} is not a folder")
    files = sorted(
        p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and not security.is_ignored(p)
    )

    if kind == "skill":
        unexpected = [f for f in files if f != "SKILL.md"]
        if unexpected:
            raise PackageLayoutError(f"a skill folder holds only SKILL.md (found {', '.join(unexpected)})")
        if "SKILL.md" not in files:
            raise PackageLayoutError("missing SKILL.md")
        skill_md = (folder / "SKILL.md").read_text(encoding="utf-8")
        meta = parse_frontmatter(skill_md.lstrip())
        doc: dict[str, Any] = {
            "kind": "skill",
            "name": name,
            "version": meta.get("version", "1.0.0"),
            "title": meta.get("title") or _title_from_name(name),
            "description": meta.get("description", ""),
            "categories": meta.get("categories", []),
            "skill_md": skill_md,
        }
        for key in ("flavor", "compatible_agents"):
            if key in meta:
                doc[key] = meta[key]
        return doc

    if kind == "plugin":
        allowed = {"plugin.json", "mcp.json", *USAGE_CARD_PATHS}
        unexpected = [f for f in files if f not in allowed and not re.fullmatch(r"skills/[^/]+/SKILL\.md", f)]
        if unexpected:
            raise PackageLayoutError(f"unexpected files in a plugin folder: {', '.join(unexpected)}")
        if "plugin.json" not in files:
            raise PackageLayoutError("missing plugin.json")
        cards = [p for p in USAGE_CARD_PATHS if p in files]
        if len(cards) > 1:
            raise PackageLayoutError(f"more than one usage card: {', '.join(cards)}")
        plugin_json = _read_json(folder / "plugin.json")
        doc = {
            "kind": "plugin",
            "name": name,
            "version": plugin_json.get("version", "1.0.0") if isinstance(plugin_json, dict) else "1.0.0",
            "plugin_json": plugin_json,
        }
        if "mcp.json" in files:
            doc["mcp_json"] = _read_json(folder / "mcp.json")
        if cards:
            doc["usage_card"] = (folder / cards[0]).read_text(encoding="utf-8")
        bundled = sorted(f.split("/")[1] for f in files if f.startswith("skills/"))
        if bundled:
            doc["skills"] = [
                {"name": skill, "skill_md": (folder / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")}
                for skill in bundled
            ]
        return doc

    if kind == "agent":
        unexpected = [f for f in files if f not in ("agent.json", "listing.json")]
        if unexpected:
            raise PackageLayoutError(
                f"an agent folder holds only agent.json and listing.json (found {', '.join(unexpected)})"
            )
        for required in ("agent.json", "listing.json"):
            if required not in files:
                raise PackageLayoutError(f"missing {required}")
        listing = _read_json(folder / "listing.json")
        if not isinstance(listing, dict):
            raise PackageLayoutError("listing.json must be an object")
        extra = sorted(set(listing) - {"version", "title", "description", "categories"})
        if extra:
            raise PackageLayoutError(
                f"listing.json may hold only version, title, description, categories (found {extra})"
            )
        return {
            "kind": "agent",
            "name": name,
            "version": listing.get("version", ""),
            "title": listing.get("title", ""),
            "description": listing.get("description", ""),
            "categories": listing.get("categories", []),
            "agent": _read_json(folder / "agent.json"),
        }

    raise PackageLayoutError(f"unknown kind {kind!r}")


def check_package(root: Path, kind: str, name: str) -> list[FieldError]:
    """Folder safety, layout and every submission rule for one package."""
    where = f"{_KIND_DIRS[kind]}/{name}"
    if not is_valid_name(name):
        return [FieldError(f"{where}: folder name must follow the package name rules", where)]
    folder = package_dir(root, kind, name)
    errors = security.check_tree(folder, where=where)
    if any(e.is_error for e in errors):
        return errors
    try:
        doc = from_tree(root, kind, name)
    except PackageLayoutError as exc:
        return errors + [FieldError(f"{where}: {exc}", where)]
    for problem in validate(doc):
        errors.append(FieldError(f"{where}: {problem}", problem.field, problem.severity))
    return errors
