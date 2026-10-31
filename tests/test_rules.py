"""The rule data itself (scripts/ainexlayer_marketplace/rules.py)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ainexlayer_marketplace import rules  # noqa: E402


class RulesTest(unittest.TestCase):
    def test_names(self) -> None:
        for good in ("a", "hello-ainexlayer", "a.b-c", "x" * 64, "0day"):
            self.assertTrue(rules.is_valid_name(good), good)
        for bad in ("", "A", "a_b", "-a", "a-", "a--b", "a..b", "x" * 65, "a/b", None, 3):
            self.assertFalse(rules.is_valid_name(bad), bad)

    def test_semver_is_plain(self) -> None:
        self.assertTrue(rules.is_semver("10.0.1"))
        for bad in ("1.0", "1.0.0-rc.1", "v1.0.0", " 1.0.0", None):
            self.assertFalse(rules.is_semver(bad), bad)
        self.assertLess(rules.version_tuple("1.9.0"), rules.version_tuple("1.10.0"))

    def test_reserved_names_cover_both_spellings(self) -> None:
        self.assertIn("google_drive", rules.RESERVED_PLUGIN_IDS)
        self.assertIn("google-drive", rules.RESERVED_PLUGIN_IDS)
        self.assertIn("plugin-google-drive", rules.RESERVED_SKILL_NAMES)
        self.assertIsNotNone(rules.reserved_reason("plugin", "slack"))
        self.assertIsNone(rules.reserved_reason("plugin", "error-tracker"))
        self.assertIsNone(rules.reserved_reason("agent", "slack"))

    def test_app_identifiers_are_kept(self) -> None:
        # The app reads plugin manifests under these keys and opens publish
        # issues with these markers; renaming them would break the app.
        self.assertEqual(rules.EXTENSION_NAMESPACES, ("io.github.dodaios", "io.github.personaljarvis"))
        self.assertEqual(rules.SKILL_FLAVORS, ("jarvis", "portable"))
        self.assertEqual(rules.INTAKE_TITLE_TAG, "[publish]")
        self.assertEqual(rules.INTAKE_BODY_MARKER, "<!-- jarvis-marketplace-submission -->")

    def test_field_error_matches_the_app_shape(self) -> None:
        problem = rules.FieldError("bad", "name")
        self.assertEqual(problem.to_dict(), {"error": "bad", "field": "name"})
        self.assertTrue(problem.is_error)
        self.assertFalse(rules.warning("meh").is_error)


if __name__ == "__main__":
    unittest.main()
