"""The push-to-main publish path and the GitHub client, with GitHub mocked."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import automation_support as support
import publish as publish_script
from ainexlayer_marketplace.automation import LABEL_NEEDS_CHANGES, AutomationError
from ainexlayer_marketplace.bridge import Attempt
from ainexlayer_marketplace.github_client import GitHubClient, GitHubError

SHA = "a" * 40
BEFORE = "b" * 40
DATA = {"kind": "skill", "name": "tidy-notes", "version": "1.0.0"}


class PushGit:
    remote = "origin"
    branch = "main"

    def __init__(
        self,
        changes: list[tuple],
        on_main: bool = True,
        merged: bytes | None = None,
        main: bytes | None = None,
        commit: str = "c" * 40,
    ) -> None:
        self.changes = changes
        self.on_main = on_main
        self.after_reset: list = []
        self.pushed: list[str] = []
        # The submission in the merge commit, and what main holds when the run resets.
        self.merged = merged if merged is not None else json.dumps(DATA).encode()
        self.main = main if main is not None else self.merged
        self.commit_sha = commit
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def fetch_branch(self) -> str:
        return SHA

    def is_ancestor(self, older: str, newer: str) -> bool:
        return self.on_main

    def changed_files(self, base: str, head: str, prefix: str) -> list[tuple]:
        return list(self.changes)

    def read_blob(self, sha: str, path: str, *, max_bytes: int) -> bytes:
        return self.merged

    def reset_to(self, ref: str) -> None:
        for _, path in self.changes:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.main)

    def commit(self, paths: Any, message: str) -> str:
        return self.commit_sha

    def push(self) -> str:
        self.pushed.append("c" * 40)
        return "c" * 40

    def head(self) -> str:
        return "c" * 40


def merged_pr(**extra: Any) -> dict[str, Any]:
    pr = {
        "number": 5,
        "merged_at": "2026-10-31T00:00:00Z",
        "merge_commit_sha": SHA,
        "base": {"ref": "main"},
        "user": support.user(),
    }
    pr.update(extra)
    return pr


def run(gh: support.FakeGitHub, git: PushGit, event: dict[str, Any] | None = None, ok: bool = True) -> int:
    def attempt(path: Path, author: Any) -> Attempt:
        return Attempt(ok, [] if ok else [AutomationError("version", "must increase")])

    return publish_script.run(event or {"before": BEFORE, "after": SHA}, root=git.root, gh=gh, git=git, attempt=attempt)


class PublishScriptTests(unittest.TestCase):
    def test_merged_pull_request_publishes_as_its_author(self) -> None:
        gh = support.FakeGitHub(files=[{"filename": "submissions/tidy-notes.json"}], pulls=[merged_pr()])
        git = PushGit([("A", "submissions/tidy-notes.json")])
        self.assertEqual(run(gh, git), 0)
        self.assertEqual(git.pushed, ["c" * 40])

    def test_a_later_change_on_main_is_not_published_under_this_author(self) -> None:
        gh = support.FakeGitHub(files=[{"filename": "submissions/tidy-notes.json"}], pulls=[merged_pr()])
        later = json.dumps(dict(DATA, description="replaced by another account")).encode()
        git = PushGit([("A", "submissions/tidy-notes.json")], main=later)
        self.assertEqual(run(gh, git), 0)
        self.assertEqual(git.pushed, [])
        self.assertIn("changed again on main", gh.comments()[-1])
        self.assertNotIn(LABEL_NEEDS_CHANGES, gh.labels_added())

    def test_unreadable_merged_submission_is_reported_on_the_pull_request(self) -> None:
        gh = support.FakeGitHub(files=[{"filename": "submissions/tidy-notes.json"}], pulls=[merged_pr()])
        git = PushGit([("A", "submissions/tidy-notes.json")], merged=b"{not json")
        self.assertEqual(run(gh, git), 1)
        self.assertIn("not valid JSON", gh.comments()[-1])
        self.assertIn(LABEL_NEEDS_CHANGES, gh.labels_added())

    def test_unchanged_still_hands_pages_a_commit(self) -> None:
        gh = support.FakeGitHub(files=[{"filename": "submissions/tidy-notes.json"}], pulls=[merged_pr()])
        git = PushGit([("M", "submissions/tidy-notes.json")], commit=None)
        with tempfile.NamedTemporaryFile("r", suffix=".out") as out:
            with mock.patch.dict("os.environ", {"GITHUB_OUTPUT": out.name}):
                self.assertEqual(run(gh, git), 0)
            self.assertEqual(out.read().strip(), "published_sha=" + "c" * 40)

    def test_direct_push_without_pull_request_is_refused(self) -> None:
        gh = support.FakeGitHub(pulls=[])
        git = PushGit([("A", "submissions/tidy-notes.json")])
        self.assertEqual(run(gh, git), 1)
        self.assertEqual(git.pushed, [])

    def test_pull_request_merged_elsewhere_is_ignored(self) -> None:
        gh = support.FakeGitHub(pulls=[merged_pr(merge_commit_sha="e" * 40)])
        self.assertEqual(run(gh, PushGit([("A", "submissions/tidy-notes.json")])), 1)

    def test_file_outside_the_pull_request_is_refused(self) -> None:
        gh = support.FakeGitHub(files=[{"filename": "submissions/other.json"}], pulls=[merged_pr()])
        git = PushGit([("A", "submissions/tidy-notes.json")])
        self.assertEqual(run(gh, git), 1)
        self.assertEqual(git.pushed, [])
        self.assertIn(LABEL_NEEDS_CHANGES, gh.labels_added())

    def test_validator_refusal_after_merge_is_visible(self) -> None:
        gh = support.FakeGitHub(files=[{"filename": "submissions/tidy-notes.json"}], pulls=[merged_pr()])
        self.assertEqual(run(gh, PushGit([("M", "submissions/tidy-notes.json")]), ok=False), 1)
        self.assertIn("must increase", gh.comments()[-1])

    def test_no_submission_changes_is_a_no_op(self) -> None:
        gh = support.FakeGitHub()
        self.assertEqual(run(gh, PushGit([("M", "submissions/README.md")])), 0)
        self.assertEqual(gh.calls, [])

    def test_dispatch_needs_a_commit_on_main(self) -> None:
        gh = support.FakeGitHub()
        self.assertEqual(run(gh, PushGit([]), event={"inputs": {"sha": "not-a-sha"}}), 1)
        self.assertEqual(run(gh, PushGit([], on_main=False), event={"inputs": {"sha": SHA}}), 1)


class FakeTransport:
    def __init__(self, responses: list[tuple]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], body: Any) -> tuple:
        self.requests.append((method, url, headers, body))
        status, data = self.responses.pop(0)
        return status, {}, json.dumps(data).encode()


class GitHubClientTests(unittest.TestCase):
    TOKEN = "ghs" + "_" + "x" * 30

    def client(self, responses: list[tuple]) -> tuple:
        transport = FakeTransport(responses)
        return GitHubClient("ainexlayer/registry", self.TOKEN, transport=transport), transport

    def test_errors_never_carry_the_token(self) -> None:
        gh, _ = self.client([(403, {"message": f"bad credentials {self.TOKEN}"[:20]})])
        with self.assertRaises(GitHubError) as ctx:
            gh.comment(1, "hi")
        self.assertNotIn(self.TOKEN, str(ctx.exception))
        self.assertNotIn(self.TOKEN, repr(gh))

    def test_pagination(self) -> None:
        gh, transport = self.client([(200, [{"filename": str(i)} for i in range(100)]), (200, [{"filename": "last"}])])
        self.assertEqual(len(gh.list_pull_files(3)), 101)
        self.assertIn("page=2", transport.requests[1][1])

    def test_merge_is_pinned_to_the_head_sha(self) -> None:
        gh, transport = self.client([(200, {"sha": "d" * 40, "merged": True})])
        self.assertEqual(gh.merge_pull(3, "b" * 40, "t"), "d" * 40)
        self.assertEqual(json.loads(transport.requests[0][3])["sha"], "b" * 40)
        with self.assertRaises(ValueError):
            gh.merge_pull(3, "main", "t")

    def test_bad_repository_names_refused(self) -> None:
        for repo in ("", "no-slash", "a/b/c", "../x"):
            with self.assertRaises(ValueError):
                GitHubClient(repo, self.TOKEN)


if __name__ == "__main__":
    unittest.main()
