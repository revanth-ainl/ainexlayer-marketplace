"""scripts/validate.py over a whole registry checkout."""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate
from ainexlayer_marketplace import rules

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "submissions"


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args)


class ValidateTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(ROOT / "skills", self.root / "skills")
        shutil.copy(ROOT / "registry.json", self.root / "registry.json")
        self.registry = json.loads((self.root / "registry.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def errors(self) -> list:
        return [e.message for e in validate.validate_tree(self.root) if e.is_error]

    def save_registry(self) -> None:
        (self.root / "registry.json").write_text(json.dumps(self.registry), encoding="utf-8")

    def test_this_repository_passes(self) -> None:
        self.assertEqual([e.message for e in validate.validate_tree(ROOT) if e.is_error], [])

    def test_copy_passes(self) -> None:
        self.assertEqual(self.errors(), [])

    def test_package_without_owner_is_refused(self) -> None:
        del self.registry["owners"]["hello-ainexlayer"]
        self.save_registry()
        self.assertTrue(any("has no owner" in m for m in self.errors()))

    def test_owner_kind_must_match(self) -> None:
        self.registry["owners"]["hello-ainexlayer"]["kind"] = "plugin"
        self.save_registry()
        self.assertTrue(any("not a skill" in m for m in self.errors()))

    def test_owner_fields(self) -> None:
        owner = self.registry["owners"]["hello-ainexlayer"]
        owner["publisher"] = "not a login!"
        owner["publisher_id"] = -3
        self.save_registry()
        messages = " | ".join(self.errors())
        self.assertIn("GitHub login", messages)
        self.assertIn("positive integer", messages)

    def test_same_name_in_two_kinds_is_refused(self) -> None:
        shutil.copytree(self.root / "skills" / "hello-ainexlayer", self.root / "plugins" / "hello-ainexlayer")
        self.assertTrue(any("both a" in m for m in self.errors()))

    def test_stray_file_in_a_kind_folder_is_refused(self) -> None:
        (self.root / "skills" / "loose.md").write_text("x", encoding="utf-8")
        self.assertTrue(any("not a folder" in m for m in self.errors()))

    def test_finder_metadata_is_ignored(self) -> None:
        # macOS drops .DS_Store into folders the user opens; .gitignore
        # keeps it out of the repository, so it must not fail the check.
        for folder in (self.root / "skills", self.root / "skills" / "hello-ainexlayer"):
            (folder / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        self.assertEqual(self.errors(), [])

    def test_other_hidden_files_are_still_refused(self) -> None:
        (self.root / "skills" / "hello-ainexlayer" / ".env").write_text("A=1", encoding="utf-8")
        self.assertTrue(any("hidden" in m for m in self.errors()))

    def test_github_automation_is_refused(self) -> None:
        for rel in (".github/workflows/ci.yml", ".github/dependabot.yml", "docs/.nojekyll", "CNAME"):
            with self.subTest(path=rel):
                path = self.root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")
                self.assertTrue(any("no GitHub automation" in m for m in self.errors()))
                path.unlink()

    def test_exit_status(self) -> None:
        self.assertEqual(quiet(validate.main, ["--root", str(self.root)]), 0)
        (self.root / "registry.json").write_text("{", encoding="utf-8")
        self.assertEqual(quiet(validate.main, ["--root", str(self.root)]), 1)

    def test_registry_version_must_match_the_package(self) -> None:
        self.registry["owners"]["hello-ainexlayer"]["version"] = "1.0.1"
        self.save_registry()
        self.assertTrue(any("record the published version" in m for m in self.errors()))

    def test_unknown_owner_field_is_refused(self) -> None:
        self.registry["owners"]["hello-ainexlayer"]["owner_email"] = "x@example.com"
        self.save_registry()
        self.assertTrue(any("unknown fields" in m for m in self.errors()))

    def test_format_1_registry_still_validates(self) -> None:
        owner = self.registry["owners"]["hello-ainexlayer"]
        del owner["version"], owner["publisher_id"]
        self.registry["version"] = 1
        self.save_registry()
        self.assertEqual(self.errors(), [])

    def test_trusted_publishers_file_shape(self) -> None:
        shutil.copy(ROOT / "trusted-publishers.json", self.root / "trusted-publishers.json")
        self.assertEqual(self.errors(), [])
        (self.root / "trusted-publishers.json").write_text('{"version": 1, "publishers": [{"login": "x"}]}')
        self.assertTrue(any("trusted-publishers.json" in m for m in self.errors()))


def git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


class BaseRefTest(unittest.TestCase):
    """--base-ref: ownership, version increase and content-change rules against a commit."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for rel in ("skills", "docs"):
            shutil.copytree(ROOT / rel, self.root / rel)
        for rel in ("registry.json", "trusted-publishers.json"):
            shutil.copy(ROOT / rel, self.root / rel)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        self.skill = self.root / "skills" / "hello-ainexlayer" / "SKILL.md"
        self.registry = json.loads((self.root / "registry.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def errors(self) -> list:
        return [e.message for e in validate.validate_tree(self.root, "HEAD") if e.is_error]

    def set_version(self, version: str) -> None:
        text = re.sub(r"(?m)^version: .*$", f"version: {version}", self.skill.read_text(encoding="utf-8"))
        self.skill.write_text(text, encoding="utf-8")
        self.registry["owners"]["hello-ainexlayer"]["version"] = version
        (self.root / "registry.json").write_text(json.dumps(self.registry), encoding="utf-8")

    def test_unchanged_tree_passes(self) -> None:
        self.assertEqual(self.errors(), [])

    def test_unknown_ref_is_refused(self) -> None:
        messages = [e.message for e in validate.validate_tree(self.root, "no-such-ref") if e.is_error]
        self.assertTrue(any("is not a commit" in m for m in messages))

    def test_content_change_needs_a_new_version(self) -> None:
        with self.skill.open("a", encoding="utf-8") as fh:
            fh.write("\nOne more line.\n")
        self.assertTrue(any("content changed" in m for m in self.errors()))
        self.set_version("1.0.1")
        self.assertEqual(self.errors(), [])

    def test_downgrade_is_refused(self) -> None:
        self.set_version("1.1.0")
        git(self.root, "commit", "-q", "-am", "1.1.0")
        self.set_version("1.0.5")
        self.assertTrue(any("versions only go up" in m for m in self.errors()))

    def test_owner_takeover_is_refused(self) -> None:
        self.registry["owners"]["hello-ainexlayer"].update(publisher="someone-else", publisher_id=99)
        (self.root / "registry.json").write_text(json.dumps(self.registry), encoding="utf-8")
        self.assertTrue(any("belongs to GitHub account 340421019" in m for m in self.errors()))


class SubmissionFileTest(unittest.TestCase):
    def test_issue_body_file(self) -> None:
        doc = json.loads((FIXTURES / "plugin-stdio.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "issue.md"
            body.write_text(f"{rules.INTAKE_BODY_MARKER}\n```json\n{json.dumps(doc)}\n```\n", encoding="utf-8")
            self.assertEqual(quiet(validate.main, ["--submission", str(body)]), 0)
            doc["mcp_json"]["mcpServers"]["notes-index"]["args"] = ["-y", "@example/notes-index-mcp"]
            body.write_text(f"```json\n{json.dumps(doc)}\n```\n", encoding="utf-8")
            self.assertEqual(quiet(validate.main, ["--submission", str(body)]), 1)


class CommandLineTest(unittest.TestCase):
    def test_runs_isolated_from_the_repository_root(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(ROOT / "scripts" / "validate.py")],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validate: ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
