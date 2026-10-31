"""Two writers racing on one registry, against a real local git remote.

No network: the "remote" is a bare repository in a temporary folder.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

import automation_support as support
from ainexlayer_marketplace.automation import Author, AutomationError
from ainexlayer_marketplace.bridge import Attempt, publish_with_retry
from ainexlayer_marketplace.gitops import GitError, GitOps, PushRejected

ALICE = Author("alice-dev", 101)
BOB = Author("bob-dev", 202)


def claim_attempt(root: Path, name: str, before: Callable[[], None] | None = None):
    """A stand-in pipeline: claim ``name`` in registry.json and write a skill.

    ``before`` runs once, in the middle of the attempt, to let another writer
    publish first (the race).
    """
    state = {"before": before}

    def attempt(sub_path: Path, author: Author) -> Attempt:
        if state["before"]:
            hook, state["before"] = state["before"], None
            hook()
        registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
        entry = registry["owners"].get(name)
        if entry and entry["publisher_id"] != author.id:
            return Attempt(False, [AutomationError("owner", f"{name} belongs to another publisher")])
        registry["owners"][name] = {"publisher": author.login, "publisher_id": author.id}
        (root / "registry.json").write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        skill = root / "skills" / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        return Attempt(True)

    return attempt


class RemoteFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.remote = base / "remote.git"
        support.git(base, "init", "--bare", "-b", "main", str(self.remote))
        seed = base / "seed"
        support.git(base, "clone", "-q", str(self.remote), str(seed))
        (seed / "registry.json").write_text(json.dumps({"version": 2, "owners": {}}) + "\n", encoding="utf-8")
        support.git(seed, "add", "registry.json")
        support.git(seed, "commit", "-qm", "seed")
        support.git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
        self.a = base / "writer-a"
        self.b = base / "writer-b"
        for path in (self.a, self.b):
            support.git(base, "clone", "-q", str(self.remote), str(path))
        self.sleeps: list[float] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def ops(self, root: Path) -> GitOps:
        return GitOps(root, allow_reset=True)

    def publish(self, root: Path, name: str, author: Author, before=None, attempts: int = 5):
        return publish_with_retry(
            root / "submissions" / f"{name}.json",
            author,
            git=self.ops(root),
            attempt=claim_attempt(root, name, before),
            message=f"publish {name}",
            attempts=attempts,
            sleep=self.sleeps.append,
        )

    def remote_registry(self) -> dict:
        check = Path(self._tmp.name) / "check"
        support.git(Path(self._tmp.name), "clone", "-q", str(self.remote), str(check))
        return json.loads((check / "registry.json").read_text(encoding="utf-8"))["owners"]


class RaceTests(RemoteFixture):
    def test_different_names_both_publish(self) -> None:
        outcome = self.publish(self.b, "bob-skill", BOB, before=lambda: self.publish(self.a, "alice-skill", ALICE))
        self.assertEqual(outcome.status, "published")
        self.assertEqual(len(self.sleeps), 1, "bob lost exactly one race and retried")
        self.assertEqual(set(self.remote_registry()), {"alice-skill", "bob-skill"})

    def test_same_name_second_writer_is_refused(self) -> None:
        outcome = self.publish(self.b, "shared-name", BOB, before=lambda: self.publish(self.a, "shared-name", ALICE))
        self.assertEqual(outcome.status, "refused")
        self.assertEqual(outcome.findings[0].code, "owner")
        self.assertEqual(self.remote_registry()["shared-name"]["publisher_id"], ALICE.id)

    def test_retries_run_out_visibly(self) -> None:
        counter = {"n": 0}

        def always_first():
            counter["n"] += 1
            self.publish(self.a, f"alice-{counter['n']}", ALICE)

        ops = self.ops(self.b)
        state = {"calls": 0}
        inner = claim_attempt(self.b, "bob-skill")

        def attempt(sub_path: Path, author: Author) -> Attempt:
            state["calls"] += 1
            always_first()
            return inner(sub_path, author)

        outcome = publish_with_retry(
            self.b / "x", BOB, git=ops, attempt=attempt, message="m", attempts=3, sleep=self.sleeps.append
        )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(state["calls"], 3)
        self.assertNotIn("bob-skill", self.remote_registry())

    def test_unchanged_when_nothing_to_commit(self) -> None:
        self.assertEqual(self.publish(self.a, "alice-skill", ALICE).status, "published")
        self.assertEqual(self.publish(self.a, "alice-skill", ALICE).status, "unchanged")

    def test_reset_reruns_callbacks(self) -> None:
        ops = self.ops(self.a)
        hits = []
        ops.after_reset.append(lambda: hits.append(1))
        ops.fetch_branch()
        ops.reset_to("origin/main")
        self.assertEqual(hits, [1])


class IntakeEndToEndTests(RemoteFixture):
    """Issue → intake → retry → push, with a writer that wins the first race."""

    def test_intake_submission_survives_a_lost_race(self) -> None:
        from ainexlayer_marketplace import intake

        ops = self.ops(self.b)
        gh = support.FakeGitHub()
        rival = {"done": False}
        inner = claim_attempt(self.b, "bob-skill")

        def attempt(sub_path: Path, author: Author) -> Attempt:
            if not rival["done"]:
                rival["done"] = True
                self.publish(self.a, "alice-skill", ALICE)
            self.assertTrue(sub_path.is_file(), "the submission must be back after every reset")
            return inner(sub_path, author)

        def publish(path: Path, author: Author):
            return publish_with_retry(
                path, author, git=ops, attempt=attempt, message="publish bob-skill", sleep=self.sleeps.append
            )

        body = "```json\n" + json.dumps({"kind": "skill", "name": "bob-skill", "version": "1.0.0"}) + "\n```"
        event = {
            "issue": {
                "number": 3,
                "title": f"{support.COMPAT.INTAKE_TITLE_TAG} skill: bob-skill",
                "body": body,
                "user": support.user("bob-dev", 202),
            }
        }
        result = intake.handle_issue(event, root=self.b, gh=gh, git=ops, publish=publish)

        self.assertEqual(result.status, "published")
        self.assertEqual(len(self.sleeps), 1)
        self.assertEqual(result.sha, support.git(self.remote, "rev-parse", "main"))
        files = support.git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertIn("submissions/bob-skill.json", files)
        self.assertIn("skills/alice-skill/SKILL.md", files)
        stored = json.loads(support.git(self.remote, "show", "main:submissions/bob-skill.json"))
        self.assertEqual((stored["publisher"], stored["publisher_id"]), ("bob-dev", 202))
        self.assertIn("close_issue", gh.names())


class MergedContentPinnedTests(RemoteFixture):
    """A later change to the same submission must not ride on an earlier author's merge."""

    def merge(self, maint: Path, description: str) -> str:
        sub = maint / "submissions" / "shared-tool.json"
        sub.parent.mkdir(exist_ok=True)
        sub.write_text(
            json.dumps({"kind": "skill", "name": "shared-tool", "version": "1.0.0", "description": description})
        )
        support.git(maint, "add", "submissions")
        support.git(maint, "commit", "-qm", description)
        support.git(maint, "push", "-q", "origin", "HEAD:refs/heads/main")
        return support.git(maint, "rev-parse", "HEAD")

    def run_publish(self, base: str, sha: str):
        import publish as publish_script

        gh = support.FakeGitHub(
            files=[{"filename": "submissions/shared-tool.json"}],
            pulls=[
                {
                    "number": 5,
                    "merged_at": "2026-10-31T00:00:00Z",
                    "merge_commit_sha": sha,
                    "base": {"ref": "main"},
                    "user": support.user("alice-dev", 101),
                }
            ],
        )
        calls = []
        inner = claim_attempt(self.b, "shared-tool")

        def attempt(sub_path: Path, author: Author) -> Attempt:
            calls.append(json.loads(sub_path.read_text())["description"])
            return inner(sub_path, author)

        rc = publish_script.run(
            {"before": base, "after": sha}, root=self.b, gh=gh, git=self.ops(self.b), attempt=attempt
        )
        return rc, gh, calls

    def test_content_replaced_by_another_account_is_not_published_as_the_first_author(self) -> None:
        maint = Path(self._tmp.name) / "maint"
        support.git(Path(self._tmp.name), "clone", "-q", str(self.remote), str(maint))
        base = support.git(maint, "rev-parse", "HEAD")
        alice_merge = self.merge(maint, "alice's package")
        self.merge(maint, "mallory's replacement")
        tip = support.git(self.remote, "rev-parse", "main")

        rc, gh, calls = self.run_publish(base, alice_merge)

        self.assertEqual(rc, 0)
        self.assertEqual(calls, [], "the replaced content must never reach the pipeline")
        self.assertEqual(support.git(self.remote, "rev-parse", "main"), tip, "nothing was pushed")
        self.assertNotIn("shared-tool", self.remote_registry())
        self.assertIn("changed again on main", gh.comments()[-1])

    def test_unreplaced_content_publishes_as_the_merging_author(self) -> None:
        maint = Path(self._tmp.name) / "maint"
        support.git(Path(self._tmp.name), "clone", "-q", str(self.remote), str(maint))
        base = support.git(maint, "rev-parse", "HEAD")
        alice_merge = self.merge(maint, "alice's package")

        rc, _, calls = self.run_publish(base, alice_merge)

        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["alice's package"])
        self.assertEqual(self.remote_registry()["shared-tool"]["publisher_id"], ALICE.id)


class GitOpsSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        support.git(self.root, "init", "-q", "-b", "main")
        (self.root / "a.json").write_text("{}", encoding="utf-8")
        support.git(self.root, "add", "a.json")
        support.git(self.root, "commit", "-qm", "init")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reset_refused_outside_ci(self) -> None:
        with mock.patch.dict("os.environ", {"GITHUB_ACTIONS": ""}), self.assertRaises(GitError):
            GitOps(self.root).reset_to("HEAD")

    def test_unsafe_refs_and_paths_refused(self) -> None:
        ops = GitOps(self.root, allow_reset=True)
        for ref in ("--upload-pack=x", "a..b", "x;y"):
            with self.assertRaises(GitError):
                ops.reset_to(ref)
        head = ops.head()
        for path in ("../x", "/etc/passwd", "-x", "a:b"):
            with self.assertRaises(GitError):
                ops.read_blob(head, path, max_bytes=100)

    def test_blob_size_cap(self) -> None:
        ops = GitOps(self.root)
        self.assertEqual(ops.read_blob(ops.head(), "a.json", max_bytes=10), b"{}")
        with self.assertRaises(GitError):
            ops.read_blob(ops.head(), "a.json", max_bytes=1)

    def test_push_token_stays_out_of_argv_and_errors(self) -> None:
        token = "tok" + "en-value-" + "0123456789"
        ops = GitOps(self.root, push_token=token)
        seen = {}

        def fake_run(args, **kwargs):
            seen["args"], seen["env"] = args, kwargs["env"]
            return mock.Mock(returncode=1, stdout=b"", stderr=f"fatal: auth failed for {token}".encode())

        with mock.patch("subprocess.run", fake_run), self.assertRaises(GitError) as ctx:
            ops.push()
        self.assertNotIn(token, " ".join(seen["args"]))
        self.assertNotIn(token, str(ctx.exception))
        self.assertNotIn(token, repr(ops))
        self.assertTrue(seen["env"]["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic "))

    def test_rejected_push_is_recognised(self) -> None:
        ops = GitOps(self.root)
        with (
            mock.patch(
                "subprocess.run",
                return_value=mock.Mock(
                    returncode=1, stdout=b"! HEAD:refs/heads/main [rejected] (fetch first)", stderr=b""
                ),
            ),
            self.assertRaises(PushRejected),
        ):
            ops.push()


if __name__ == "__main__":
    unittest.main()
