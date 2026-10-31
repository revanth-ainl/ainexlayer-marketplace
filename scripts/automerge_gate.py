#!/usr/bin/env python3
"""Auto-merge gate entry point (pull_request_target, trusted base code).

Reads the pull request from ``GITHUB_EVENT_PATH``, lists its files through
the API, fetches the head commit as git objects and reads the one submission
blob as bytes. Nothing from the pull request is checked out or executed.
When ``gate.evaluate`` passes, the merge is pinned to the evaluated head
commit, then the trusted pipeline publishes from the new main.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ainexlayer_marketplace import automation, bridge, gate
from ainexlayer_marketplace.automation import AutomationError
from ainexlayer_marketplace.github_client import GitHubClient, GitHubError
from ainexlayer_marketplace.gitops import GitError, GitOps

ROOT = Path(__file__).resolve().parent.parent
RETRY_HINT = "Push a fix to this pull request to run the checks again."


def _owners(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    owners = data.get("owners") if isinstance(data, dict) else None
    return owners if isinstance(owners, dict) else {}


def run(event: dict[str, Any], *, root: Path, gh: Any, git: Any, validate: Any, attempt: Any) -> int:
    pr = event.get("pull_request")
    if not isinstance(pr, dict) or pr.get("draft"):
        return 0
    number = pr.get("number")
    head_sha = (pr.get("head") or {}).get("sha")
    if (pr.get("base") or {}).get("ref") != git.branch or not isinstance(number, int) or not isinstance(head_sha, str):
        return 0

    def refuse(findings: Any) -> int:
        gh.comment(number, automation.refused_message(findings, RETRY_HINT))
        gh.add_labels(number, [automation.LABEL_NEEDS_CHANGES])
        return 0

    try:
        author = automation.author_from_user(pr.get("user"))
    except AutomationError as exc:
        return refuse([exc])

    fetched = []
    blobs: dict[str, bytes] = {}

    def read_blob(path: str) -> bytes:
        try:
            if not fetched:
                git.fetch_pull_head(number, head_sha)
                fetched.append(True)
            blobs[path] = git.read_blob(head_sha, path, max_bytes=automation.MAX_SUBMISSION_BYTES)
            return blobs[path]
        except GitError as exc:
            raise AutomationError("files", str(exc)[:200]) from exc

    verdict = gate.evaluate(gh.list_pull_files(number), read_blob, author, _owners(root), validate=validate)
    if not verdict.eligible:
        return refuse(verdict.reasons)

    data = verdict.data or {}
    try:
        gh.merge_pull(number, head_sha, f"publish: {verdict.name} by @{author.login}")
    except GitHubError as exc:
        # 405: not mergeable (conflict, protection); 409: the head moved.
        # A new push re-runs the gate, so this is not a failure of the run.
        gh.comment(
            number,
            f"Checks passed, but GitHub did not merge this pull request ({exc.status}). "
            "It will be retried on the next push, or a maintainer can merge it.",
        )
        return 0

    message = bridge.commit_message(data.get("kind"), verdict.name or "", data.get("version"), author)
    outcome = bridge.publish_with_retry(
        root / (verdict.path or ""),
        author,
        git=git,
        attempt=attempt,
        message=message,
        expected=blobs.get(verdict.path or ""),
    )
    if outcome.status in ("published", "unchanged"):
        if outcome.sha:
            # Also on "unchanged", so a re-run after a cancelled deploy redeploys.
            automation.set_output("published_sha", outcome.sha)
        gh.comment(number, automation.published_message(verdict.name or "", data.get("version")))
        gh.add_labels(number, [automation.LABEL_PUBLISHED])
        return 0
    if outcome.status == "refused" and automation.only_superseded(outcome.findings):
        gh.comment(number, automation.superseded_message(verdict.name or ""))
        return 0
    if outcome.status == "refused":
        gh.comment(
            number,
            automation.refused_message(outcome.findings, "A maintainer needs to revert or fix the merged submission."),
        )
        gh.add_labels(number, [automation.LABEL_NEEDS_CHANGES])
        return 1
    gh.comment(number, automation.failed_message())
    gh.add_labels(number, [automation.LABEL_PUBLISH_FAILED])
    return 1


def main() -> int:
    token = os.environ.get("GH_TOKEN", "")
    gh = GitHubClient(os.environ.get("GITHUB_REPOSITORY", ""), token)
    git = GitOps(ROOT, push_token=token)
    return run(
        automation.load_event(),
        root=ROOT,
        gh=gh,
        git=git,
        validate=bridge.library_validate(ROOT),
        attempt=bridge.library_attempt(ROOT),
    )


if __name__ == "__main__":
    raise SystemExit(main())
