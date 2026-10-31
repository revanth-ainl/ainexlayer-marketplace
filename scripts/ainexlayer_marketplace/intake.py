"""Issue intake: turn a publish issue into a published submission.

The issue body is untrusted text. It is parsed, never executed, and only the
single fenced ``json`` block is read. The publisher is the issue's author as
GitHub authenticated it; publisher fields in the body are overwritten.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple

from . import compat
from .automation import (
    LABEL_NEEDS_CHANGES,
    LABEL_PUBLISH_FAILED,
    LABEL_PUBLISHED,
    AutomationError,
    PublishFn,
    author_from_user,
    bind_author,
    failed_message,
    loads_submission,
    published_message,
    refused_message,
    write_submission,
)

MAX_BODY_BYTES = 256 * 1024
# A fence opens at the start of a line with ```json and closes at the next
# line that is only ```. JSON text cannot contain a raw newline inside a
# string, so a fence inside the submission's own strings cannot end a block.
_FENCE_RE = re.compile(r"^```[ \t]*json[ \t]*\r?\n(.*?)\r?\n```[ \t]*$", re.MULTILINE | re.DOTALL | re.IGNORECASE)
_ANY_FENCE_RE = re.compile(r"^```", re.MULTILINE)
RETRY_HINT = "Edit this issue (the JSON block) to run the checks again."


class IntakeResult(NamedTuple):
    status: str  # published | unchanged | refused | failed | ignored
    sha: str | None = None


def is_intake_issue(title: Any, body: Any) -> bool:
    title_ok = isinstance(title, str) and title.strip().lower().startswith(compat.INTAKE_TITLE_TAG.lower())
    marker_ok = isinstance(body, str) and compat.INTAKE_BODY_MARKER in body
    return title_ok or marker_ok


def parse_issue(title: Any, body: Any) -> dict[str, Any]:
    """Return the submission object carried by a publish issue.

    Requires the title tag and exactly one ```json block. The body marker is
    optional, so an issue from the web form is read the same way.
    """
    if not isinstance(title, str) or not title.strip().lower().startswith(compat.INTAKE_TITLE_TAG.lower()):
        raise AutomationError("title", f"the issue title must start with {compat.INTAKE_TITLE_TAG}")
    if not isinstance(body, str) or not body.strip():
        raise AutomationError("body", "the issue body is empty")
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise AutomationError("size", f"the issue body is larger than {MAX_BODY_BYTES} bytes")
    blocks = _FENCE_RE.findall(body)
    if len(blocks) != 1:
        found = "no" if not blocks else str(len(blocks))
        raise AutomationError("body", f"the issue must contain exactly one ```json block; found {found}")
    # A stray unclosed fence elsewhere would make the reading ambiguous.
    if len(_ANY_FENCE_RE.findall(body)) != 2:
        raise AutomationError("body", "the issue body has extra ``` fences; keep only the one json block")
    return loads_submission(blocks[0].encode("utf-8"))


def _edited_by_author(event: dict[str, Any], issue: dict[str, Any]) -> bool:
    sender, user = event.get("sender"), issue.get("user")
    if not isinstance(sender, dict) or not isinstance(user, dict):
        return False
    sid, uid = sender.get("id"), user.get("id")
    return isinstance(sid, int) and not isinstance(sid, bool) and sid == uid


def handle_issue(event: dict[str, Any], *, root: Path, gh: Any, git: Any, publish: PublishFn) -> IntakeResult:
    issue = event.get("issue")
    if not isinstance(issue, dict) or issue.get("pull_request") is not None:
        return IntakeResult("ignored")
    number = issue.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        return IntakeResult("ignored")
    title, body = issue.get("title"), issue.get("body")
    if not is_intake_issue(title, body):
        return IntakeResult("ignored")
    if event.get("action") == "edited" and not _edited_by_author(event, issue):
        # Someone else (a maintainer) edited the issue; publishing now would
        # put their text under the author's name.
        return IntakeResult("ignored")

    def refuse(findings: Any) -> IntakeResult:
        gh.comment(number, refused_message(findings, RETRY_HINT))
        gh.remove_label(number, LABEL_PUBLISHED)
        gh.add_labels(number, [LABEL_NEEDS_CHANGES])
        return IntakeResult("refused")

    try:
        author = author_from_user(issue.get("user"))
        data = bind_author(parse_issue(title, body), author, allow_claims=False)
        path = write_submission(root, data)
    except AutomationError as exc:
        return refuse([exc])

    # A retry starts again from a fresh main, which does not have this file.
    git.after_reset.append(lambda: write_submission(root, data))
    outcome = publish(path, author)

    if outcome.status in ("published", "unchanged"):
        gh.comment(number, published_message(data["name"], data.get("version")))
        gh.remove_label(number, LABEL_NEEDS_CHANGES)
        gh.remove_label(number, LABEL_PUBLISH_FAILED)
        gh.add_labels(number, [LABEL_PUBLISHED])
        gh.close_issue(number)
        return IntakeResult(outcome.status, outcome.sha)
    if outcome.status == "refused":
        return refuse(outcome.findings)
    gh.comment(number, failed_message())
    gh.add_labels(number, [LABEL_PUBLISH_FAILED])
    return IntakeResult("failed")
