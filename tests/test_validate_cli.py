"""scripts/validate.py over a whole registry checkout."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate  # noqa: E402
from ainexlayer_marketplace import rules  # noqa: E402

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
