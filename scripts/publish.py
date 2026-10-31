#!/usr/bin/env python3
"""Publish submissions merged into main by a maintainer (publish workflow).

The publisher of each changed ``submissions/<name>.json`` is the author of
the merged pull request whose merge commit is the pushed commit. A file that
reached main any other way (a direct push, or a file outside that pull
request) has no verifiable publisher and is refused.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ainexlayer_marketplace import automation, bridge
from ainexlayer_marketplace.automation import SUBMISSION_PATH_RE, AutomationError
from ainexlayer_marketplace.github_client import GitHubClient
from ainexlayer_marketplace.gitops import GitError, GitOps

ROOT = Path(__file__).resolve().parent.parent
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ZERO = "0" * 40


def _target(event: dict[str, Any]) -> tuple | None:
    inputs = event.get("inputs")
    if isinstance(inputs, dict):
        sha = str(inputs.get("sha") or "").strip().lower()
        return (f"{sha}^", sha) if _SHA_RE.fullmatch(sha) else None
    after, before = event.get("after"), event.get("before")
    if not isinstance(after, str) or not _SHA_RE.fullmatch(after):
        return None
    base = before if isinstance(before, str) and _SHA_RE.fullmatch(before) and before != _ZERO else f"{after}^"
    return (base, after)


def run(event: dict[str, Any], *, root: Path, gh: Any, git: Any, attempt: Any) -> int:
    target = _target(event)
    if target is None:
        print("publish: no valid commit in the event", file=sys.stderr)
        return 1
    base, sha = target
    git.fetch_branch()
    if not git.is_ancestor(sha, f"{git.remote}/{git.branch}"):
        print("publish: the commit is not on main", file=sys.stderr)
        return 1
    subs = [(st, p) for st, p in git.changed_files(base, sha, "submissions") if SUBMISSION_PATH_RE.fullmatch(p)]
    if not subs:
        print("publish: no submission changes")
        return 0

    merged = [
        pr
        for pr in gh.pulls_for_commit(sha)
        if pr.get("merged_at") and pr.get("merge_commit_sha") == sha and (pr.get("base") or {}).get("ref") == git.branch
    ]
    if len(merged) != 1:
        print(
            "publish: refused — these submission changes did not arrive through one merged pull request, "
            "so there is no verified publisher: " + ", ".join(p for _, p in subs),
            file=sys.stderr,
        )
        return 1
    number = merged[0]["number"]
    try:
        author = automation.author_from_user(merged[0].get("user"))
    except AutomationError as exc:
        gh.comment(number, automation.refused_message([exc], "A maintainer needs to revert this submission."))
        return 1
    in_pr = {f.get("filename") for f in gh.list_pull_files(number)}

    problems: list[Any] = []
    published_sha = None
    for status, path in subs:
        name = SUBMISSION_PATH_RE.fullmatch(path).group(1)
        if path not in in_pr:
            problems.append(AutomationError("files", f"{path} is not part of pull request #{number}"))
            continue
        if status == "D":
            problems.append(AutomationError("delist", f"{name}: removing a submission needs a maintainer to delist it"))
            continue
        sub_path = root / path
        try:
            # The merged content, pinned: a later change to the same file on
            # main must not be published under this pull request's author.
            merged_bytes = git.read_blob(sha, path, max_bytes=automation.MAX_SUBMISSION_BYTES)
            data = automation.loads_submission(merged_bytes)
        except (AutomationError, GitError) as exc:
            problems.append(exc if isinstance(exc, AutomationError) else AutomationError("files", str(exc)[:200]))
            continue
        message = bridge.commit_message(data.get("kind"), name, data.get("version"), author)
        outcome = bridge.publish_with_retry(
            sub_path, author, git=git, attempt=attempt, message=message, expected=merged_bytes
        )
        if outcome.status == "published":
            published_sha = outcome.sha
            gh.comment(number, automation.published_message(name, data.get("version")))
        elif outcome.status == "unchanged":
            # Already live; still hand Pages a commit so a re-run redeploys.
            published_sha = published_sha or outcome.sha
        elif outcome.status == "refused" and automation.only_superseded(outcome.findings):
            gh.comment(number, automation.superseded_message(name))
        elif outcome.status == "refused":
            problems.extend(outcome.findings)
        else:
            gh.comment(number, automation.failed_message())
            gh.add_labels(number, [automation.LABEL_PUBLISH_FAILED])
            return 1

    if published_sha:
        automation.set_output("published_sha", published_sha)
    if problems:
        gh.comment(
            number, automation.refused_message(problems, "A maintainer needs to revert or fix the merged submission.")
        )
        gh.add_labels(number, [automation.LABEL_NEEDS_CHANGES])
        return 1
    return 0


def main() -> int:
    token = os.environ.get("GH_TOKEN", "")
    gh = GitHubClient(os.environ.get("GITHUB_REPOSITORY", ""), token)
    git = GitOps(ROOT, push_token=token)
    return run(automation.load_event(), root=ROOT, gh=gh, git=git, attempt=bridge.library_attempt(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
