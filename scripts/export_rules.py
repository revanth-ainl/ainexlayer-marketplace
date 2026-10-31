#!/usr/bin/env python3
"""Export the marketplace rule set to docs/rules.json and the JSON Schemas to schemas/.

Nothing is defined here: every value is read from the validation library
(scripts/ainexlayer_marketplace), which stays the one authority. Each
public UPPER_CASE constant a module defines itself is exported under that
module's name; compiled patterns become their pattern text, sets become
sorted lists. The output has no timestamp, so it changes only when a rule
does. The JSON Schemas in ``schemas/`` are generated from the same
constants (``ainexlayer_marketplace/schemas.py``), so they cannot drift
from the validator either.

Run by hand from the repository root:

    python3 scripts/export_rules.py           # write docs/rules.json and schemas/ if a rule changed
    python3 scripts/export_rules.py --check   # exit 1 if any of them is out of date
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ainexlayer_marketplace import registry, rules, schemas, security, submission

RULES_PATH = Path("docs") / "rules.json"
SCHEMAS_DIR = Path("schemas")
#: Modules exported, in output order. ``rules`` first: the others re-use it.
MODULES: tuple[ModuleType, ...] = (rules, submission, security, registry)
_CONSTANT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_FLAG_NAMES = (("IGNORECASE", re.IGNORECASE), ("MULTILINE", re.MULTILINE), ("DOTALL", re.DOTALL))


def _jsonable(value: Any) -> Any:
    if isinstance(value, re.Pattern):
        flags = [name for name, flag in _FLAG_NAMES if value.flags & flag]
        return {"pattern": value.pattern, "flags": flags} if flags else value.pattern
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"cannot export {type(value).__name__}")


def _shape(name: str, value: Any) -> Any:
    """Name the two halves of the auth field table instead of leaving a bare pair."""
    if name == "AUTH_FIELDS":
        return {mode: {"required": _jsonable(req), "optional": _jsonable(opt)} for mode, (req, opt) in value.items()}
    if name == "INSTANCE_URL_FIELDS":
        required, optional = value
        return {"required": _jsonable(required), "optional": _jsonable(optional)}
    return _jsonable(value)


def module_constants(module: ModuleType) -> dict[str, Any]:
    """The public constants ``module`` defines itself (not those it imports)."""
    out: dict[str, Any] = {}
    for name in sorted(vars(module)):
        if not _CONSTANT_RE.match(name):
            continue
        value = getattr(module, name)
        if isinstance(value, ModuleType) or callable(value):
            continue
        borrowed = any(m is not module and getattr(m, name, None) is value for m in MODULES)
        if borrowed and module is not rules:
            continue
        out[name] = _shape(name, value)
    return out


def build_rules() -> dict[str, Any]:
    return {
        "generated_by": "scripts/export_rules.py",
        "authority": "scripts/ainexlayer_marketplace (this file is a read-only export)",
        "app_source_commit": rules.APP_SOURCE_COMMIT,
        "modules": {
            module.__name__.rsplit(".", 1)[-1]: {
                "summary": (module.__doc__ or "").strip().splitlines()[0],
                "constants": module_constants(module),
            }
            for module in MODULES
        },
    }


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def outputs(rules_path: Path, schemas_dir: Path) -> dict[Path, str]:
    """Every file this script owns, with the text it should hold."""
    files = {rules_path: render(build_rules())}
    for name, schema in schemas.build_all().items():
        files[schemas_dir / name] = render(schema)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / RULES_PATH, help="default: docs/rules.json")
    parser.add_argument("--schemas-dir", type=Path, default=ROOT / SCHEMAS_DIR, help="default: schemas/")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if any file is out of date")
    args = parser.parse_args(argv)
    stale: list[Path] = []
    files = outputs(args.output, args.schemas_dir)
    for path, text in files.items():
        try:
            current = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current = ""
        if current != text:
            stale.append(path)
    extra = sorted(p for p in args.schemas_dir.glob("*.json") if p not in files) if args.schemas_dir.is_dir() else []
    if args.check:
        if not stale and not extra:
            print(f"export_rules: {len(files)} file(s) up to date")
            return 0
        for path in stale:
            print(f"export_rules: {path.name} is out of date; run python3 scripts/export_rules.py", file=sys.stderr)
        for path in extra:
            print(f"export_rules: {path.name} is not generated by this script; remove it", file=sys.stderr)
        return 1
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(files[path], encoding="utf-8")
        os.replace(tmp, path)
        print(f"export_rules: wrote {path.name}")
    for path in extra:
        print(f"warn: {path.name} is not generated by this script", file=sys.stderr)
    if not stale:
        print(f"export_rules: {len(files)} file(s) unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
