"""Package ownership (scripts/ainexlayer_marketplace/registry.py)."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ainexlayer_marketplace import registry

OWNER_ID = 340421019
OTHER_ID = 1234567

V1 = {
    "version": 1,
    "owners": {
        "hello-ainexlayer": {"kind": "skill", "publisher": "revanth-ainl", "claimed_at": "2026-10-10T00:00:00Z"},
    },
}
V2 = {
    "version": 2,
    "owners": {
        "hello-ainexlayer": {
            "kind": "skill",
            "publisher": "revanth-ainl",
            "publisher_id": OWNER_ID,
            "version": "1.2.0",
            "claimed_at": "2026-10-10T00:00:00Z",
        },
    },
}
TRUSTED = {OWNER_ID: {"login": "revanth-ainl", "id": OWNER_ID, "trust": "official", "roles": ["maintainer"]}}


def errors(problems) -> list:
    return [p for p in problems if p.is_error]


def fields(problems) -> set:
    return {p.field for p in errors(problems)}


class VersionTest(unittest.TestCase):
    def test_compare(self) -> None:
        self.assertEqual(registry.compare_versions("1.0.0", "1.0.0"), 0)
        self.assertEqual(registry.compare_versions("1.0.10", "1.0.9"), 1)
        self.assertEqual(registry.compare_versions("1.9.9", "2.0.0"), -1)


class MigrateTest(unittest.TestCase):
    def test_v1_becomes_v2_without_losing_fields(self) -> None:
        before = copy.deepcopy(V1)
        out = registry.migrate(V1)
        self.assertEqual(V1, before, "migrate must not change its argument")
        self.assertEqual(out["version"], 2)
        owner = out["owners"]["hello-ainexlayer"]
        self.assertEqual(owner["publisher"], "revanth-ainl")
        self.assertEqual(owner["claimed_at"], "2026-10-10T00:00:00Z")
        self.assertIsNone(owner["publisher_id"])

    def test_idempotent(self) -> None:
        once = registry.migrate(V1)
        self.assertEqual(registry.migrate(once), once)
        self.assertEqual(registry.migrate(V2), V2)

    def test_garbage_is_an_empty_registry(self) -> None:
        self.assertEqual(registry.migrate(None), {"version": 2, "owners": {}})

    def test_this_repository_registry_is_format_2(self) -> None:
        data = json.loads((ROOT / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual(data, registry.migrate(data))
        self.assertEqual(registry.dump(data), (ROOT / "registry.json").read_text(encoding="utf-8"))


class DumpTest(unittest.TestCase):
    def test_sorted_and_fixed_order(self) -> None:
        reg = {"owners": {"b": {"version": "1.0.0", "kind": "skill"}, "a": {"kind": "agent"}}, "version": 2}
        text = registry.dump(reg)
        self.assertTrue(text.endswith("}\n"))
        self.assertLess(text.index('"a"'), text.index('"b"'))
        self.assertLess(text.index('"kind": "skill"'), text.index('"version": "1.0.0"'))
        self.assertEqual(registry.dump(json.loads(text)), text)


class CheckClaimTest(unittest.TestCase):
    def test_new_name_is_free(self) -> None:
        self.assertEqual(errors(registry.check_claim(V2, "skill", "new-skill", "1.0.0", "someone", OTHER_ID)), [])

    def test_new_name_without_id_warns(self) -> None:
        problems = registry.check_claim(V2, "skill", "new-skill", "1.0.0", "someone", None)
        self.assertEqual(errors(problems), [])
        self.assertTrue(any(p.field == "publisher_id" for p in problems))

    def test_bad_inputs_are_field_errors(self) -> None:
        self.assertEqual(fields(registry.check_claim(V2, "skill", "Bad_Name", "1.0.0", "x", 1)), {"name"})
        self.assertEqual(fields(registry.check_claim(V2, "skill", "ok", "1.0", "x", 1)), {"version"})
        self.assertEqual(fields(registry.check_claim(V2, "skill", "ok", "1.0.0", "not a login", 1)), {"publisher"})
        self.assertEqual(fields(registry.check_claim(V2, "skill", "ok", "1.0.0", "x", -1)), {"publisher_id"})
        self.assertEqual(fields(registry.check_claim(V2, "widget", "ok", "1.0.0", "x", 1)), {"kind"})

    def test_reserved_names(self) -> None:
        self.assertEqual(fields(registry.check_claim(V2, "plugin", "google-drive", "1.0.0", "x", 1)), {"name"})
        self.assertEqual(fields(registry.check_claim(V2, "skill", "skill-creator", "1.0.0", "x", 1)), {"name"})

    def test_owner_by_id_may_update(self) -> None:
        problems = registry.check_claim(V2, "skill", "hello-ainexlayer", "1.3.0", "revanth-ainl", OWNER_ID)
        self.assertEqual(problems, [])

    def test_renamed_login_with_same_id_is_the_owner(self) -> None:
        problems = registry.check_claim(V2, "skill", "hello-ainexlayer", "1.3.0", "new-login", OWNER_ID)
        self.assertEqual(errors(problems), [])
        self.assertTrue(any("login changed" in p.message for p in problems))

    def test_same_login_other_id_is_refused(self) -> None:
        problems = registry.check_claim(V2, "skill", "hello-ainexlayer", "1.3.0", "revanth-ainl", OTHER_ID)
        self.assertEqual(fields(problems), {"publisher_id"})

    def test_missing_id_against_recorded_id_is_refused(self) -> None:
        problems = registry.check_claim(V2, "skill", "hello-ainexlayer", "1.3.0", "revanth-ainl", None)
        self.assertEqual(fields(problems), {"publisher_id"})

    def test_pending_id_matches_login_with_warning(self) -> None:
        problems = registry.check_claim(V1, "skill", "hello-ainexlayer", "1.0.1", "Revanth-AINL", OWNER_ID)
        self.assertEqual(errors(problems), [])
        self.assertTrue(any(p.field == "publisher_id" for p in problems))
        self.assertEqual(
            fields(registry.check_claim(V1, "skill", "hello-ainexlayer", "1.0.1", "intruder", OTHER_ID)), {"publisher"}
        )

    def test_name_taken_by_another_kind(self) -> None:
        problems = registry.check_claim(V2, "plugin", "hello-ainexlayer", "2.0.0", "revanth-ainl", OWNER_ID)
        self.assertEqual(fields(problems), {"name"})

    def test_downgrade_is_refused_same_version_allowed(self) -> None:
        self.assertEqual(
            fields(registry.check_claim(V2, "skill", "hello-ainexlayer", "1.1.9", "revanth-ainl", OWNER_ID)), {"version"}
        )
        self.assertEqual(registry.check_claim(V2, "skill", "hello-ainexlayer", "1.2.0", "revanth-ainl", OWNER_ID), [])

    def test_v1_base_without_version_skips_downgrade_check(self) -> None:
        problems = registry.check_claim(V1, "skill", "hello-ainexlayer", "0.0.1", "revanth-ainl", OWNER_ID)
        self.assertEqual(errors(problems), [])

    def test_maintainer_managed(self) -> None:
        reg = copy.deepcopy(V2)
        reg["owners"]["managed"] = {
            "kind": "plugin",
            "publisher": "community-dev",
            "publisher_id": OTHER_ID,
            "version": "1.0.0",
            "claimed_at": "2026-10-31T00:00:00Z",
            "maintainer_managed": True,
        }
        allowed = registry.check_claim(reg, "plugin", "managed", "1.1.0", "revanth-ainl", OWNER_ID, trusted=TRUSTED)
        self.assertEqual(allowed, [])
        # Without the trusted list, or on a package that is not maintainer-managed, it is refused.
        self.assertEqual(
            fields(registry.check_claim(reg, "plugin", "managed", "1.1.0", "revanth-ainl", OWNER_ID)), {"publisher_id"}
        )
        reg["owners"]["managed"]["maintainer_managed"] = False
        self.assertEqual(
            fields(registry.check_claim(reg, "plugin", "managed", "1.1.0", "revanth-ainl", OWNER_ID, trusted=TRUSTED)),
            {"publisher_id"},
        )


class ApplyClaimTest(unittest.TestCase):
    def test_new_claim(self) -> None:
        before = copy.deepcopy(V2)
        out = registry.apply_claim(V2, "agent", "helper", "1.0.0", "someone", OTHER_ID, claimed_at="2026-10-31T00:00:00Z")
        self.assertEqual(V2, before)
        self.assertEqual(
            out["owners"]["helper"],
            {
                "kind": "agent",
                "publisher": "someone",
                "publisher_id": OTHER_ID,
                "version": "1.0.0",
                "claimed_at": "2026-10-31T00:00:00Z",
            },
        )

    def test_update_keeps_claim_time_and_moves_version(self) -> None:
        out = registry.apply_claim(
            V2, "skill", "hello-ainexlayer", "1.3.0", "renamed", OWNER_ID, claimed_at="2030-01-01T00:00:00Z"
        )
        owner = out["owners"]["hello-ainexlayer"]
        self.assertEqual(owner["claimed_at"], "2026-10-10T00:00:00Z")
        self.assertEqual(owner["version"], "1.3.0")
        self.assertEqual(owner["publisher"], "renamed")

    def test_update_fills_a_pending_id(self) -> None:
        out = registry.apply_claim(V1, "skill", "hello-ainexlayer", "1.0.0", "revanth-ainl", OWNER_ID, claimed_at="x")
        self.assertEqual(out["owners"]["hello-ainexlayer"]["publisher_id"], OWNER_ID)

    def test_maintainer_publish_keeps_the_owner(self) -> None:
        reg = copy.deepcopy(V2)
        reg["owners"]["managed"] = {
            "kind": "plugin",
            "publisher": "community-dev",
            "publisher_id": OTHER_ID,
            "version": "1.0.0",
            "claimed_at": "2026-10-31T00:00:00Z",
            "maintainer_managed": True,
        }
        out = registry.apply_claim(reg, "plugin", "managed", "1.1.0", "revanth-ainl", OWNER_ID, claimed_at="x")
        self.assertEqual(out["owners"]["managed"]["publisher_id"], OTHER_ID)
        self.assertEqual(out["owners"]["managed"]["version"], "1.1.0")

    def test_idempotent(self) -> None:
        once = registry.apply_claim(V2, "skill", "hello-ainexlayer", "1.3.0", "revanth-ainl", OWNER_ID, claimed_at="x")
        twice = registry.apply_claim(once, "skill", "hello-ainexlayer", "1.3.0", "revanth-ainl", OWNER_ID, claimed_at="y")
        self.assertEqual(once, twice)


class TrustedTest(unittest.TestCase):
    def test_trust_needs_a_confirmed_id(self) -> None:
        self.assertEqual(registry.trust_for(OWNER_ID, TRUSTED), "official")
        self.assertEqual(registry.trust_for(OTHER_ID, TRUSTED), "community")
        self.assertEqual(registry.trust_for(None, TRUSTED), "community")
        self.assertEqual(registry.trust_for(OWNER_ID, {}), "community")

    def test_load_skips_pending_entries(self) -> None:
        data = {
            "version": 1,
            "publishers": [
                {"login": "a", "id": 5, "trust": "official", "roles": []},
                {"login": "b", "id": None, "status": "pending", "trust": "official", "roles": []},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trusted-publishers.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(sorted(registry.load_trusted(path)), [5])
            self.assertEqual(registry.load_trusted(Path(tmp) / "missing.json"), {})
        self.assertEqual([p for p in registry.check_trusted(data) if p.is_error], [])

    def test_shape_errors(self) -> None:
        bad = {"version": 1, "publishers": [{"login": "a", "id": "5", "trust": "gold", "roles": ["admin"]}, {"id": None}]}
        messages = " | ".join(p.message for p in registry.check_trusted(bad) if p.is_error)
        for expected in ("positive integer", "trust must be", "roles may only", "GitHub login", "pending"):
            self.assertIn(expected, messages)
        self.assertTrue(registry.check_trusted([]))

    def test_this_repository_file(self) -> None:
        data = json.loads((ROOT / "trusted-publishers.json").read_text(encoding="utf-8"))
        self.assertEqual([p for p in registry.check_trusted(data) if p.is_error], [])
        trusted = registry.load_trusted(ROOT / "trusted-publishers.json")
        self.assertEqual(registry.trust_for(OWNER_ID, trusted), "official")


if __name__ == "__main__":
    unittest.main()
