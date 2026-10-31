"""Who owns each package name: ``registry.json`` and ``trusted-publishers.json``.

A name belongs to the publisher who first published it, identified by the
immutable numeric GitHub account id. Logins can be renamed, ids cannot, so
the id decides; the login is recorded for people to read.

``registry.json`` (format version 2)::

    {
      "version": 2,
      "owners": {
        "<name>": {
          "kind": "skill" | "plugin" | "agent",
          "publisher": "<GitHub login>",
          "publisher_id": <numeric GitHub id> | null,   # null: not yet looked up
          "version": "<latest published SemVer>",
          "claimed_at": "<UTC timestamp of the first claim>",
          "maintainer_managed": true                   # optional
        }
      }
    }

Format version 1 (no ``version`` per owner, ``publisher_id`` optional) is
still read: :func:`migrate` brings it to version 2 in memory, and an owner
without a stored version simply skips the downgrade check.

``trusted-publishers.json`` lists the first-party publishers. A package is
``official`` only when its owner's numeric id is listed there with a
confirmed id; everything else is ``community``. A trusted publisher with
the ``maintainer`` role may also publish updates to a package whose owner
entry is ``maintainer_managed``.

Nothing here touches the network or the file system beyond reading and
writing the two JSON files it is given.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .rules import GITHUB_LOGIN_RE, KINDS, FieldError, is_semver, is_valid_name, reserved_reason, version_tuple, warning

REGISTRY_FORMAT = 2
TRUSTED_FORMAT = 1
TRUST_LEVELS = ("official", "community")
MAINTAINER_ROLE = "maintainer"
_OWNER_KEY_ORDER = ("kind", "publisher", "publisher_id", "version", "claimed_at", "maintainer_managed")
OWNER_KEYS = frozenset(_OWNER_KEY_ORDER)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def compare_versions(a: str, b: str) -> int:
    """-1, 0 or 1 as ``a`` is lower than, equal to or higher than ``b`` (plain SemVer)."""
    left, right = version_tuple(a), version_tuple(b)
    return (left > right) - (left < right)


def is_publisher_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


# ---------------------------------------------------------------------------
# registry.json
# ---------------------------------------------------------------------------


def migrate(data: Any) -> dict[str, Any]:
    """Return ``data`` as a format-2 registry. Never changes the argument.

    Version 1 owner entries keep every field; ``publisher_id`` becomes an
    explicit ``null`` when absent. Running it on a version 2 registry
    returns an equal copy.
    """
    if not isinstance(data, Mapping):
        return {"version": REGISTRY_FORMAT, "owners": {}}
    owners_in = data.get("owners") if isinstance(data.get("owners"), Mapping) else {}
    owners: dict[str, Any] = {}
    for name, entry in owners_in.items():
        if isinstance(entry, Mapping):
            owner = copy.deepcopy(dict(entry))
            owner.setdefault("publisher_id", None)
            owners[name] = owner
        else:
            owners[name] = copy.deepcopy(entry)
    out = {key: copy.deepcopy(value) for key, value in data.items() if key not in ("version", "owners")}
    out["version"] = REGISTRY_FORMAT
    out["owners"] = owners
    return out


def load(path: Path) -> dict[str, Any]:
    """Read and migrate ``registry.json``. Raises ``OSError`` / ``ValueError``."""
    return migrate(json.loads(Path(path).read_text(encoding="utf-8")))


def dump(registry: Mapping[str, Any]) -> str:
    """The registry as written to disk: owners sorted by name, fixed key order."""
    owners = registry.get("owners") or {}
    ordered: dict[str, Any] = {}
    for name in sorted(owners):
        entry = owners[name]
        if isinstance(entry, Mapping):
            row = {k: entry[k] for k in _OWNER_KEY_ORDER if k in entry}
            row.update({k: entry[k] for k in sorted(entry) if k not in row})
            ordered[name] = row
        else:
            ordered[name] = entry
    out: dict[str, Any] = {"version": registry.get("version", REGISTRY_FORMAT), "owners": ordered}
    out.update({k: registry[k] for k in sorted(registry) if k not in out})
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def owner_of(registry: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    owners = registry.get("owners")
    entry = owners.get(name) if isinstance(owners, Mapping) else None
    return dict(entry) if isinstance(entry, Mapping) else None


def check_owner_entry(name: str, owner: Any) -> list[FieldError]:
    """Shape of one format-2 owner entry (the fields beyond what validate.py checks)."""
    where = f"registry.json owners.{name}"
    if not isinstance(owner, Mapping):
        return []
    errors: list[FieldError] = []
    unknown = sorted(set(owner) - OWNER_KEYS)
    if unknown:
        errors.append(FieldError(f"{where} has unknown fields: {', '.join(unknown)}", "registry.json"))
    if "version" in owner and not is_semver(owner.get("version")):
        errors.append(FieldError(f"{where}.version must be plain SemVer, e.g. 1.0.0", "registry.json"))
    if "maintainer_managed" in owner and not isinstance(owner.get("maintainer_managed"), bool):
        errors.append(FieldError(f"{where}.maintainer_managed must be true or false", "registry.json"))
    claimed = owner.get("claimed_at")
    if claimed is not None and not isinstance(claimed, str):
        errors.append(FieldError(f"{where}.claimed_at must be a UTC timestamp string", "registry.json"))
    return errors


# ---------------------------------------------------------------------------
# trusted-publishers.json
# ---------------------------------------------------------------------------


def check_trusted(data: Any) -> list[FieldError]:
    """Shape of ``trusted-publishers.json``."""
    where = "trusted-publishers.json"
    if not isinstance(data, Mapping) or not isinstance(data.get("publishers"), list):
        return [FieldError(f"{where} must be an object with a publishers list", where)]
    errors: list[FieldError] = []
    if data.get("version") != TRUSTED_FORMAT:
        errors.append(FieldError(f"{where} version must be {TRUSTED_FORMAT}", where))
    seen: set = set()
    for index, entry in enumerate(data["publishers"]):
        at = f"{where} publishers[{index}]"
        if not isinstance(entry, Mapping):
            errors.append(FieldError(f"{at} must be an object", where))
            continue
        login = entry.get("login")
        if not isinstance(login, str) or not GITHUB_LOGIN_RE.fullmatch(login):
            errors.append(FieldError(f"{at}.login must be a GitHub login", where))
        pid = entry.get("id")
        if pid is None:
            if entry.get("status") != "pending":
                errors.append(FieldError(f"{at} has no id; mark it \"status\": \"pending\" until it is looked up", where))
            else:
                errors.append(warning(f"{at} ({login}) has no confirmed GitHub id yet; it is not trusted", where))
        elif not is_publisher_id(pid):
            errors.append(FieldError(f"{at}.id must be a positive integer (the numeric GitHub account id)", where))
        elif pid in seen:
            errors.append(FieldError(f"{at}.id {pid} is listed twice", where))
        else:
            seen.add(pid)
        if entry.get("trust") not in TRUST_LEVELS:
            errors.append(FieldError(f"{at}.trust must be one of {', '.join(TRUST_LEVELS)}", where))
        roles = entry.get("roles", [])
        if not isinstance(roles, list) or not all(r == MAINTAINER_ROLE for r in roles):
            errors.append(FieldError(f"{at}.roles may only contain {MAINTAINER_ROLE!r}", where))
    return errors


def load_trusted(path: Path) -> dict[int, dict[str, Any]]:
    """Trusted publishers with a confirmed numeric id, keyed by that id.

    A missing file means no one is trusted. Entries still pending an id are
    left out: trust is never granted on a login alone.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, dict[str, Any]] = {}
    if isinstance(data, Mapping) and isinstance(data.get("publishers"), list):
        for entry in data["publishers"]:
            if isinstance(entry, Mapping) and is_publisher_id(entry.get("id")):
                out[entry["id"]] = dict(entry)
    return out


