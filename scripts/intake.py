#!/usr/bin/env python3
"""Publish a submission that arrived as an issue (intake workflow entry point).

Runs from trusted default-branch code on the ``issues`` event. Reads the
event from ``GITHUB_EVENT_PATH``; see ``ainexlayer_marketplace/intake.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ainexlayer_marketplace import automation, bridge, intake
from ainexlayer_marketplace.github_client import GitHubClient
from ainexlayer_marketplace.gitops import GitOps

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    event = automation.load_event()
    token = os.environ.get("GH_TOKEN", "")
    gh = GitHubClient(os.environ.get("GITHUB_REPOSITORY", ""), token)
    git = GitOps(ROOT, push_token=token)
    attempt = bridge.library_attempt(ROOT)

    def publish(path: Path, author: automation.Author) -> automation.PublishOutcome:
        data = automation.loads_submission(path.read_bytes())
        message = bridge.commit_message(data.get("kind"), path.stem, data.get("version"), author)
        return bridge.publish_with_retry(path, author, git=git, attempt=attempt, message=message)

    result = intake.handle_issue(event, root=ROOT, gh=gh, git=git, publish=publish)
    print(f"intake: {result.status}")
    if result.status in ("published", "unchanged") and result.sha:
        automation.set_output("published_sha", result.sha)
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
