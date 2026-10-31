"""Credential scan and package folder safety (scripts/ainexlayer_marketplace/security.py)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ainexlayer_marketplace import security  # noqa: E402
from ainexlayer_marketplace.rules import MAX_PACKAGE_FILE_BYTES  # noqa: E402

# Built from pieces so this file itself never matches a secret scanner.
FAKE_TOKENS = {
    "github": "gh" + "p_" + "A" * 36,
    "github_pat": "github" + "_pat_" + "B" * 30,
    "openai": "sk" + "-" + "c" * 30,
    "slack": "xo" + "xb-" + "1" * 20,
    "aws": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "google": "AI" + "za" + "D" * 33,
    "private_key": "-----BEGIN RSA PRIVATE" + " KEY-----",
    "jwt": "ey" + "J" + "a" * 24 + ".ey" + "J" + "b" * 24,
}


class ScanTextTest(unittest.TestCase):
    def test_every_token_family_is_refused(self) -> None:
        for family, token in FAKE_TOKENS.items():
            with self.subTest(family=family):
                errors = security.scan_text(f"use {token} here", "SKILL.md")
                self.assertTrue(errors and errors[0].is_error, family)

    def test_generic_key_value_is_a_warning_outside_agents(self) -> None:
        errors = security.scan_text("password = hunter2hunter2", "SKILL.md")
        self.assertEqual([e.severity for e in errors], ["warning"])

    def test_generic_key_value_is_an_error_in_strict_mode(self) -> None:
        errors = security.scan_text("password = hunter2hunter2", "agent", strict=True)
        self.assertEqual([e.severity for e in errors], ["error"])

    def test_placeholders_and_plain_text_pass(self) -> None:
        self.assertEqual(security.scan_text("token: $plugin_x_token", "mcp.json"), [])
        self.assertEqual(security.scan_text("Ask the user for their token.", "SKILL.md"), [])


class PathTest(unittest.TestCase):
    def test_safe_relpath(self) -> None:
        for good in ("SKILL.md", "skills/a/SKILL.md", "io.github.dodaios/usage-card.md"):
            self.assertTrue(security.is_safe_relpath(good), good)
        for bad in ("", "/etc/passwd", "../x", "a/../b", "a\\b", ".hidden", "a/.git/config", "a//b"):
            self.assertFalse(security.is_safe_relpath(bad), bad)

    def test_safe_join_stays_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(security.safe_join(root, "skills/a/SKILL.md"), root.resolve() / "skills/a/SKILL.md")
            self.assertIsNone(security.safe_join(root, "../escape.md"))
            self.assertIsNone(security.safe_join(root, "/abs.md"))

    def test_safe_join_refuses_a_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (root / "skills").symlink_to(outside, target_is_directory=True)
            self.assertIsNone(security.safe_join(root, "skills/a/SKILL.md"))


class CheckTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.pkg = self.root / "pkg"
        self.pkg.mkdir()
        (self.pkg / "plugin.json").write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def messages(self) -> list:
        return [e.message for e in security.check_tree(self.pkg) if e.is_error]

    def test_clean_folder_passes(self) -> None:
        self.assertEqual(self.messages(), [])

    def test_symlinked_file_is_refused(self) -> None:
        target = self.root / "secret.md"
        target.write_text("x", encoding="utf-8")
        os.symlink(target, self.pkg / "usage_card.md")
        self.assertTrue(any("symlink" in m for m in self.messages()))

    def test_symlinked_folder_is_refused(self) -> None:
        os.symlink(self.root, self.pkg / "skills", target_is_directory=True)
        self.assertTrue(any("symlink" in m for m in self.messages()))

    def test_the_package_folder_itself_may_not_be_a_link(self) -> None:
        link = self.root / "link"
        os.symlink(self.pkg, link, target_is_directory=True)
        self.assertTrue(any("symlink" in e.message for e in security.check_tree(link)))

    def test_hidden_file_is_refused(self) -> None:
        (self.pkg / ".env").write_text("A=1", encoding="utf-8")
        self.assertTrue(any("hidden" in m for m in self.messages()))

    def test_other_file_types_are_refused(self) -> None:
        (self.pkg / "install.sh").write_text("echo hi", encoding="utf-8")
        self.assertTrue(any("only" in m for m in self.messages()))

    def test_oversized_file_is_refused(self) -> None:
        (self.pkg / "big.md").write_text("a" * (MAX_PACKAGE_FILE_BYTES + 1), encoding="utf-8")
        self.assertTrue(any("limit" in m for m in self.messages()))

    def test_non_utf8_is_refused(self) -> None:
        (self.pkg / "bad.md").write_bytes(b"\xff\xfe\x00")
        self.assertTrue(any("UTF-8" in m for m in self.messages()))

    def test_credential_in_a_file_is_refused(self) -> None:
        (self.pkg / "usage_card.md").write_text(f"key {FAKE_TOKENS['github']}", encoding="utf-8")
        self.assertTrue(any("credential" in m for m in self.messages()))

    def test_unsafe_folder_name_is_refused(self) -> None:
        (self.pkg / "Bad_Name").mkdir()
        self.assertTrue(any("folder name" in m for m in self.messages()))


if __name__ == "__main__":
    unittest.main()