def trust_for(publisher_id: Any, trusted: Mapping[int, Mapping[str, Any]] | None) -> str:
    """``official`` when ``publisher_id`` is a trusted first-party id, else ``community``."""
    if trusted and is_publisher_id(publisher_id):
        entry = trusted.get(publisher_id)
        if entry and entry.get("trust") == "official":
            return "official"
    return "community"


def _is_maintainer(publisher_id: Any, trusted: Mapping[int, Mapping[str, Any]] | None) -> bool:
    if not trusted or not is_publisher_id(publisher_id):
        return False
    entry = trusted.get(publisher_id)
    return bool(entry) and MAINTAINER_ROLE in (entry.get("roles") or [])


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------


def check_claim(
    registry: Any,
    kind: str,
    name: Any,
    version: Any,
    publisher: Any,
    publisher_id: Any,
    *,
    trusted: Mapping[int, Mapping[str, Any]] | None = None,
) -> list[FieldError]:
    """Every reason ``publisher`` may not publish ``kind`` ``name`` at ``version``.

    ``registry`` is the registry as it was *before* this change (format 1 or
    2). An empty list means the claim is allowed; warnings do not refuse it.
    """
    errors: list[FieldError] = []
    if kind not in KINDS:
        return [FieldError(f"kind must be one of {', '.join(KINDS)}", "kind")]
    if not is_valid_name(name):
        return [FieldError("name must follow the package name rules", "name")]
    reason = reserved_reason(kind, name)
    if reason:
        errors.append(FieldError(reason, "name"))
    if not is_semver(version):
        errors.append(FieldError("version must be plain SemVer, e.g. 1.0.0", "version"))
    if not isinstance(publisher, str) or not GITHUB_LOGIN_RE.fullmatch(publisher):
        errors.append(FieldError("publisher must be a GitHub login", "publisher"))
    if publisher_id is not None and not is_publisher_id(publisher_id):
        errors.append(FieldError("publisher_id must be a positive integer", "publisher_id"))

    base = migrate(registry)
    owner = owner_of(base, name)
    if owner is None:
        if publisher_id is None:
            errors.append(
                warning("publisher_id is missing; record the owner's numeric GitHub id with a new claim", "publisher_id")
            )
        return errors

    if owner.get("kind") != kind:
        errors.append(FieldError(f"{name!r} is already published as a {owner.get('kind')}, not a {kind}", "name"))
        return errors

    stored_id = owner.get("publisher_id")
    stored_login = owner.get("publisher")
    same_owner = False
    if is_publisher_id(stored_id):
        if publisher_id == stored_id:
            same_owner = True
            if isinstance(publisher, str) and isinstance(stored_login, str) and publisher.lower() != stored_login.lower():
                errors.append(
                    warning(f"login changed from {stored_login!r} to {publisher!r}; the account id still matches", "publisher")
                )
    elif isinstance(publisher, str) and isinstance(stored_login, str) and publisher.lower() == stored_login.lower():
        same_owner = True
        errors.append(warning(f"{name!r} has no recorded publisher_id; matched on the login only", "publisher_id"))

    if not same_owner:
        if owner.get("maintainer_managed") is True and _is_maintainer(publisher_id, trusted):
            pass  # a trusted maintainer may publish a maintainer-managed package
        elif is_publisher_id(stored_id):
            errors.append(
                FieldError(
                    f"{name!r} belongs to GitHub account {stored_id} ({stored_login}); this claim is from "
                    f"{publisher_id if publisher_id is not None else 'an unknown id'} ({publisher})",
                    "publisher_id",
                )
            )
        else:
            errors.append(FieldError(f"{name!r} belongs to {stored_login!r}, not {publisher!r}", "publisher"))

    stored_version = owner.get("version")
    if is_semver(version) and is_semver(stored_version) and compare_versions(version, stored_version) < 0:
        errors.append(
            FieldError(f"version {version} is lower than the published {stored_version}; versions only go up", "version")
        )
    return errors


