"""Test helpers for the publishing automation (intake, gate, publish).

Puts ``scripts/`` on ``sys.path`` and provides fakes for GitHub and for the
pipeline, so no test talks to the network or to the real repository.
"""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:  # the shared constants module, when the library provides it
    from ainexlayer_marketplace import compat  # noqa: F401
except ImportError:  # neutral stand-in so these tests run on their own
    stub = types.ModuleType("ainexlayer_marketplace.compat")
    stub.INTAKE_TITLE_TAG = "[publish]"
    stub.INTAKE_BODY_MARKER = "<!-- test-intake-marker -->"
    sys.modules["ainexlayer_marketplace.compat"] = stub
    import ainexlayer_marketplace

    ainexlayer_marketplace.compat = stub  # type: ignore[attr-defined]

from ainexlayer_marketplace import compat as COMPAT  # noqa: F401


class FakeGitHub:
    """Records every call; returns canned data for reads."""

    def __init__(self, files: list[dict[str, Any]] | None = None, pulls: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple] = []
        self.files = files or []
        self.pulls = pulls or []
        self.merge_error: Exception = None
        self.merged_with: list[str] = []

    def comment(self, number: int, body: str) -> None:
        self.calls.append(("comment", number, body))

    def add_labels(self, number: int, labels: list[str]) -> None:
        self.calls.append(("add_labels", number, tuple(labels)))

    def remove_label(self, number: int, label: str) -> None:
        self.calls.append(("remove_label", number, label))

    def close_issue(self, number: int) -> None:
        self.calls.append(("close_issue", number))

    def list_pull_files(self, number: int) -> list[dict[str, Any]]:
        self.calls.append(("list_pull_files", number))
        return list(self.files)

    def pulls_for_commit(self, sha: str) -> list[dict[str, Any]]:
        self.calls.append(("pulls_for_commit", sha))
        return list(self.pulls)

    def merge_pull(self, number: int, head_sha: str, title: str) -> str:
        self.calls.append(("merge_pull", number, head_sha))
        if self.merge_error is not None:
            raise self.merge_error
        self.merged_with.append(head_sha)
        return "f" * 40

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def labels_added(self) -> list[str]:
        return [label for c in self.calls if c[0] == "add_labels" for label in c[2]]

    def comments(self) -> list[str]:
        return [c[2] for c in self.calls if c[0] == "comment"]


class FakeGit:
    """Just enough of GitOps for code that only registers reset callbacks."""

    remote = "origin"
    branch = "main"

    def __init__(self) -> None:
        self.after_reset: list[Any] = []


def user(login: str = "octo-dev", uid: int = 4242, kind: str = "User") -> dict[str, Any]:
    return {"login": login, "id": uid, "type": kind}


def git(cwd: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.com",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
    )
    proc = subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True, check=True)
    return proc.stdout.decode().strip()
