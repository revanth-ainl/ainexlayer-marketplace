"""Shared pieces of the three publishing workflows (intake, gate, publish).

Kept free of any GitHub or git calls of its own: the scripts pass in a
``GitHubClient``, a ``GitOps`` and a ``publish`` callable, so every path here
runs in tests against fakes.

``publish`` is the bridge to the trusted pipeline. It takes the submission
path and the verified author and returns a ``PublishOutcome``:

* ``published`` — the bot commit was pushed; ``sha`` is that commit.
* ``unchanged`` — the submission was already published exactly like this.
* ``refused``   — the validator said no; ``findings`` says why.
* ``failed``    — pushing kept losing races or something broke; retry later.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Callable, NamedTuple

LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
# The package-name rule the app enforces (community_source._SKILL_NAME_RE).
# Used here only to refuse an unsafe file name before any path is built; the
# validator remains the authority on names.
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
SUBMISSION_PATH_RE = re.compile(r"^submissions/([a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?)\.json$")
MAX_SUBMISSION_BYTES = 256 * 1024
MAX_FINDINGS_SHOWN = 30

#: Finding code for a submission that a later change on main replaced.
SUPERSEDED = "superseded"

LABEL_PUBLISHED = "published"
LABEL_NEEDS_CHANGES = "needs-changes"
LABEL_PUBLISH_FAILED = "publish-failed"


class Author(NamedTuple):
    """A GitHub account as GitHub itself authenticated it."""

    login: str
    id: int


class PublishOutcome(NamedTuple):
    status: str  # published | unchanged | refused | failed
    sha: str | None = None
    findings: Sequence[Any] = ()
    detail: str = ""


PublishFn = Callable[[Path, Author], PublishOutcome]


class AutomationError(ValueError):
    """A submission problem the publisher can fix; shown to them verbatim."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def only_superseded(findings: Iterable[Any]) -> bool:
    items = list(findings)
    return bool(items) and all(getattr(f, "code", None) == SUPERSEDED for f in items)


# -- identity -----------------------------------------------------------------


def author_from_user(user: Any) -> Author:
    """Read the account from an event's ``user`` object.

    Only real user accounts publish. Bots (including this repository's own
    automation) and organisations are refused, so nothing can publish under
    an identity GitHub did not authenticate as a person.
    """
    if not isinstance(user, dict):
        raise AutomationError("author", "the event carries no author")
    login = user.get("login")
    uid = user.get("id")
    if user.get("type") != "User":
        raise AutomationError("author", "only personal GitHub accounts can publish")
    if not isinstance(login, str) or not LOGIN_RE.fullmatch(login):
        raise AutomationError("author", "the author login is not a valid GitHub login")
    if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
        raise AutomationError("author", "the author has no numeric account id")
    return Author(login, uid)


def bind_author(data: dict[str, Any], author: Author, *, allow_claims: bool) -> dict[str, Any]:
    """Stamp the verified author onto a submission.

    ``allow_claims=False`` (intake): whatever the body says is overwritten.
    ``allow_claims=True`` (pull requests): a ``publisher`` / ``publisher_id``
    already in the file must name the PR author, otherwise the file is
    claiming someone else's identity and is refused.
    """
    if allow_claims:
        claimed = data.get("publisher")
        if claimed is not None and (not isinstance(claimed, str) or claimed.casefold() != author.login.casefold()):
            raise AutomationError("publisher", "publisher must be the GitHub account that opened the pull request")
        claimed_id = data.get("publisher_id")
        if claimed_id is not None and (isinstance(claimed_id, bool) or claimed_id != author.id):
            raise AutomationError(
                "publisher_id", "publisher_id must be the id of the account that opened the pull request"
            )
    out = dict(data)
    out["publisher"] = author.login
    out["publisher_id"] = author.id
    return out


# -- JSON as data -------------------------------------------------------------


