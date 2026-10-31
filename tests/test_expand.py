"""scripts/expand.py: one reviewed submission -> package files and a registry claim."""

from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import compile_index
import expand
import validate
from ainexlayer_marketplace import registry, rules, submission

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "submissions"
PUBLISHER, PUBLISHER_ID = "dev-one", 1234567
WHEN = "2026-10-31T00:00:00Z"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class ExpandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        for rel in ("skills", "docs"):
            shutil.copytree(ROOT / rel, self.root / rel)
        for rel in ("registry.json", "trusted-publishers.json"):
            shutil.copy(ROOT / rel, self.root / rel)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, obj, *extra: str, publisher=PUBLISHER, publisher_id=PUBLISHER_ID, text=None) -> tuple:
        path = Path(self._tmp.name) / "submission.json"
        path.write_text(text if text is not None else json.dumps(obj), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        argv = [str(path), "--root", str(self.root), "--publisher", publisher, "--publisher-id", str(publisher_id)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = expand.main([*argv, "--claimed-at", WHEN, *extra])
        return code, out.getvalue(), err.getvalue()

    def write(self, obj, **kw) -> tuple:
        return self.run_cli(obj, "--write", **kw)

    # -- dry run and repeat runs --------------------------------------------

    def test_dry_run_writes_nothing(self) -> None:
        before = snapshot(self.root)
        for name in ("skill-portable.json", "plugin-http.json", "plugin-stdio.json", "agent-template.json"):
            code, out, _ = self.run_cli(fixture(name))
            self.assertEqual(code, 0, name)
            self.assertIn("dry run", out)
        self.assertEqual(snapshot(self.root), before)

    def test_second_write_changes_nothing(self) -> None:
        for name in ("skill-portable.json", "plugin-http.json", "plugin-stdio.json", "agent-template.json"):
            self.assertEqual(self.write(fixture(name))[0], 0, name)
        after_first = snapshot(self.root)
        for name in ("skill-portable.json", "plugin-http.json", "plugin-stdio.json", "agent-template.json"):
            code, out, _ = self.write(fixture(name))
            self.assertEqual(code, 0)
            self.assertIn("no changes", out)
        self.assertEqual(snapshot(self.root), after_first)

    # -- round trips ----------------------------------------------------------

    def test_every_kind_round_trips_and_validates(self) -> None:
        for name in ("skill-portable.json", "plugin-http.json", "plugin-stdio.json", "agent-template.json"):
            obj = fixture(name)
            self.assertEqual(self.write(obj)[0], 0, name)
            doc = submission.from_tree(self.root, obj["kind"], obj["name"])
            self.assertEqual(doc["version"], obj["version"])
            if obj["kind"] == "plugin":
                self.assertEqual(doc["plugin_json"], obj["plugin_json"])
                self.assertEqual(doc.get("mcp_json"), obj.get("mcp_json"))
            if obj["kind"] == "agent":
                self.assertEqual(doc["agent"], obj["agent"])
                self.assertEqual(doc["title"], obj["title"])
        self.assertEqual([e.message for e in validate.validate_tree(self.root) if e.is_error], [])
        index = compile_index.build_index(self.root, None, now=WHEN)
        self.assertEqual([len(index[s]) for s in ("plugins", "skills", "agents")], [2, 2, 1])
        owner = registry.load(self.root / "registry.json")["owners"]["research-buddy"]
        self.assertEqual(owner, {"kind": "agent", "publisher": PUBLISHER, "publisher_id": PUBLISHER_ID,
                                 "version": "1.0.0", "claimed_at": WHEN})  # fmt: skip

    def test_app_style_skill_keeps_the_submitted_version_title_and_categories(self) -> None:
        # AIAgentLayer's template frontmatter says version 1.0.0 and has no title;
        # the form sends version, title and categories as separate fields.
        obj = {
            "kind": "skill",
            "name": "app-style",
            "version": "1.1.0",
            "title": "App Style: demo",
            "description": "Form text.",
            "categories": ["writing"],
            "skill_md": "---\nname: app-style\ndescription: When to use it.\nversion: 1.0.0\n---\n\n# Body\n\nDo it.\n",
        }
        code, _, err = self.write(obj)
        self.assertEqual(code, 0, err)
        self.assertIn("frontmatter description", err)
        text = (self.root / "skills" / "app-style" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.endswith("---\n\n# Body\n\nDo it.\n"), "the body is untouched")
        self.assertIn("description: When to use it.\n", text)
        entry = next(
            e for e in compile_index.build_index(self.root, None, now=WHEN)["skills"] if e["name"] == "app-style"
        )
        self.assertEqual(
            (entry["version"], entry["title"], entry["categories"]), ("1.1.0", "App Style: demo", ["writing"])
        )
        self.assertEqual(entry["description"], "When to use it.")

    def test_submitted_description_is_still_checked(self) -> None:
        obj = fixture("skill-portable.json")
        obj["description"] = "x" * (rules.MAX_DESCRIPTION_CHARS + 1)
        code, _, err = self.write(obj)
        self.assertEqual(code, 1)
        self.assertIn("description", err)

    def test_issue_body_is_read_as_data(self) -> None:
        obj = fixture("plugin-stdio.json")
        body = f"{rules.INTAKE_BODY_MARKER}\n\nSubmitted from AIAgentLayer.\n\n```json\n{json.dumps(obj)}\n```\n"
        code, out, err = self.run_cli(None, text=body)
        self.assertEqual(code, 0, err)
        self.assertIn("plugins/notes-index/", out)

    def test_submission_publisher_fields_are_ignored(self) -> None:
        obj = fixture("skill-portable.json")
        obj.update(publisher="someone-else", publisher_id=999)
        self.assertEqual(self.write(obj)[0], 0)
        owner = registry.load(self.root / "registry.json")["owners"]["meeting-recap"]
        self.assertEqual((owner["publisher"], owner["publisher_id"]), (PUBLISHER, PUBLISHER_ID))

    # -- refusals -------------------------------------------------------------

    def test_invalid_submission_writes_nothing(self) -> None:
        before = snapshot(self.root)
        obj = fixture("plugin-stdio.json")
        obj["mcp_json"]["mcpServers"]["notes-index"]["args"] = ["-y", "@example/notes-index-mcp"]
        code, _, err = self.write(obj)
        self.assertEqual(code, 1)
        self.assertIn("nothing was written", err)
        self.assertEqual(snapshot(self.root), before)

    def test_path_escapes_are_refused(self) -> None:
        for bad in ("../escape", "a/b", ".hidden", "..", "/abs"):
            obj = fixture("skill-portable.json")
            obj["name"] = bad
            self.assertEqual(self.write(obj)[0], 1, bad)
        obj = fixture("plugin-http.json")
        obj["skills"][0]["name"] = "../../outside"
        self.assertEqual(self.write(obj)[0], 1)
        self.assertFalse((self.root.parent / "outside").exists())

    def test_symlinked_target_is_refused(self) -> None:
        elsewhere = Path(self._tmp.name) / "elsewhere"
        elsewhere.mkdir()
        os.symlink(elsewhere, self.root / "skills" / "meeting-recap")
        code, _, err = self.write(fixture("skill-portable.json"))
        self.assertEqual(code, 1)
        self.assertIn("symlink", err)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_symlinked_kind_folder_is_refused(self) -> None:
        elsewhere = Path(self._tmp.name) / "elsewhere"
        elsewhere.mkdir()
        os.symlink(elsewhere, self.root / "agents")
        self.assertEqual(self.write(fixture("agent-template.json"))[0], 1)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_symlink_inside_existing_package_is_refused(self) -> None:
        self.assertEqual(self.write(fixture("plugin-http.json"))[0], 0)
        os.symlink("/etc/hosts", self.root / "plugins" / "error-tracker" / "usage_card.md.bak")
        obj = fixture("plugin-http.json")
        obj["version"] = obj["plugin_json"]["version"] = "1.0.1"
        self.assertEqual(self.write(obj)[0], 1)

    def test_other_owner_is_refused(self) -> None:
        self.assertEqual(self.write(fixture("agent-template.json"))[0], 0)
        obj = fixture("agent-template.json")
        obj["version"] = "1.1.0"
        code, _, err = self.write(obj, publisher="intruder", publisher_id=42)
        self.assertEqual(code, 1)
        self.assertIn("belongs to GitHub account 1234567", err)

    def test_existing_name_cannot_be_taken(self) -> None:
        obj = fixture("skill-portable.json")
        obj["name"] = "hello-ainexlayer"
        obj["skill_md"] = obj["skill_md"].replace("meeting-recap", "hello-ainexlayer")
        obj["version"] = "2.0.0"
        self.assertEqual(self.write(obj)[0], 1)

    def test_downgrade_and_same_version_with_new_content_are_refused(self) -> None:
        obj = fixture("plugin-stdio.json")
        self.assertEqual(self.write(obj)[0], 0)
        older = copy.deepcopy(obj)
        older["version"] = older["plugin_json"]["version"] = "2.0.9"
        self.assertEqual(self.write(older)[0], 1)
        changed = copy.deepcopy(obj)
        changed["plugin_json"]["description"] = "Something else entirely."
        code, _, err = self.write(changed)
        self.assertEqual(code, 1)
        self.assertIn("use a new version", err)

    def test_update_removes_files_the_new_version_dropped(self) -> None:
        self.assertEqual(self.write(fixture("plugin-http.json"))[0], 0)
        obj = fixture("plugin-http.json")
        obj["version"] = obj["plugin_json"]["version"] = "1.1.0"
        del obj["skills"], obj["usage_card"]
        self.assertEqual(self.write(obj)[0], 0)
        folder = self.root / "plugins" / "error-tracker"
        self.assertEqual(
            sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")), ["mcp.json", "plugin.json"]
        )
        self.assertEqual(registry.load(self.root / "registry.json")["owners"]["error-tracker"]["version"], "1.1.0")

    def test_oversized_input_is_refused(self) -> None:
        code, _, err = self.run_cli(None, text="x" * (expand.MAX_INPUT_BYTES + 1))
        self.assertEqual(code, 1)
        self.assertIn("limit", err)

    def test_publisher_id_is_required(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            expand.main([str(FIXTURES / "skill-portable.json"), "--publisher", "x"])


class FrontmatterTest(unittest.TestCase):
    def test_replaces_and_appends_keys(self) -> None:
        text = "---\nname: x\ntitle: Old\ncategories:\n  - a\n  - b\ndescription: d\n---\nbody\n"
        out = expand.set_frontmatter(text, {"title": "New", "categories": ["c"], "version": "1.2.3"})
        self.assertEqual(out, "---\nname: x\ntitle: New\ncategories: [c]\ndescription: d\nversion: 1.2.3\n---\nbody\n")
        meta = submission.parse_frontmatter(out)
        self.assertEqual((meta["title"], meta["categories"], meta["version"]), ("New", ["c"], "1.2.3"))

    def test_quotes_when_needed(self) -> None:
        out = expand.set_frontmatter("---\nname: x\n---\n", {"title": "A: b # c", "categories": ["x y"]})
        self.assertIn('title: "A: b # c"', out)
        self.assertEqual(submission.parse_frontmatter(out)["title"], "A: b # c")
        with self.assertRaises(expand.ExpandError):
            expand.set_frontmatter("---\nname: x\n---\n", {"categories": ["a, b"]})

    def test_no_frontmatter_is_left_for_validation(self) -> None:
        self.assertEqual(expand.set_frontmatter("# no frontmatter\n", {"version": "1.0.0"}), "# no frontmatter\n")


class NoNetworkNoExecutionTest(unittest.TestCase):
    def test_imports_only_safe_standard_modules(self) -> None:
        tree = ast.parse((ROOT / "scripts" / "expand.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        forbidden = {
            "subprocess",
            "socket",
            "urllib",
            "http",
            "ftplib",
            "ssl",
            "requests",
            "importlib",
            "runpy",
            "ctypes",
        }
        self.assertEqual(imported & forbidden, set())
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertEqual(calls & {"eval", "exec", "compile", "__import__"}, set())


if __name__ == "__main__":
    unittest.main()
