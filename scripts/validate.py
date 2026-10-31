#!/usr/bin/env python3
"""Check the whole registry, or one submission, against the marketplace rules.

Run by hand from the repository root before committing:

    python3 scripts/validate.py                      # every package + registry.json
    python3 scripts/validate.py --base-ref origin/main   # also ownership and version rules
    python3 scripts/validate.py --submission FILE    # one submission JSON, or an issue body

Exit status is 1 when anything is refused. Warnings are printed and do not
fail the run. Nothing here touches the network or GitHub.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ainexlayer_marketplace import submission
from ainexlayer_marketplace.rules import GITHUB_LOGIN_RE, KINDS, FieldError, warning

KIND_DIRS = {"skill": "skills", "plugin": "plugins", "agent": "agents"}
#: Files that make the repository run or publish something on GitHub. The
#: registry is source only; see README.md.
FORBIDDEN_REPO_PATHS = (
    ".github/workflows",
    ".github/dependabot.yml",
    ".github/dependabot.yaml",
    "docs/CNAME",
    "docs/.nojekyll",
    "CNAME",
    ".nojekyll",
)


def check_repository(root: Path) -> list[FieldError]:
    return [
        FieldError(f"{rel} must not exist: this repository runs no GitHub automation", rel)
        for rel in FORBIDDEN_REPO_PATHS
        if (root / rel).exists() or (root / rel).is_symlink()
    ]


def find_packages(root: Path) -> list[tuple]:
    """``(kind, name)`` for every package folder, plus errors for stray entries."""
    found = []
    for kind in KINDS:
        base = root / KIND_DIRS[kind]
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if entry.name == "README.md" and entry.is_file() and not entry.is_symlink():
                continue
            found.append((kind, entry.name))
    return found


def load_registry(root: Path) -> tuple:
    path = root / "registry.json"
    if not path.is_file():
        return {}, [FieldError("registry.json is missing", "registry.json")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, [FieldError(f"registry.json is not valid JSON ({exc})", "registry.json")]
    if not isinstance(data, dict) or not isinstance(data.get("owners"), dict):
        return {}, [FieldError("registry.json must be an object with an owners map", "registry.json")]
    return data, []


def check_owners(registry: dict[str, Any], packages: list[tuple]) -> list[FieldError]:
    """Every package has an owner of the same kind, and every owner entry is well formed."""
    errors: list[FieldError] = []
    owners = registry.get("owners", {})
    names: dict[str, str] = {}
    for kind, name in packages:
        if name in names:
            errors.append(FieldError(f"{name!r} is published as both a {names[name]} and a {kind}", name))
            continue
        names[name] = kind
        owner = owners.get(name)
        if not isinstance(owner, dict):
            errors.append(FieldError(f"{KIND_DIRS[kind]}/{name} has no owner in registry.json", "registry.json"))
        elif owner.get("kind") != kind:
            errors.append(
                FieldError(f"registry.json lists {name!r} as a {owner.get('kind')}, not a {kind}", "registry.json")
            )
    for name, owner in sorted(owners.items()):
        where = f"registry.json owners.{name}"
        if not isinstance(owner, dict):
            errors.append(FieldError(f"{where} must be an object", "registry.json"))
            continue
        if owner.get("kind") not in KINDS:
            errors.append(FieldError(f"{where}.kind must be one of {', '.join(KINDS)}", "registry.json"))
        publisher = owner.get("publisher")
        if not isinstance(publisher, str) or not GITHUB_LOGIN_RE.fullmatch(publisher):
            errors.append(FieldError(f"{where}.publisher must be a GitHub login", "registry.json"))
        publisher_id = owner.get("publisher_id")
        if publisher_id is None:
            errors.append(
                warning(f"{where} has no publisher_id (the owner's numeric GitHub account id)", "registry.json")
            )
        elif isinstance(publisher_id, bool) or not isinstance(publisher_id, int) or publisher_id <= 0:
            errors.append(FieldError(f"{where}.publisher_id must be a positive integer", "registry.json"))
        if name not in names:
            errors.append(warning(f"{where} has no package folder (delisted?)", "registry.json"))
    return errors


def _git_show(root: Path, ref: str, rel: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{ref}:{rel}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def check_against_base(
    root: Path, ref: str, registry: dict[str, Any], docs: dict[tuple, dict[str, Any]]
) -> list[FieldError]:
    """Ownership and version rules against ``ref`` (registry.check_claim)."""
    try:
        from ainexlayer_marketplace import registry as registry_rules
    except ImportError:
        return [FieldError("scripts/ainexlayer_marketplace/registry.py is missing; ownership rules cannot run", None)]
    verify = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    if verify.returncode != 0:
        return [FieldError(f"--base-ref {ref!r} is not a commit in this repository", None)]
    base_text = _git_show(root, ref, "registry.json")
    try:
        base_registry = json.loads(base_text) if base_text else {"owners": {}}
    except json.JSONDecodeError:
        return [FieldError(f"registry.json at {ref} is not valid JSON", "registry.json")]
    errors: list[FieldError] = []
    owners = registry.get("owners", {})
    for (kind, name), doc in sorted(docs.items()):
        owner = owners.get(name) if isinstance(owners.get(name), dict) else {}
        for problem in registry_rules.check_claim(
            base_registry,
            kind,
            name,
            doc.get("version"),
            owner.get("publisher"),
            owner.get("publisher_id"),
        ):
            errors.append(FieldError(f"{KIND_DIRS[kind]}/{name}: {problem.message}", problem.field, problem.severity))
    return errors


def validate_tree(root: Path, base_ref: str | None = None) -> list[FieldError]:
    errors = check_repository(root)
    packages = find_packages(root)
    docs: dict[tuple, dict[str, Any]] = {}
    for kind, name in packages:
        errors.extend(submission.check_package(root, kind, name))
        try:
            docs[(kind, name)] = submission.from_tree(root, kind, name)
        except (submission.PackageLayoutError, OSError, UnicodeDecodeError):
            pass  # already reported by check_package
    registry, registry_errors = load_registry(root)
    errors.extend(registry_errors)
    if registry:
        errors.extend(check_owners(registry, packages))
        if base_ref:
            errors.extend(check_against_base(root, base_ref, registry, docs))
    return errors


def validate_submission_file(path: Path) -> list[FieldError]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [FieldError(f"cannot read {path}: {exc}", None)]
    try:
        obj = submission.extract_submission(text)
    except ValueError as exc:
        return [FieldError(str(exc), None)]
    return submission.validate(obj)


def report(errors: list[FieldError]) -> int:
    failed = [e for e in errors if e.is_error]
    for problem in errors:
        label = "FAIL" if problem.is_error else "warn"
        print(f"{label}: {problem.message}", file=sys.stderr)
    if failed:
        print(f"validate: {len(failed)} problem(s) refused", file=sys.stderr)
        return 1
    print("validate: ok" + (f" ({len(errors)} warning(s))" if errors else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="registry checkout (default: this repository)")
    parser.add_argument("--base-ref", help="git ref to check ownership and version increases against, e.g. origin/main")
    parser.add_argument("--submission", type=Path, help="validate one submission JSON file or issue body instead")
    args = parser.parse_args(argv)
    if args.submission:
        return report(validate_submission_file(args.submission))
    return report(validate_tree(args.root.resolve(), args.base_ref))


if __name__ == "__main__":
    raise SystemExit(main())