def _no_duplicate_keys(pairs: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise AutomationError("json", f"the submission repeats the key {str(key)[:60]!r}")
        out[key] = value
    return out


def _reject_constant(token: str) -> Any:
    raise AutomationError("json", f"the submission contains {token}, which is not JSON")


def loads_submission(raw: bytes) -> dict[str, Any]:
    """Parse untrusted submission bytes. Never evaluates anything."""
    if len(raw) > MAX_SUBMISSION_BYTES:
        raise AutomationError("size", f"the submission is larger than {MAX_SUBMISSION_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AutomationError("json", "the submission is not UTF-8 text") from exc
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise AutomationError(
            "json", f"the submission is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from exc
    except RecursionError as exc:
        raise AutomationError("json", "the submission is nested too deeply") from exc
    if not isinstance(data, dict):
        raise AutomationError("json", "the submission must be a JSON object")
    return data


def submission_name(data: dict[str, Any]) -> str:
    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or ".." in name:
        raise AutomationError("name", "name must be a lowercase slug: a-z, 0-9, dots and hyphens, 1-64 characters")
    return name


def write_submission(root: Path, data: dict[str, Any]) -> Path:
    """Write ``submissions/<name>.json`` inside ``root`` and nowhere else."""
    name = submission_name(data)
    folder = (root / "submissions").resolve()
    path = (folder / f"{name}.json").resolve()
    if path.parent != folder:
        raise AutomationError("name", "the submission name leaves the submissions folder")
    folder.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


# -- messages -----------------------------------------------------------------


def _clean(text: Any, limit: int = 300) -> str:
    """Make validator text safe to post: one line, no mentions, no HTML."""
    s = " ".join(str(text).split())[:limit]
    s = s.replace("@", "@\u200b").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace("`", "'")


def render_findings(findings: Iterable[Any]) -> str:
    lines = []
    items = list(findings)
    for f in items[:MAX_FINDINGS_SHOWN]:
        if isinstance(f, AutomationError):
            code, path, message = f.code, "", f.message
        else:
            code = getattr(f, "code", "") or ""
            path = getattr(f, "path", "") or ""
            message = getattr(f, "message", None) or str(f)
        where = f" ({_clean(path, 120)})" if path else ""
        lines.append(f"- `{_clean(code, 60) or 'invalid'}`{where}: {_clean(message)}")
    if len(items) > MAX_FINDINGS_SHOWN:
        lines.append(f"- … and {len(items) - MAX_FINDINGS_SHOWN} more")
    return "\n".join(lines) or "- the submission was refused"


def run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if repo and run_id.isdigit():
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def published_message(name: str, version: Any) -> str:
    return (
        f"Published `{_clean(name, 64)}` version `{_clean(version, 40)}`.\n\n"
        "It appears in the AINexLayer Marketplace once the feed redeploys, usually within a few minutes. "
        "Community packages are shown as unreviewed, and AIAgentLayer asks for consent before installing."
    )


def refused_message(findings: Iterable[Any], retry_hint: str) -> str:
    return f"This submission was not published:\n\n{render_findings(findings)}\n\n{retry_hint}"


def superseded_message(name: str) -> str:
    return (
        f"Merged, but `{_clean(name, 64)}` changed again on main before this run published it. "
        "Nothing was published for this pull request; the newer change is published by its own run, under its own author."
    )


def failed_message() -> str:
    link = run_url()
    where = f" Details: {link}" if link else ""
    return (
        "Publishing did not finish (the registry changed too many times while this ran, or an internal step failed). "
        f"Nothing was published. A maintainer can re-run it.{where}"
    )


# -- workflow plumbing --------------------------------------------------------


def load_event(path: str | None = None) -> dict[str, Any]:
    event_path = path or os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        raise SystemExit("GITHUB_EVENT_PATH is not set")
    with open(event_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SystemExit("the event payload is not an object")
    return data


def set_output(name: str, value: str) -> None:
    if not re.fullmatch(r"[a-z_]{1,40}", name) or "\n" in value:
        raise ValueError("unsafe output")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")
