"""The auto-merge decision for a submission pull request.

``evaluate`` is pure: it sees the PR's file list, a function that reads one
file of the PR head as bytes, the verified author and the registry owners,
and a validator. It never imports, runs or checks out anything from the PR.

A pull request is eligible only when all of this holds:

1. It changes exactly one file, ``submissions/<name>.json``, added or
   modified (never removed, renamed or copied).
2. The file is a JSON object whose ``name`` matches the file name.
3. Any ``publisher`` / ``publisher_id`` in it names the PR author.
4. A ``modified`` file belongs to a name already in the registry (a
   pending, unpublished submission cannot be rewritten by another PR), and
   the name is not maintainer-managed (a registry entry without a numeric
   ``publisher_id`` or with ``source: "maintainer"``).
5. The validator, run with the PR author as the publisher, returns nothing.
   That covers ownership, the version bump and every package rule.

Passing the gate does not make a package trusted: trust comes only from
``trusted-publishers.json``, which no automated path can change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, NamedTuple

from .automation import (
    SUBMISSION_PATH_RE,
    Author,
    AutomationError,
    bind_author,
    loads_submission,
)

Validate = Callable[[dict[str, Any], Author], list[Any]]


class Verdict(NamedTuple):
    eligible: bool
    reasons: list[Any]
    name: str | None = None
    path: str | None = None
    data: dict[str, Any] | None = None


def _refuse(code: str, message: str, **kw: Any) -> Verdict:
    return Verdict(False, [AutomationError(code, message)], **kw)


def maintainer_managed(owners: Mapping[str, Any], name: str) -> bool:
    entry = owners.get(name) if isinstance(owners, Mapping) else None
    if not isinstance(entry, Mapping):
        return False
    pid = entry.get("publisher_id")
    return entry.get("source") == "maintainer" or isinstance(pid, bool) or not isinstance(pid, int)


def evaluate(
    changes: list[dict[str, Any]],
    read_blob: Callable[[str], bytes],
    author: Author,
    registry: Mapping[str, Any],
    *,
    validate: Validate,
) -> Verdict:
    if len(changes) != 1:
        return _refuse("files", "a submission pull request changes exactly one file: submissions/<name>.json")
    change = changes[0]
    path = change.get("filename")
    status = change.get("status")
    if not isinstance(path, str):
        return _refuse("files", "the pull request file list is malformed")
    match = SUBMISSION_PATH_RE.fullmatch(path)
    if not match:
        return _refuse("files", "only submissions/<name>.json may change in a submission pull request")
    if status not in ("added", "modified") or change.get("previous_filename"):
        return _refuse("files", "the submission file must be added or modified, not removed, renamed or copied")
    name = match.group(1)
    if status == "modified" and not (isinstance(registry, Mapping) and name in registry):
        # The file exists on main but was never published (pending or failed).
        # It belongs to whoever submitted it; only a maintainer may change it.
        return _refuse(
            "owner",
            f"{name} has a submission that is not published yet; only a maintainer can change it",
            name=name,
            path=path,
        )
    if maintainer_managed(registry, name):
        return _refuse(
            "owner",
            f"{name} is maintained by the registry team and cannot be updated by a pull request",
            name=name,
            path=path,
        )

    try:
        data = loads_submission(read_blob(path))
        if data.get("name") != name:
            raise AutomationError("name", "the name inside the file must equal the file name")
        data = bind_author(data, author, allow_claims=True)
    except AutomationError as exc:
        return Verdict(False, [exc], name=name, path=path)

    findings = list(validate(data, author))
    if findings:
        return Verdict(False, findings, name=name, path=path, data=data)
    return Verdict(True, [], name=name, path=path, data=data)
