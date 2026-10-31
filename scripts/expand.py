#!/usr/bin/env python3
"""Turn one reviewed submission into package files and a registry claim, by hand.

A maintainer runs this after reading a submission: a JSON file, or the body
of the ``[publish]`` issue AIAgentLayer opened (its one fenced json block is
read as data, never run). The publisher's GitHub login and numeric account
id come from the command line, from the issue's author as the maintainer
checked it, never from the submission itself.

    python3 scripts/expand.py issue.md --publisher LOGIN --publisher-id 123456          # dry run
    python3 scripts/expand.py issue.md --publisher LOGIN --publisher-id 123456 --write  # write files

Without ``--write`` nothing is written: the planned files, their hashes and
the registry change are printed. Everything is checked before anything is
written: the submission rules, the ownership claim, and the package as it
will lie on disk (built in a temporary folder and read back). Running it
again with the same input changes nothing. Then run
``python3 scripts/compile_index.py`` and ``python3 scripts/validate.py``.

A skill's listing (version, title, categories, flavor, compatible_agents)
lives in its SKILL.md frontmatter, which is what the feed reads. AIAgentLayer
sends these as separate fields, so they are written into the frontmatter;
every other byte of the SKILL.md stays as submitted. The frontmatter
description is kept as written (it tells an assistant when to use the
skill); a different submission description is reported as a warning.

Standard library only. No network, no subprocess, no submitted code is run.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ainexlayer_marketplace import hashing, registry, security, submission
from ainexlayer_marketplace.rules import MAX_FILE_BYTES, FieldError, is_valid_name, warning

#: An issue body carries the submission plus the app's own text around it.
MAX_INPUT_BYTES = 4 * MAX_FILE_BYTES
KIND_DIRS = {"skill": "skills", "plugin": "plugins", "agent": "agents"}
#: Listing keys written into a skill's frontmatter, in this order.
SKILL_LISTING_KEYS = ("version", "title", "categories", "flavor", "compatible_agents")
_PLAIN_SCALAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()/+-]*$")
_TOP_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:")


class ExpandError(Exception):
    """The submission cannot be expanded; ``errors`` says why."""

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__(f"{len(errors)} problem(s)")
        self.errors = errors


# ---------------------------------------------------------------------------
# SKILL.md frontmatter
# ---------------------------------------------------------------------------


def _yaml_scalar(value: str, field: str) -> str:
    if _PLAIN_SCALAR_RE.fullmatch(value) and not value.endswith(" "):
        return value
    if "\n" in value or "\r" in value:
        raise ExpandError([FieldError(f"{field} must be one line", field)])
    if '"' not in value and "\\" not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    raise ExpandError([FieldError(f"{field} cannot hold both kinds of quote mark", field)])


def _yaml_value(key: str, value: Any) -> str:
    if isinstance(value, list):
        items = []
        for item in value:
            if not isinstance(item, str) or "," in item:
                raise ExpandError([FieldError(f"{key} entries must be text without commas", key)])
            items.append(_yaml_scalar(item, key))
        return "[" + ", ".join(items) + "]"
    if not isinstance(value, str):
        raise ExpandError([FieldError(f"{key} must be text", key)])
    return _yaml_scalar(value, key)


def set_frontmatter(skill_md: str, values: dict[str, Any]) -> str:
    """``skill_md`` with each of ``values`` set as a top-level frontmatter key.

    An existing key (and any indented lines continuing it) is replaced in
    place; a missing one is added before the closing ``---``. Nothing else
    changes. A SKILL.md without frontmatter is returned unchanged, for the
    validation to report.
    """
    if not skill_md.startswith("---"):
        return skill_md
    lines = skill_md.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].startswith("---")), None)
    if close is None:
        return skill_md
    head, body, tail = lines[:1], lines[1:close], lines[close:]
    pending = dict(values)
    out: list[str] = []
    skipping = False
    for line in body:
        match = _TOP_KEY_RE.match(line)
        if skipping and (line.startswith((" ", "\t")) or not line.strip()) and not match:
            continue
        skipping = False
        if match and match.group(1) in pending:
            key = match.group(1)
            out.append(f"{key}: {_yaml_value(key, pending.pop(key))}")
            skipping = True
            continue
        out.append(line)
    for key, value in pending.items():
        out.append(f"{key}: {_yaml_value(key, value)}")
    return "\n".join(head + out + tail)


def normalize(obj: dict[str, Any]) -> tuple[dict[str, Any], list[FieldError]]:
    """The submission as it will be stored, plus warnings about what changed.

    ``publisher`` / ``publisher_id`` are dropped (ownership comes from the
    command line). A skill's listing fields go into its frontmatter.
    """
    doc = {k: copy.deepcopy(v) for k, v in obj.items() if k not in ("publisher", "publisher_id")}
    notes: list[FieldError] = []
    if doc.get("kind") == "skill" and isinstance(doc.get("skill_md"), str):
        values = {k: doc[k] for k in SKILL_LISTING_KEYS if k in doc and doc[k] is not None}
        before = submission.parse_frontmatter(doc["skill_md"].lstrip())
        doc["skill_md"] = set_frontmatter(doc["skill_md"], values)
        changed = [k for k in values if before.get(k) != values[k]]
        if changed:
            notes.append(warning(f"wrote {', '.join(changed)} into the SKILL.md frontmatter", "skill_md"))
        fm_description = before.get("description")
        if isinstance(fm_description, str) and fm_description and fm_description != doc.get("description"):
            notes.append(
                warning(
                    "the listing uses the SKILL.md frontmatter description; the submission's description differs",
                    "description",
                )
            )
            doc["description"] = fm_description
    if doc.get("kind") == "plugin" and isinstance(doc.get("skills"), list):
        doc["skills"] = sorted(doc["skills"], key=lambda s: str(s.get("name")) if isinstance(s, dict) else "")
    return doc, notes


def check_submission(obj: Any) -> tuple[dict[str, Any], list[FieldError], list[FieldError]]:
    """``(stored document, notes, problems)`` for one submission.

    The stored document is checked, and so is the description as submitted:
    the listing shows the frontmatter description, but a submission whose own
    description breaks the rules is refused, as AIAgentLayer refuses it.
    """
    if not isinstance(obj, dict):
        return {}, [], [FieldError("a submission is a JSON object", None)]
    doc, notes = normalize(obj)
    problems = submission.validate(doc)
    if doc.get("kind") == "skill" and doc.get("description") != obj.get("description"):
        seen = {(p.message, p.field) for p in problems}
        as_sent = submission.validate({**doc, "description": obj.get("description")})
        problems += [p for p in as_sent if p.field == "description" and (p.message, p.field) not in seen]
    return doc, notes, problems


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def package_files(doc: dict[str, Any]) -> dict[str, bytes]:
    """Relative path -> bytes for the package folder; the reverse of ``submission.from_tree``."""
    kind = doc["kind"]
    files: dict[str, str] = {}
    if kind == "skill":
        files["SKILL.md"] = doc["skill_md"]
    elif kind == "plugin":
        files["plugin.json"] = _json_text(doc["plugin_json"])
        if doc.get("mcp_json") is not None:
            files["mcp.json"] = _json_text(doc["mcp_json"])
        if doc.get("usage_card") is not None:
            files["usage_card.md"] = doc["usage_card"]
        for skill in doc.get("skills") or []:
            files[f"skills/{skill['name']}/SKILL.md"] = skill["skill_md"]
    else:
        files["agent.json"] = _json_text(doc["agent"])
        listing = {"version": doc["version"], "title": doc["title"], "description": doc["description"]}
        listing["categories"] = doc.get("categories", [])
        files["listing.json"] = _json_text(listing)
    for rel in files:
        if not security.is_safe_relpath(rel):
            raise ExpandError([FieldError(f"unsafe path {rel!r}", None)])
    return {rel: text.encode("utf-8") for rel, text in sorted(files.items())}


def _expected_tree_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """What ``submission.from_tree`` must read back from the written files."""
    expected = copy.deepcopy(doc)
    if expected["kind"] in ("skill", "agent"):
        expected.setdefault("categories", [])
    if expected["kind"] == "skill":
        meta = submission.parse_frontmatter(expected["skill_md"].lstrip())
        for key in ("flavor", "compatible_agents"):
            if expected.get(key) is None:
                expected.pop(key, None)
                if key in meta:  # stated only in the SKILL.md the publisher wrote
                    expected[key] = meta[key]
    if expected["kind"] == "plugin":
        for key in ("mcp_json", "usage_card"):
            if expected.get(key) is None:
                expected.pop(key, None)
        if not expected.get("skills"):
            expected.pop("skills", None)
    return expected


def _no_symlink_on_path(root: Path, rel: str) -> FieldError | None:
    current = root
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            return FieldError(
                f"{current.relative_to(root).as_posix()} is a symlink; refusing to write through it", None
            )
    return None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def _timestamp(claimed_at: str | None) -> str:
    if claimed_at:
        return claimed_at
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    moment = datetime.fromtimestamp(int(epoch), timezone.utc) if epoch.isdigit() else datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def plan(
    root: Path,
    obj: Any,
    *,
    publisher: str,
    publisher_id: int,
    claimed_at: str | None = None,
    maintainer_managed: bool = False,
) -> dict[str, Any]:
    """Check everything and work out the changes. Writes nothing.

    Returns ``{"kind", "name", "version", "folder", "files", "remove",
    "registry_text", "registry_changed", "unchanged", "warnings"}``.
    Raises :class:`ExpandError` when the submission may not be published.
    """
    root = Path(root)
    if not isinstance(obj, dict):
        raise ExpandError([FieldError("a submission is a JSON object", None)])
    doc, notes, problems = check_submission(obj)
    if any(p.is_error for p in problems):
        raise ExpandError(problems)
    notes.extend(problems)
    kind, name, version = doc["kind"], doc["name"], doc["version"]
    if not is_valid_name(name):  # already refused by validate; kept next to the path use
        raise ExpandError([FieldError("name must follow the package name rules", "name")])

    try:
        base = registry.load(root / "registry.json")
    except FileNotFoundError:
        base = registry.migrate({})
    except (OSError, ValueError) as exc:
        raise ExpandError([FieldError(f"registry.json cannot be read ({exc})", "registry.json")]) from exc
    trusted = registry.load_trusted(root / "trusted-publishers.json")
    claim = registry.check_claim(base, kind, name, version, publisher, publisher_id, trusted=trusted)
    if any(p.is_error for p in claim):
        raise ExpandError(claim)
    notes.extend(claim)

    files = package_files(doc)
    rel_folder = f"{KIND_DIRS[kind]}/{name}"

    # The package as it will lie on disk, checked and read back before anything is written.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        tmp_folder = tmp_root / rel_folder
        tmp_folder.mkdir(parents=True)
        for rel, data in files.items():
            target = security.safe_join(tmp_folder, rel)
            if target is None:
                raise ExpandError([FieldError(f"unsafe path {rel!r}", None)])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        laid_out = [p for p in submission.check_package(tmp_root, kind, name) if p.is_error]
        if laid_out:
            raise ExpandError(laid_out)
        read_back = submission.from_tree(tmp_root, kind, name)
        if read_back != _expected_tree_doc(doc):
            raise ExpandError([FieldError("the package would not read back as submitted; refusing to write", None)])

    link = _no_symlink_on_path(root, rel_folder)
    if link:
        raise ExpandError([link])
    folder = root / rel_folder
    existing: dict[str, bytes] = {}
    if folder.exists():
        unsafe = [p for p in security.check_tree(folder, where=rel_folder) if p.is_error and "symlink" in p.message]
        if unsafe:
            raise ExpandError(unsafe)
        existing = {rel: (folder / rel).read_bytes() for rel in hashing.package_files(folder)}
    unchanged = existing == files
    owner = registry.owner_of(base, name)
    if existing and not unchanged and owner and owner.get("version") == version:
        raise ExpandError(
            [
                FieldError(
                    f"{rel_folder} {version} is already published with other content; use a new version", "version"
                )
            ]
        )

    new_registry = registry.apply_claim(
        base,
        kind,
        name,
        version,
        publisher,
        publisher_id,
        claimed_at=_timestamp(claimed_at),
        maintainer_managed=maintainer_managed,
    )
    registry_text = registry.dump(new_registry)
    try:
        current_registry = (root / "registry.json").read_text(encoding="utf-8")
    except OSError:
        current_registry = ""
    return {
        "kind": kind,
        "name": name,
        "version": version,
        "folder": rel_folder,
        "files": files,
        "existing": existing,
        "remove": sorted(set(existing) - set(files)),
        "registry_text": registry_text,
        "registry_changed": registry_text != current_registry,
        "unchanged": unchanged,
        "warnings": notes,
    }


def write(root: Path, result: dict[str, Any]) -> list[str]:
    """Apply a :func:`plan`. Returns the repository paths written or removed."""
    root = Path(root)
    folder_rel = result["folder"]
    link = _no_symlink_on_path(root, folder_rel)
    if link:
        raise ExpandError([link])
    folder = security.safe_join(root, folder_rel)
    if folder is None:
        raise ExpandError([FieldError(f"unsafe folder {folder_rel!r}", None)])
    touched: list[str] = []
    for rel, data in result["files"].items():
        if result["existing"].get(rel) == data:
            continue
        link = _no_symlink_on_path(folder, rel)
        target = security.safe_join(folder, rel)
        if link or target is None:
            raise ExpandError([link or FieldError(f"unsafe path {rel!r}", None)])
        _write_atomic(target, data)
        touched.append(f"{folder_rel}/{rel}")
    for rel in result["remove"]:
        target = security.safe_join(folder, rel)
        if target is not None and target.is_file() and not target.is_symlink():
            target.unlink()
            touched.append(f"{folder_rel}/{rel} (removed)")
            parent = target.parent
            while parent != folder and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    if result["registry_changed"]:
        _write_atomic(root / "registry.json", result["registry_text"].encode("utf-8"))
        touched.append("registry.json")
    return touched


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def read_submission(path: Path) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ExpandError([FieldError(f"cannot read {path}: {exc}", None)]) from exc
    if size > MAX_INPUT_BYTES:
        raise ExpandError([FieldError(f"{path.name} is {size} bytes; the limit is {MAX_INPUT_BYTES}", None)])
    try:
        return submission.extract_submission(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ExpandError([FieldError(str(exc), None)]) from exc


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("submission", type=Path, help="submission JSON file, or the [publish] issue body")
    parser.add_argument("--publisher", required=True, help="the publisher's GitHub login (the issue author)")
    parser.add_argument(
        "--publisher-id", required=True, type=_positive_int, help="the publisher's numeric GitHub account id"
    )
    parser.add_argument("--write", action="store_true", help="write the files (default: dry run, nothing written)")
    parser.add_argument("--root", type=Path, default=ROOT, help="registry checkout (default: this repository)")
    parser.add_argument("--claimed-at", help="UTC timestamp for a new claim (default: SOURCE_DATE_EPOCH or now)")
    parser.add_argument("--maintainer-managed", action="store_true", help="let trusted maintainers publish updates")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        obj = read_submission(args.submission)
        result = plan(
            root,
            obj,
            publisher=args.publisher,
            publisher_id=args.publisher_id,
            claimed_at=args.claimed_at,
            maintainer_managed=args.maintainer_managed,
        )
    except ExpandError as exc:
        for problem in exc.errors:
            label = "FAIL" if problem.is_error else "warn"
            print(f"{label}: {problem}", file=sys.stderr)
        print("expand: refused; nothing was written", file=sys.stderr)
        return 1
    for problem in result["warnings"]:
        print(f"warn: {problem}", file=sys.stderr)
    header = f"{result['kind']} {result['name']} {result['version']} -> {result['folder']}/"
    if result["unchanged"] and not result["registry_changed"]:
        print(f"expand: {header} no changes")
        return 0
    print(f"expand: {header}")
    for rel, data in result["files"].items():
        before = result["existing"].get(rel)
        action = "create" if before is None else ("same  " if before == data else "update")
        print(f"  {action} {rel}  sha256 {hashlib.sha256(data).hexdigest()}")
    for rel in result["remove"]:
        print(f"  remove {rel}")
    if result["registry_changed"]:
        print("  update registry.json")
    if not args.write:
        print("expand: dry run; nothing written. Add --write to write these files.")
        return 0
    try:
        touched = write(root, result)
    except ExpandError as exc:
        for problem in exc.errors:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    print(
        f"expand: wrote {len(touched)} path(s). Next: python3 scripts/compile_index.py && python3 scripts/validate.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
