"""Where the publishing workflows meet the validator and the pipeline.

Two halves:

* ``publish_with_retry`` owns the git side of a publish: start from a fresh
  ``main``, run one pipeline attempt, commit as the bot, push. When the push
  loses a race to another writer it starts over from the new ``main`` — so
  the claim, the owner id and the version are checked again against the
  registry as it is now — and gives up after ``attempts`` tries with a
  ``failed`` outcome the workflow reports on the issue or pull request.
* ``library_validate`` / ``library_attempt`` call the shared library through
  the agreed interface (``submission.parse_submission``,
  ``submission.validate_submission``, ``registry.load``,
  ``pipeline.publish``). This is the only file in the automation that names
  those functions.
"""

from __future__ import annotations

import random
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable, NamedTuple

from .automation import SUPERSEDED, Author, AutomationError, PublishOutcome
from .gitops import GitError, PushRejected

#: Everything a publish may change. The bot commit stages these and nothing
#: else, so a pipeline bug cannot commit scripts or workflows.
PUBLISHED_PATHS = (
    "submissions",
    "skills",
    "plugins",
    "agents",
    "registry.json",
    "docs/index.json",
    "docs/rules.json",
)


class Attempt(NamedTuple):
    ok: bool
    findings: Sequence[Any] = ()


AttemptFn = Callable[[Path, Author], Attempt]


def publish_with_retry(
    sub_path: Path,
    author: Author,
    *,
    git: Any,
    attempt: AttemptFn,
    message: str,
    attempts: int = 5,
    sleep: Callable[[float], None] = time.sleep,
    expected: bytes | None = None,
) -> PublishOutcome:
    """Publish ``sub_path`` from a fresh main, retrying lost push races.

    ``expected`` pins the content: when given, every attempt first checks that
    the submission on the newest main is byte-for-byte the content that was
    checked and merged. If a later change replaced it, this run publishes
    nothing (``superseded``) and that later change is published by its own
    run, under its own author.
    """
    last_error = ""
    for n in range(attempts):
        try:
            git.fetch_branch()
            git.reset_to(f"{git.remote}/{git.branch}")
            if expected is not None and _read_or_none(sub_path) != expected:
                return PublishOutcome("refused", findings=[superseded_finding(sub_path)])
            result = attempt(sub_path, author)
            if not result.ok:
                return PublishOutcome("refused", findings=list(result.findings))
            sha = git.commit(PUBLISHED_PATHS, message)
            if sha is None:
                return PublishOutcome("unchanged", sha=git.head())
            return PublishOutcome("published", sha=git.push())
        except PushRejected:
            last_error = "another publish changed main first"
            # Jittered backoff so two racing writers do not collide again.
            sleep(min(30.0, (2**n)) * (0.5 + random.random()))  # noqa: S311 - jitter, not security
        except GitError as exc:
            return PublishOutcome("failed", detail=str(exc)[:300])
    return PublishOutcome("failed", detail=f"gave up after {attempts} attempts: {last_error}")


def _read_or_none(path: Path) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def superseded_finding(path: Path) -> AutomationError:
    return AutomationError(
        SUPERSEDED,
        f"submissions/{Path(path).name} on main is no longer the content that was merged; "
        "the newer change is published by its own run",
    )


def commit_message(kind: Any, name: str, version: Any, author: Author) -> str:
    return f"publish: {kind} {name} {version} by @{author.login} (id {author.id})"


# -- the shared library, through the agreed interface -------------------------


def _as_findings(exc: BaseException) -> list[Any]:
    found = getattr(exc, "findings", None)
    if isinstance(found, list) and found:
        return found
    return [AutomationError("invalid", str(exc) or exc.__class__.__name__)]


def library_validate(root: Path) -> Callable[[dict[str, Any], Author], list[Any]]:
    """Validate a submission object (already bound to its author).

    The library is imported on first use, so a run that has nothing to
    publish never depends on it.
    """

    def validate(data: dict[str, Any], author: Author) -> list[Any]:
        from . import registry as registry_mod
        from . import submission as submission_mod

        try:
            sub = submission_mod.parse_submission(data)
        except (ValueError, TypeError, KeyError) as exc:
            return _as_findings(exc)
        reg = registry_mod.load(root / "registry.json")
        return list(submission_mod.validate_submission(sub, registry=reg, author=author))

    return validate


def library_attempt(root: Path) -> AttemptFn:
    """One pipeline run on the current working tree: validate, expand, build."""
    from .automation import bind_author, loads_submission

    validate = library_validate(root)

    def attempt(sub_path: Path, author: Author) -> Attempt:
        from . import pipeline

        try:
            data = bind_author(loads_submission(Path(sub_path).read_bytes()), author, allow_claims=True)
        except AutomationError as exc:
            return Attempt(False, [exc])
        findings = validate(data, author)
        if findings:
            return Attempt(False, findings)
        try:
            pipeline.publish(Path(sub_path), root, author)
        except (ValueError, TypeError, KeyError) as exc:
            return Attempt(False, _as_findings(exc))
        return Attempt(True)

    return attempt