def apply_claim(
    registry: Any,
    kind: str,
    name: str,
    version: str,
    publisher: str,
    publisher_id: int | None,
    *,
    claimed_at: str,
    maintainer_managed: bool = False,
) -> dict[str, Any]:
    """A new format-2 registry with this claim recorded. Never changes ``registry``.

    An existing owner keeps its ``claimed_at`` and ``maintainer_managed``
    and, unless a trusted maintainer is publishing for them, its login and
    id; only the version moves. Call :func:`check_claim` first.
    """
    out = migrate(registry)
    owner = out["owners"].get(name)
    if isinstance(owner, dict) and owner.get("kind") == kind:
        stored_id = owner.get("publisher_id")
        is_owner = (is_publisher_id(stored_id) and stored_id == publisher_id) or (
            not is_publisher_id(stored_id) and str(owner.get("publisher", "")).lower() == publisher.lower()
        )
        if is_owner:
            owner["publisher"] = publisher
            if publisher_id is not None:
                owner["publisher_id"] = publisher_id
        owner["version"] = version
        if maintainer_managed:
            owner["maintainer_managed"] = True
        return out
    entry: dict[str, Any] = {
        "kind": kind,
        "publisher": publisher,
        "publisher_id": publisher_id,
        "version": version,
        "claimed_at": claimed_at,
    }
    if maintainer_managed:
        entry["maintainer_managed"] = True
    out["owners"][name] = entry
    return out
