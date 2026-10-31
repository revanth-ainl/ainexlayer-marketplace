"""Issue intake: the issue body is data, the author is the publisher."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import automation_support as support
from ainexlayer_marketplace import intake
from ainexlayer_marketplace.automation import (
    LABEL_NEEDS_CHANGES,
    LABEL_PUBLISH_FAILED,
    LABEL_PUBLISHED,
    AutomationError,
    PublishOutcome,
    render_findings,
)

TAG = support.COMPAT.INTAKE_TITLE_TAG
MARKER = support.COMPAT.INTAKE_BODY_MARKER

SUBMISSION = {
    "kind": "skill",
    "name": "tidy-notes",
    "version": "1.2.0",
    "title": "Tidy notes",
    "description": "Turns rough notes into a short list.",
    "categories": ["productivity"],
    "skill_md": "---\nname: tidy-notes\ndescription: Tidy notes.\n---\n\nUse ``` fences sparingly.\n",
}


def app_body(data: object) -> str:
    """The body shape the desktop app writes: marker, prose, one json block."""
    return "\n".join(
        [
            MARKER,
            "**Tidy notes** — skill `tidy-notes` 1.2.0",
            "",
            "> Turns rough notes into a short list.",
            "",
            "### Submission",
            "",
            "```json",
            json.dumps(data, indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    )


def form_body(data: object) -> str:
    """The body the issue form renders (render: json wraps the field)."""
    return "\n".join(
        [
            "### Submission JSON",
            "",
            "```json",
            json.dumps(data),
            "```",
            "",
            "### Publisher agreement",
            "",
            "- [X] I own this package and may publish it",
        ]
    )


def title() -> str:
    return f"{TAG} skill: tidy-notes 1.2.0"


class ParseIssueTests(unittest.TestCase):
    def test_app_body(self) -> None:
        self.assertEqual(intake.parse_issue(title(), app_body(SUBMISSION)), SUBMISSION)

    def test_form_body_without_marker(self) -> None:
        self.assertEqual(intake.parse_issue(title(), form_body(SUBMISSION)), SUBMISSION)

    def test_escaped_fence_inside_a_string_is_fine(self) -> None:
        self.assertIn("```", intake.parse_issue(title(), app_body(SUBMISSION))["skill_md"])

    def assertRefused(self, code: str, t: object, body: object) -> None:
        with self.assertRaises(AutomationError) as ctx:
            intake.parse_issue(t, body)
        self.assertEqual(ctx.exception.code, code)

    def test_title_tag_required(self) -> None:
        self.assertRefused("title", "skill: tidy-notes", app_body(SUBMISSION))
        self.assertRefused("title", None, app_body(SUBMISSION))

    def test_empty_body(self) -> None:
        self.assertRefused("body", title(), "")
        self.assertRefused("body", title(), None)

    def test_no_json_block(self) -> None:
        self.assertRefused("body", title(), MARKER + "\n" + json.dumps(SUBMISSION))

    def test_two_json_blocks(self) -> None:
        self.assertRefused("body", title(), app_body(SUBMISSION) + "\n```json\n{}\n```\n")

    def test_stray_fence(self) -> None:
        self.assertRefused("body", title(), app_body(SUBMISSION) + "\n```\nprint('x')\n")

    def test_oversized_body(self) -> None:
        big = dict(SUBMISSION, description="x" * (300 * 1024))
        self.assertRefused("size", title(), app_body(big))

    def test_invalid_json(self) -> None:
        self.assertRefused("json", title(), "```json\n{'name': 'x'}\n```")

    def test_not_an_object(self) -> None:
        self.assertRefused("json", title(), "```json\n[1, 2]\n```")

    def test_duplicate_keys(self) -> None:
        self.assertRefused("json", title(), '```json\n{"name": "a", "name": "b"}\n```')

    def test_non_json_constants(self) -> None:
        self.assertRefused("json", title(), '```json\n{"name": "a", "x": NaN}\n```')


class HandleIssueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.gh = support.FakeGitHub()
        self.git = support.FakeGit()
        self.published = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def event(self, body: object, *, author: dict | None = None, t: str | None = None, **issue: object) -> dict:
        data = {"number": 7, "title": t or title(), "body": body, "user": author or support.user(), "state": "open"}
        data.update(issue)
        return {"action": "opened", "issue": data}

    def publish_returning(self, outcome: PublishOutcome):
        def publish(path: Path, author: object) -> PublishOutcome:
            self.published.append((path, author, json.loads(path.read_text(encoding="utf-8"))))
            return outcome

        return publish

    def run_intake(self, event: dict, outcome: PublishOutcome) -> intake.IntakeResult:
        return intake.handle_issue(
            event, root=self.root, gh=self.gh, git=self.git, publish=self.publish_returning(outcome)
        )

    def test_success_publishes_as_the_issue_author(self) -> None:
        body = app_body(dict(SUBMISSION, publisher="someone-else", publisher_id=1))
        result = self.run_intake(self.event(body), PublishOutcome("published", sha="a" * 40))
        self.assertEqual(result, intake.IntakeResult("published", "a" * 40))
        path, author, written = self.published[0]
        self.assertEqual(path, (self.root / "submissions" / "tidy-notes.json").resolve())
        self.assertEqual((author.login, author.id), ("octo-dev", 4242))
        self.assertEqual((written["publisher"], written["publisher_id"]), ("octo-dev", 4242))
        self.assertIn(LABEL_PUBLISHED, self.gh.labels_added())
        self.assertIn("close_issue", self.gh.names())

    def test_retry_rewrites_the_submission_after_reset(self) -> None:
        self.run_intake(self.event(app_body(SUBMISSION)), PublishOutcome("published", sha="a" * 40))
        path = self.root / "submissions" / "tidy-notes.json"
        path.unlink()
        for callback in self.git.after_reset:
            callback()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["name"], "tidy-notes")

    def test_refused_by_validator(self) -> None:
        finding = AutomationError("version", "version must be higher than 1.2.0")
        result = self.run_intake(self.event(app_body(SUBMISSION)), PublishOutcome("refused", findings=[finding]))
        self.assertEqual(result.status, "refused")
        self.assertIn(LABEL_NEEDS_CHANGES, self.gh.labels_added())
        self.assertIn("version must be higher", self.gh.comments()[0])
        self.assertNotIn("close_issue", self.gh.names())

    def test_failed_publish_is_visible(self) -> None:
        result = self.run_intake(self.event(app_body(SUBMISSION)), PublishOutcome("failed", detail="race"))
        self.assertEqual(result.status, "failed")
        self.assertIn(LABEL_PUBLISH_FAILED, self.gh.labels_added())
        self.assertTrue(self.gh.comments())

    def test_bot_author_refused(self) -> None:
        result = self.run_intake(
            self.event(app_body(SUBMISSION), author=support.user("helper[bot]", 9, "Bot")), PublishOutcome("published")
        )
        self.assertEqual(result.status, "refused")
        self.assertEqual(self.published, [])

    def test_author_without_numeric_id_refused(self) -> None:
        result = self.run_intake(
            self.event(app_body(SUBMISSION), author={"login": "octo-dev", "type": "User"}), PublishOutcome("published")
        )
        self.assertEqual(result.status, "refused")

    def test_path_traversal_name_refused_and_nothing_written(self) -> None:
        result = self.run_intake(
            self.event(app_body(dict(SUBMISSION, name="../../escape"))), PublishOutcome("published")
        )
        self.assertEqual(result.status, "refused")
        self.assertEqual(self.published, [])
        self.assertEqual(list(self.root.rglob("*.json")), [])

    def test_edit_by_someone_else_is_ignored(self) -> None:
        event = self.event(app_body(SUBMISSION))
        event["action"] = "edited"
        event["sender"] = support.user("a-maintainer", 999)
        self.assertEqual(self.run_intake(event, PublishOutcome("published", sha="a" * 40)).status, "ignored")
        self.assertEqual(self.published, [])
        self.assertEqual(self.gh.calls, [])

    def test_edit_by_the_author_runs_again(self) -> None:
        event = self.event(app_body(SUBMISSION))
        event["action"] = "edited"
        event["sender"] = support.user()
        self.assertEqual(self.run_intake(event, PublishOutcome("published", sha="a" * 40)).status, "published")

    def test_pull_request_and_unrelated_issues_ignored(self) -> None:
        self.assertEqual(
            self.run_intake(self.event(app_body(SUBMISSION), pull_request={}), PublishOutcome("published")).status,
            "ignored",
        )
        self.assertEqual(
            self.run_intake(self.event("hello", t="A question"), PublishOutcome("published")).status, "ignored"
        )
        self.assertEqual(self.gh.calls, [])


class MessageSafetyTests(unittest.TestCase):
    def test_findings_cannot_mention_or_inject_html(self) -> None:
        text = render_findings([AutomationError("x", "ping @maintainers <img src=x> `code`")])
        self.assertNotIn("@maintainers", text)
        self.assertNotIn("<img", text)
        self.assertNotIn("`code`", text)


if __name__ == "__main__":
    unittest.main()
