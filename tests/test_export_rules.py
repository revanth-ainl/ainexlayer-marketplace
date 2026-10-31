"""scripts/export_rules.py: docs/rules.json is an export of the library, never a second copy."""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import export_rules
from ainexlayer_marketplace import registry, rules, security, submission


class ExportRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = export_rules.build_rules()
        self.rules = self.data["modules"]["rules"]["constants"]

    def test_committed_file_is_current(self) -> None:
        self.assertEqual((ROOT / "docs" / "rules.json").read_text(encoding="utf-8"), export_rules.render(self.data))

    def test_deterministic_without_timestamps(self) -> None:
        self.assertEqual(export_rules.render(export_rules.build_rules()), export_rules.render(self.data))
        self.assertNotRegex(export_rules.render(self.data), r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

    def test_every_public_rule_is_exported_from_the_library(self) -> None:
        public = {n for n in vars(rules) if re.fullmatch(r"[A-Z][A-Z0-9_]*", n) and not callable(getattr(rules, n))}
        self.assertEqual(set(self.rules), public)
        self.assertEqual(self.data["app_source_commit"], rules.APP_SOURCE_COMMIT)

    def test_values_match_the_library(self) -> None:
        self.assertEqual(self.rules["NAME_RE"], rules.NAME_RE.pattern)
        self.assertEqual(self.rules["SEMVER_RE"], rules.SEMVER_RE.pattern)
        self.assertEqual(self.rules["SECRET_PATTERNS"], [p.pattern for p in rules.SECRET_PATTERNS])
        self.assertEqual(self.rules["MAX_FILE_BYTES"], rules.MAX_FILE_BYTES)
        self.assertEqual(self.rules["RESERVED_PLUGIN_IDS"], sorted(rules.RESERVED_PLUGIN_IDS))
        self.assertEqual(self.rules["RESERVED_ENV_NAMES"], sorted(rules.RESERVED_ENV_NAMES))
        self.assertEqual(self.rules["MCP_TRANSPORTS"], list(rules.MCP_TRANSPORTS))
        self.assertEqual(set(self.rules["AUTH_FIELDS"]), set(rules.AUTH_FIELDS))
        for mode, (required, optional) in rules.AUTH_FIELDS.items():
            self.assertEqual(set(self.rules["AUTH_FIELDS"][mode]["required"]), set(required))
            self.assertEqual(set(self.rules["AUTH_FIELDS"][mode]["optional"]), set(optional))
        exported = self.data["modules"]
        self.assertEqual(
            {k: sorted(v) for k, v in exported["submission"]["constants"]["ALLOWED_KEYS"].items()},
            {k: sorted(v) for k, v in submission.ALLOWED_KEYS.items()},
        )
        self.assertEqual(exported["security"]["constants"]["ALLOWED_SUFFIXES"], sorted(security.ALLOWED_SUFFIXES))
        self.assertEqual(exported["registry"]["constants"]["TRUST_LEVELS"], list(registry.TRUST_LEVELS))

    def test_exported_patterns_compile_and_behave_the_same(self) -> None:
        name_re = re.compile(self.rules["NAME_RE"])
        for name in ("hello-ainexlayer", "a.b", "Bad", "-x"):
            self.assertEqual(bool(name_re.fullmatch(name)), bool(rules.NAME_RE.fullmatch(name)), name)

    def test_imported_constants_are_not_repeated(self) -> None:
        # submission.py imports MAX_FILE_BYTES from rules; it appears once, under rules.
        self.assertNotIn("MAX_FILE_BYTES", self.data["modules"]["submission"]["constants"])

    def test_check_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "rules.json"
            run = lambda *a: export_rules.main(["--output", str(out), *a])
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run("--check"), 1)
                self.assertFalse(out.exists())
                self.assertEqual(run(), 0)
                self.assertEqual(run("--check"), 0)
                out.write_text(out.read_text(encoding="utf-8").replace("3", "4", 1), encoding="utf-8")
                self.assertEqual(run("--check"), 1)
            json.loads(out.read_text(encoding="utf-8"))

    def test_command_line_check(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(ROOT / "scripts" / "export_rules.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
