"""The auto-merge gate: one submission file, the PR author as publisher,
nothing from the pull request ever run."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import automation_support as support
import automerge_gate
from ainexlayer_marketplace import gate
from ainexlayer_marketplace.automation import (
    LABEL_NEEDS_CHANGES,
    LABEL_PUBLISHED,
    Author,
    AutomationError,
    PublishOutcome,
)
from ainexlayer_marketplace.bridge import Attempt
from ainexlayer_marketplace.github_client import GitHubError
from ainexlayer_marketplace.gitops import GitError

AUTHOR = Author("octo-dev", 4242)
DATA = {
    "kind": "skill",
    "name": "tidy-notes",
    "version": "1.0.0",
    "title": "Tidy",
    "description": "d",
    "skill_md": "---\nname: tidy-notes\n---\n",
}


def one(path: str = "submissions/tidy-notes.json", status: str = "added", **extra: Any) -> list[dict]:
    return [dict({"filename": path, "status": status}, **extra)]


class Recorder:
    def __init__(self, blobs: dict | None = None, findings: list | None = None) -> None:
        self.blobs = blobs if blobs is not None else {"submissions/tidy-notes.json": json.dumps(DATA).encode()}
        self.read: list[str] = []
        self.validated: list[dict] = []
        self.findings = findings or []

    def read_blob(self, path: str) -> bytes:
        self.read.append(path)
        return self.blobs[path]

    def validate(self, data: dict, author: Author) -> list:
        self.validated.append(data)
        return list(self.findings)


def evaluate(changes: list[dict], rec: Recorder, owners: dict | None = None) -> gate.Verdict:
    return gate.evaluate(changes, rec.read_blob, AUTHOR, owners or {}, validate=rec.validate)


class EvaluateTests(unittest.TestCase):
    def test_single_new_submission_is_eligible(self) -> None:
        rec = Recorder()
        verdict = evaluate(one(), rec)
        self.assertTrue(verdict.eligible, verdict.reasons)
        self.assertEqual(verdict.name, "tidy-notes")
        self.assertEqual((rec.validated[0]["publisher"], rec.validated[0]["publisher_id"]), ("octo-dev", 4242))

    def test_modified_submission_is_eligible_for_a_registered_name(self) -> None:
        owners = {"tidy-notes": {"publisher": "octo-dev", "publisher_id": 4242}}
        self.assertTrue(evaluate(one(status="modified"), Recorder(), owners).eligible)

    def test_pending_submission_cannot_be_rewritten_by_another_pull_request(self) -> None:
        rec = Recorder()
        verdict = evaluate(one(status="modified"), rec)
        self.assertFalse(verdict.eligible)
        self.assertEqual(verdict.reasons[0].code, "owner")
        self.assertEqual(rec.read, [])

    def test_shape_refusals_never_read_the_pull_request(self) -> None:
        cases = [
            one() + one("submissions/other.json"),
            one("scripts/validate.py", "modified"),
            one(".github/workflows/ci.yml", "modified"),
            one("trusted-publishers.json", "modified"),
            one("registry.json", "modified"),
            one("docs/index.json", "modified"),
            one("submissions/../scripts/x.json"),
            one("submissions/Tidy.json"),
            one("submissions/nested/tidy.json"),
            one(status="removed"),
            one(status="renamed", previous_filename="submissions/old.json"),
            [],
        ]
        for changes in cases:
            rec = Recorder()
            verdict = evaluate(changes, rec)
            self.assertFalse(verdict.eligible, changes)
            self.assertEqual(rec.read, [], changes)
            self.assertEqual(rec.validated, [], changes)

    def test_name_must_match_file(self) -> None:
        rec = Recorder({"submissions/tidy-notes.json": json.dumps(dict(DATA, name="other")).encode()})
        self.assertFalse(evaluate(one(), rec).eligible)

    def test_publisher_claims_must_name_the_author(self) -> None:
        for claim in ({"publisher": "someone-else"}, {"publisher_id": 1}, {"publisher_id": True}):
            rec = Recorder({"submissions/tidy-notes.json": json.dumps(dict(DATA, **claim)).encode()})
            verdict = evaluate(one(), rec)
            self.assertFalse(verdict.eligible, claim)
            self.assertEqual(rec.validated, [])

    def test_matching_claims_are_accepted_case_insensitively(self) -> None:
        rec = Recorder(
            {"submissions/tidy-notes.json": json.dumps(dict(DATA, publisher="Octo-Dev", publisher_id=4242)).encode()}
        )
        self.assertTrue(evaluate(one(), rec).eligible)

    def test_maintainer_managed_names_are_refused(self) -> None:
        for entry in (
            {"publisher": "x", "publisher_id": None},
            {"publisher": "x"},
            {"publisher": "x", "publisher_id": 5, "source": "maintainer"},
        ):
            rec = Recorder()
            self.assertFalse(evaluate(one(), rec, {"tidy-notes": entry}).eligible, entry)
            self.assertEqual(rec.read, [])

    def test_validator_findings_block_the_merge(self) -> None:
        verdict = evaluate(one(), Recorder(findings=[AutomationError("version", "must increase")]))
        self.assertFalse(verdict.eligible)
        self.assertEqual(verdict.reasons[0].code, "version")

    def test_bad_json_and_oversize_are_refused(self) -> None:
        for raw in (b"{not json", b"[]", b"\xff\xfe", b" " * (300 * 1024)):
            self.assertFalse(evaluate(one(), Recorder({"submissions/tidy-notes.json": raw})).eligible)

    def test_pull_request_content_is_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "executed"
            payload = f"__import__('pathlib').Path({str(sentinel)!r}).write_text('x')".encode()
            verdict = evaluate(one(), Recorder({"submissions/tidy-notes.json": payload}))
            self.assertFalse(verdict.eligible)
            self.assertFalse(sentinel.exists())


class GateGit:
    remote = "origin"
    branch = "main"

    def __init__(
        self, blob: bytes | None = None, fetch_error: Exception | None = None, main_blob: bytes | None = None
    ) -> None:
        self.blob = blob if blob is not None else json.dumps(DATA).encode()
        # What submissions/tidy-notes.json holds on main after the merge.
        self.main_blob = main_blob if main_blob is not None else self.blob
        self.fetch_error = fetch_error
        self.after_reset: list = []
        self.fetched: list = []

    def fetch_pull_head(self, number: int, sha: str) -> str:
        if self.fetch_error:
            raise self.fetch_error
        self.fetched.append((number, sha))
        return sha

    def read_blob(self, sha: str, path: str, *, max_bytes: int) -> bytes:
        return self.blob


def pr_event(**pr: Any) -> dict:
    data = {"number": 12, "draft": False, "head": {"sha": "b" * 40}, "base": {"ref": "main"}, "user": support.user()}
    data.update(pr)
    return {"action": "opened", "pull_request": data}


class AutomergeRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "registry.json").write_text(json.dumps({"version": 2, "owners": {}}), encoding="utf-8")
        self.outcomes: list[PublishOutcome] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_gate(
        self,
        event: dict,
        gh: support.FakeGitHub,
        git: GateGit,
        findings: list | None = None,
        attempt_ok: bool = True,
        commit_sha: str = "d" * 40,
    ) -> int:
        def validate(data: dict, author: Author) -> list:
            return list(findings or [])

        def attempt(path: Path, author: Author) -> Attempt:
            return Attempt(attempt_ok, [] if attempt_ok else [AutomationError("owner", "claimed meanwhile")])

        git.fetch_branch = lambda: "c" * 40  # type: ignore[attr-defined]

        def reset_to(ref: str) -> None:
            target = self.root / "submissions" / "tidy-notes.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git.main_blob)

        git.reset_to = reset_to  # type: ignore[attr-defined]
        git.commit = lambda paths, message: commit_sha  # type: ignore[attr-defined]
        if not hasattr(git, "push"):
            git.push = lambda: "d" * 40  # type: ignore[attr-defined]
        git.head = lambda: "d" * 40  # type: ignore[attr-defined]
        return automerge_gate.run(event, root=self.root, gh=gh, git=git, validate=validate, attempt=attempt)

    def test_eligible_pull_request_merges_at_the_evaluated_head(self) -> None:
        gh = support.FakeGitHub(files=one())
        self.assertEqual(self.run_gate(pr_event(), gh, GateGit()), 0)
        self.assertEqual(gh.merged_with, ["b" * 40])
        self.assertIn(LABEL_PUBLISHED, gh.labels_added())

    def test_content_replaced_on_main_after_the_merge_is_not_published(self) -> None:
        gh = support.FakeGitHub(files=one())
        other = json.dumps(dict(DATA, description="someone else's text")).encode()
        published = []
        git = GateGit(main_blob=other)
        git.push = lambda: published.append(1) or "d" * 40  # type: ignore[attr-defined]
        self.assertEqual(self.run_gate(pr_event(), gh, git), 0)
        self.assertEqual(published, [])
        self.assertNotIn(LABEL_PUBLISHED, gh.labels_added())
        self.assertIn("changed again on main", gh.comments()[-1])

    def test_unchanged_still_hands_pages_a_commit(self) -> None:
        gh = support.FakeGitHub(files=one())
        git = GateGit()
        with tempfile.NamedTemporaryFile("r", suffix=".out") as out:
            with mock.patch.dict("os.environ", {"GITHUB_OUTPUT": out.name}):
                self.run_gate(pr_event(), gh, git, commit_sha=None)
            self.assertEqual(out.read().strip(), "published_sha=" + "d" * 40)

    def test_ineligible_pull_request_is_not_merged(self) -> None:
        gh = support.FakeGitHub(files=one() + one("scripts/evil.py"))
        self.assertEqual(self.run_gate(pr_event(), gh, GateGit()), 0)
        self.assertEqual(gh.merged_with, [])
        self.assertIn(LABEL_NEEDS_CHANGES, gh.labels_added())

    def test_validator_refusal_is_not_merged(self) -> None:
        gh = support.FakeGitHub(files=one())
        self.run_gate(pr_event(), gh, GateGit(), findings=[AutomationError("secret", "looks like a credential")])
        self.assertEqual(gh.merged_with, [])

    def test_head_moved_while_checking_is_not_merged(self) -> None:
        gh = support.FakeGitHub(files=one())
        self.run_gate(pr_event(), gh, GateGit(fetch_error=GitError("the pull request head moved")))
        self.assertEqual(gh.merged_with, [])

    def test_bot_and_draft_pull_requests_are_skipped(self) -> None:
        gh = support.FakeGitHub(files=one())
        self.run_gate(pr_event(user=support.user("deps[bot]", 3, "Bot")), gh, GateGit())
        self.run_gate(pr_event(draft=True), gh, GateGit())
        self.run_gate(pr_event(base={"ref": "dev"}), gh, GateGit())
        self.assertEqual(gh.merged_with, [])

    def test_merge_refused_by_github_is_reported_not_failed(self) -> None:
        gh = support.FakeGitHub(files=one())
        gh.merge_error = GitHubError(409, "Head branch was modified")
        self.assertEqual(self.run_gate(pr_event(), gh, GateGit()), 0)
        self.assertNotIn(LABEL_PUBLISHED, gh.labels_added())

    def test_claim_lost_after_merge_is_visible(self) -> None:
        gh = support.FakeGitHub(files=one())
        self.assertEqual(self.run_gate(pr_event(), gh, GateGit(), attempt_ok=False), 1)
        self.assertIn(LABEL_NEEDS_CHANGES, gh.labels_added())


if __name__ == "__main__":
    unittest.main()
