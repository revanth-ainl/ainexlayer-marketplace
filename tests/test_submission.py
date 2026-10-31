"""Submissions and package folders (scripts/ainexlayer_marketplace/submission.py)."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ainexlayer_marketplace import rules, submission

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "submissions"
FIXTURE_NAMES = ("skill-portable.json", "plugin-http.json", "plugin-stdio.json", "agent-template.json")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def errors_of(problems: list) -> list:
    return [p.message for p in problems if p.is_error]


def write_package(root: Path, doc: dict) -> None:
    """The folder layout from_tree reads, written straight from a submission."""
    kind, name = doc["kind"], doc["name"]
    folder = submission.package_dir(root, kind, name)
    folder.mkdir(parents=True)
    if kind == "skill":
        (folder / "SKILL.md").write_text(doc["skill_md"], encoding="utf-8")
    elif kind == "plugin":
        (folder / "plugin.json").write_text(json.dumps(doc["plugin_json"]), encoding="utf-8")
        if "mcp_json" in doc:
            (folder / "mcp.json").write_text(json.dumps(doc["mcp_json"]), encoding="utf-8")
        if "usage_card" in doc:
            (folder / "usage_card.md").write_text(doc["usage_card"], encoding="utf-8")
        for skill in doc.get("skills", []):
            (folder / "skills" / skill["name"]).mkdir(parents=True)
            (folder / "skills" / skill["name"] / "SKILL.md").write_text(skill["skill_md"], encoding="utf-8")
    else:
        (folder / "agent.json").write_text(json.dumps(doc["agent"]), encoding="utf-8")
        listing = {k: doc[k] for k in ("version", "title", "description", "categories")}
        (folder / "listing.json").write_text(json.dumps(listing), encoding="utf-8")


class SubmissionTest(unittest.TestCase):
    def test_fixtures_pass(self) -> None:
        for name in FIXTURE_NAMES:
            with self.subTest(fixture=name):
                self.assertEqual(errors_of(submission.validate(fixture(name))), [])

    def assertRefused(self, doc: dict, fragment: str) -> None:
        messages = errors_of(submission.validate(doc))
        self.assertTrue(any(fragment in m for m in messages), f"{fragment!r} not in {messages}")

    def test_kind_name_version(self) -> None:
        doc = fixture("skill-portable.json")
        self.assertRefused({**doc, "kind": "wallpaper"}, "kind must be")
        for bad in ("Meeting", "a--b", "a..b", "-a", "a_b", "x" * 65, "../etc"):
            self.assertRefused({**doc, "name": bad}, "name must be")
        for bad in ("1.0", "v1.0.0", "1.0.0-beta", "1.0.0+build", ""):
            self.assertRefused({**doc, "version": bad}, "SemVer")

    def test_reserved_names(self) -> None:
        plugin = fixture("plugin-stdio.json")
        plugin = {**plugin, "name": "google-drive", "plugin_json": {**plugin["plugin_json"], "name": "google-drive"}}
        self.assertRefused(plugin, "ships itself")
        skill = fixture("skill-portable.json")
        skill_md = skill["skill_md"].replace("name: meeting-recap", "name: skill-creator")
        self.assertRefused({**skill, "name": "skill-creator", "skill_md": skill_md}, "ships itself")

    def test_unknown_field_and_ignored_publisher(self) -> None:
        doc = fixture("skill-portable.json")
        self.assertRefused({**doc, "script": "rm -rf /"}, "not a skill submission field")
        problems = submission.validate({**doc, "publisher": "someone", "publisher_id": 1})
        self.assertEqual(errors_of(problems), [])
        self.assertEqual(sum(1 for p in problems if p.severity == "warning"), 2)

    def test_skill_rules(self) -> None:
        doc = fixture("skill-portable.json")
        self.assertRefused({**doc, "title": ""}, "title is required")
        self.assertRefused({**doc, "description": "d" * 501}, "longer than 500")
        self.assertRefused({**doc, "categories": ["a"] * 11}, "at most 10")
        self.assertRefused({**doc, "skill_md": "# no frontmatter\n"}, "frontmatter block")
        self.assertRefused({**doc, "skill_md": "---\nname: meeting-recap\n---\nbody\n"}, "missing 'description'")
        risky = doc["skill_md"].replace("flavor: portable", "risk_policy: safe")
        self.assertRefused({**doc, "skill_md": risky}, "risk_policy")
        renamed = doc["skill_md"].replace("name: meeting-recap", "name: other-skill")
        self.assertRefused({**doc, "skill_md": renamed}, "must equal")
        self.assertRefused({**doc, "version": "1.0.1"}, "differs from the submission version")
        self.assertRefused({**doc, "skill_md": doc["skill_md"] + "x" * 65_536}, "larger than 64 KB")
        bad_flavor = doc["skill_md"].replace("flavor: portable", "flavor: exotic")
        self.assertRefused({**doc, "skill_md": bad_flavor}, "flavor must be")
        many = doc["skill_md"].replace("[Claude Code, Cursor]", "[" + ", ".join(f"A{i}" for i in range(9)) + "]")
        self.assertRefused({**doc, "skill_md": many}, "more than 8")

    def test_credentials_are_refused(self) -> None:
        doc = fixture("skill-portable.json")
        token = "gh" + "p_" + "Z" * 36
        self.assertRefused({**doc, "skill_md": doc["skill_md"] + f"\nUse {token}\n"}, "credential")

    def test_size_limit(self) -> None:
        doc = fixture("plugin-http.json")
        doc["usage_card"] = "x" * rules.MAX_FILE_BYTES
        self.assertRefused(doc, "larger than")

    def test_plugin_rules(self) -> None:
        doc = fixture("plugin-http.json")
        self.assertRefused({**doc, "plugin_json": "nope"}, "plugin_json must be a JSON object")
        mismatch = copy.deepcopy(doc)
        mismatch["plugin_json"]["version"] = "9.9.9"
        self.assertRefused(mismatch, "differs from the submission version")
        self.assertRefused({**doc, "usage_card": 3}, "usage_card")
        self.assertRefused({**doc, "mcp_json": {"mcpServers": {"x": {"type": "sse"}}}}, "sse")

    def test_bundled_skills(self) -> None:
        doc = fixture("plugin-http.json")
        skill = doc["skills"][0]
        self.assertRefused({**doc, "skills": [skill, skill]}, "bundled twice")
        self.assertRefused({**doc, "skills": [skill] * 11}, "at most 10")
        self.assertRefused({**doc, "skills": [{"name": "../x", "skill_md": skill["skill_md"]}]}, "package name rules")
        same = {"name": "error-tracker", "skill_md": skill["skill_md"].replace("triage-errors", "error-tracker")}
        self.assertRefused({**doc, "skills": [skill, same]}, "only skill")
        self.assertEqual(errors_of(submission.validate({**doc, "skills": [same]})), [])

    def test_agent_rules(self) -> None:
        doc = fixture("agent-template.json")
        self.assertRefused({**doc, "title": ""}, "title is required")
        self.assertRefused({**doc, "agent": {**doc["agent"], "provider": "x"}}, "may not be published")


class ExtractTest(unittest.TestCase):
    def test_bare_json(self) -> None:
        doc = fixture("skill-portable.json")
        self.assertEqual(submission.extract_submission(json.dumps(doc)), doc)

    def test_issue_body(self) -> None:
        doc = fixture("agent-template.json")
        body = f"{rules.INTAKE_BODY_MARKER}\nPlease publish.\n\n```json\n{json.dumps(doc, indent=2)}\n```\n"
        self.assertEqual(submission.extract_submission(body), doc)

    def test_zero_or_two_blocks_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            submission.extract_submission("no block here")
        block = "```json\n{}\n```\n"
        with self.assertRaises(ValueError):
            submission.extract_submission(block + block)

    def test_non_object_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            submission.extract_submission("```json\n[1, 2]\n```")


class TreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_round_trip_for_every_kind(self) -> None:
        for name in FIXTURE_NAMES:
            with self.subTest(fixture=name):
                doc = fixture(name)
                write_package(self.root, doc)
                read = submission.from_tree(self.root, doc["kind"], doc["name"])
                self.assertEqual(errors_of(submission.check_package(self.root, doc["kind"], doc["name"])), [])
                for key in ("kind", "name", "version"):
                    self.assertEqual(read[key], doc[key])
                if doc["kind"] == "plugin":
                    self.assertEqual(read["plugin_json"], doc["plugin_json"])
                    self.assertEqual(read.get("mcp_json"), doc.get("mcp_json"))
                    self.assertEqual(read.get("skills", []), doc.get("skills", []))
                if doc["kind"] == "agent":
                    self.assertEqual(read["agent"], doc["agent"])
                if doc["kind"] == "skill":
                    self.assertEqual(read["skill_md"], doc["skill_md"])
                    self.assertEqual(read["compatible_agents"], ["Claude Code", "Cursor"])

    def test_unexpected_files_are_refused(self) -> None:
        doc = fixture("skill-portable.json")
        write_package(self.root, doc)
        (submission.package_dir(self.root, "skill", doc["name"]) / "notes.md").write_text("x", encoding="utf-8")
        messages = errors_of(submission.check_package(self.root, "skill", doc["name"]))
        self.assertTrue(any("only SKILL.md" in m for m in messages), messages)

    def test_agent_needs_a_listing(self) -> None:
        doc = fixture("agent-template.json")
        write_package(self.root, doc)
        (submission.package_dir(self.root, "agent", doc["name"]) / "listing.json").unlink()
        messages = errors_of(submission.check_package(self.root, "agent", doc["name"]))
        self.assertTrue(any("missing listing.json" in m for m in messages), messages)

    def test_two_usage_cards_are_refused(self) -> None:
        doc = fixture("plugin-http.json")
        write_package(self.root, doc)
        folder = submission.package_dir(self.root, "plugin", doc["name"])
        (folder / "io.github.dodaios").mkdir()
        (folder / "io.github.dodaios" / "usage-card.md").write_text("# card\n", encoding="utf-8")
        messages = errors_of(submission.check_package(self.root, "plugin", doc["name"]))
        self.assertTrue(any("more than one usage card" in m for m in messages), messages)

    def test_the_shipped_sample_skill_passes(self) -> None:
        shutil.copytree(ROOT / "skills", self.root / "skills")
        self.assertEqual(submission.check_package(self.root, "skill", "hello-ainexlayer"), [])


class FrontmatterTest(unittest.TestCase):
    def test_parse(self) -> None:
        text = (
            "---\nname: a\ndescription: >\n  folded\n  text\nflavor: 'portable'\n"
            'compatible_agents:\n  - Claude Code\n  - Cursor\ncategories: [x, "y"]\n---\nbody\n'
        )
        meta = submission.parse_frontmatter(text)
        self.assertEqual(meta["description"], "folded text")
        self.assertEqual(meta["flavor"], "portable")
        self.assertEqual(meta["compatible_agents"], ["Claude Code", "Cursor"])
        self.assertEqual(meta["categories"], ["x", "y"])

    def test_no_frontmatter(self) -> None:
        self.assertEqual(submission.parse_frontmatter("# title\n"), {})


if __name__ == "__main__":
    unittest.main()
