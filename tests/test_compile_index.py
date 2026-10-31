"""scripts/compile_index.py: the feed AIAgentLayer reads (docs/index.json)."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import compile_index
from ainexlayer_marketplace import hashing

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "submissions"
NOW = "2026-11-01T00:00:00Z"
OTHER_ID = 1234567

#: The fields the app's CommunityIndex reads, in the order the feed has always used.
SKILL_FIELDS = [
    "name", "title", "description", "publisher", "version", "published_at", "categories",
    "source_url", "raw_url", "flavor", "compatible_agents",
]  # fmt: skip
PLUGIN_FIELDS = ["name", "publisher", "version", "published_at", "source_url", "plugin_json", "mcp_json", "usage_card"]
AGENT_FIELDS = [
    "name", "title", "description", "publisher", "version", "published_at", "categories", "source_url", "agent",
]  # fmt: skip


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args)


class CompileTest(unittest.TestCase):
    """A copy of this repository plus one plugin and one agent laid out by hand."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for rel in ("skills", "docs"):
            shutil.copytree(ROOT / rel, self.root / rel)
        for rel in ("registry.json", "trusted-publishers.json"):
            shutil.copy(ROOT / rel, self.root / rel)
        reg = json.loads((self.root / "registry.json").read_text(encoding="utf-8"))

        plugin = fixture("plugin-http.json")
        folder = self.root / "plugins" / plugin["name"]
        write_json(folder / "plugin.json", plugin["plugin_json"])
        write_json(folder / "mcp.json", plugin["mcp_json"])
        (folder / "usage_card.md").write_text(plugin["usage_card"], encoding="utf-8")
        for skill in plugin["skills"]:
            path = folder / "skills" / skill["name"] / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(skill["skill_md"], encoding="utf-8")

        agent = fixture("agent-template.json")
        folder = self.root / "agents" / agent["name"]
        write_json(folder / "agent.json", agent["agent"])
        write_json(folder / "listing.json", {k: agent[k] for k in ("version", "title", "description", "categories")})

        for doc in (plugin, agent):
            reg["owners"][doc["name"]] = {
                "kind": doc["kind"],
                "publisher": "community-dev",
                "publisher_id": OTHER_ID,
                "version": doc["version"],
                "claimed_at": NOW,
            }
        write_json(self.root / "registry.json", reg)
        self.plugin, self.agent = plugin, agent

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(self, previous=None, now=NOW) -> dict:
        return compile_index.build_index(self.root, previous, now=now)

    def run_main(self, *extra: str) -> int:
        return quiet(compile_index.main, ["--root", str(self.root), *extra])

    def feed(self) -> dict:
        return json.loads((self.root / "docs" / "index.json").read_text(encoding="utf-8"))

    # -- determinism ---------------------------------------------------------

    def test_same_input_same_output(self) -> None:
        self.assertEqual(compile_index.render(self.build()), compile_index.render(self.build()))

    def test_second_compile_changes_nothing(self) -> None:
        self.assertEqual(self.run_main(), 0)
        first = (self.root / "docs" / "index.json").read_bytes()
        self.assertEqual(self.run_main(), 0)
        self.assertEqual((self.root / "docs" / "index.json").read_bytes(), first)
        self.assertEqual(self.run_main("--check"), 0)

    def test_check_detects_a_stale_feed_and_writes_nothing(self) -> None:
        before = (self.root / "docs" / "index.json").read_bytes()
        self.assertEqual(self.run_main("--check"), 1)
        self.assertEqual((self.root / "docs" / "index.json").read_bytes(), before)

    def test_revision_and_timestamps(self) -> None:
        first = self.build(now=NOW)
        again = self.build(copy.deepcopy(first), now="2027-01-01T00:00:00Z")
        self.assertEqual(again, first, "unchanged content keeps revision and generated_at")
        skill = self.root / "skills" / "hello-ainexlayer" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")
        changed = self.build(copy.deepcopy(first), now="2027-01-01T00:00:00Z")
        self.assertEqual(changed["revision"], first["revision"] + 1)
        self.assertEqual(changed["generated_at"], "2027-01-01T00:00:00Z")
        # Same version: its first publish time stays.
        self.assertEqual(changed["skills"][0]["published_at"], first["skills"][0]["published_at"])

    def test_new_version_gets_a_new_publish_time(self) -> None:
        first = self.build(now=NOW)
        listing = self.root / "agents" / "research-buddy" / "listing.json"
        data = json.loads(listing.read_text(encoding="utf-8"))
        data["version"] = "1.1.0"
        write_json(listing, data)
        reg = json.loads((self.root / "registry.json").read_text(encoding="utf-8"))
        reg["owners"]["research-buddy"]["version"] = "1.1.0"
        write_json(self.root / "registry.json", reg)
        later = self.build(copy.deepcopy(first), now="2027-01-01T00:00:00Z")
        self.assertEqual(later["agents"][0]["published_at"], "2027-01-01T00:00:00Z")

    def test_source_date_epoch(self) -> None:
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1798761600"}):
            self.assertEqual(self.build(now=None)["generated_at"], "2027-01-01T00:00:00Z")

    def test_entries_sorted_by_name(self) -> None:
        shutil.copytree(self.root / "skills" / "hello-ainexlayer", self.root / "skills" / "aaa-first")
        skill = self.root / "skills" / "aaa-first" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8").replace("hello-ainexlayer", "aaa-first"), encoding="utf-8")
        reg = json.loads((self.root / "registry.json").read_text(encoding="utf-8"))
        reg["owners"]["aaa-first"] = dict(reg["owners"]["hello-ainexlayer"])
        write_json(self.root / "registry.json", reg)
        self.assertEqual([e["name"] for e in self.build()["skills"]], ["aaa-first", "hello-ainexlayer"])

    # -- the app's contract --------------------------------------------------

    def test_existing_hello_entry_is_unchanged(self) -> None:
        committed = json.loads((ROOT / "docs" / "index.json").read_text(encoding="utf-8"))
        old = next(e for e in committed["skills"] if e["name"] == "hello-ainexlayer")
        new = next(e for e in self.build(committed)["skills"] if e["name"] == "hello-ainexlayer")
        self.assertEqual(list(new)[: len(SKILL_FIELDS)], SKILL_FIELDS)
        self.assertEqual({k: new[k] for k in SKILL_FIELDS}, {k: old[k] for k in SKILL_FIELDS})
        self.assertEqual(old["published_at"], "2026-10-10T04:11:16Z")

    def test_field_order_and_urls(self) -> None:
        index = self.build()
        self.assertEqual(list(index), ["revision", "generated_at", "plugins", "skills", "agents"])
        plugin, skill, agent = index["plugins"][0], index["skills"][0], index["agents"][0]
        self.assertEqual(list(plugin)[: len(PLUGIN_FIELDS)], PLUGIN_FIELDS)
        self.assertEqual(list(agent)[: len(AGENT_FIELDS)], AGENT_FIELDS)
        base = "https://github.com/revanth-ainl/ainexlayer-marketplace/tree/main"
        self.assertEqual(plugin["source_url"], f"{base}/plugins/error-tracker")
        self.assertEqual(agent["source_url"], f"{base}/agents/research-buddy")
        self.assertEqual(
            skill["raw_url"],
            "https://raw.githubusercontent.com/revanth-ainl/ainexlayer-marketplace/main/skills/hello-ainexlayer/SKILL.md",
        )
        self.assertIsInstance(index["revision"], int)

    def test_agent_listing_comes_from_listing_json(self) -> None:
        agent = self.build()["agents"][0]
        self.assertEqual(agent["agent"], self.agent["agent"], "agent.json is published verbatim")
        self.assertEqual(agent["title"], "Research buddy")
        self.assertEqual(agent["description"], self.agent["description"])
        self.assertEqual(agent["categories"], ["research"])
        self.assertEqual(agent["schema"], 1)
        for key in ("version", "description", "categories"):
            self.assertNotIn(key, agent["agent"])

    def test_plugin_entry(self) -> None:
        plugin = self.build()["plugins"][0]
        self.assertEqual(plugin["plugin_json"], self.plugin["plugin_json"])
        self.assertEqual(plugin["mcp_json"], self.plugin["mcp_json"])
        self.assertEqual(plugin["usage_card"], self.plugin["usage_card"])
        self.assertEqual(plugin["transport"], "streamable-http")
        self.assertEqual(
            plugin["bundled_skills"],
            [
                {
                    "name": "triage-errors",
                    "description": "Group new errors by cause and suggest the first one to fix.",
                    "sha256": hashing.file_sha256(self.root / "plugins/error-tracker/skills/triage-errors/SKILL.md"),
                }
            ],
        )

    def test_publisher_and_trust(self) -> None:
        index = self.build()
        skill, plugin = index["skills"][0], index["plugins"][0]
        self.assertEqual((skill["publisher"], skill["publisher_id"], skill["trust"]), ("revanth-ainl", 340421019, "official"))
        self.assertEqual((plugin["publisher"], plugin["publisher_id"], plugin["trust"]), ("community-dev", OTHER_ID, "community"))
        (self.root / "trusted-publishers.json").unlink()
        self.assertEqual(self.build()["skills"][0]["trust"], "community")

    def test_pending_id_is_omitted_and_not_trusted(self) -> None:
        reg = json.loads((self.root / "registry.json").read_text(encoding="utf-8"))
        reg["owners"]["hello-ainexlayer"]["publisher_id"] = None
        write_json(self.root / "registry.json", reg)
        skill = self.build()["skills"][0]
        self.assertNotIn("publisher_id", skill)
        self.assertEqual(skill["trust"], "community")

    # -- hashes --------------------------------------------------------------

    def test_hashes(self) -> None:
        index = self.build()
        skill_dir = self.root / "skills" / "hello-ainexlayer"
        skill = index["skills"][0]
        self.assertEqual(skill["raw_sha256"], hashing.file_sha256(skill_dir / "SKILL.md"))
        manifest = f"{hashing.file_sha256(skill_dir / 'SKILL.md')}  SKILL.md\n"
        self.assertEqual(skill["content_sha256"], hashlib.sha256(manifest.encode()).hexdigest())
        (skill_dir / ".DS_Store").write_bytes(b"\x00")
        self.assertEqual(self.build()["skills"][0]["content_sha256"], skill["content_sha256"])
        plugin_dir = self.root / "plugins" / "error-tracker"
        self.assertEqual(
            hashing.package_files(plugin_dir),
            ["mcp.json", "plugin.json", "skills/triage-errors/SKILL.md", "usage_card.md"],
        )
        (plugin_dir / "usage_card.md").write_text("# changed\n", encoding="utf-8")
        self.assertNotEqual(self.build()["plugins"][0]["content_sha256"], index["plugins"][0]["content_sha256"])

    # -- refusal -------------------------------------------------------------

    def test_invalid_package_stops_the_compile(self) -> None:
        (self.root / "skills" / "hello-ainexlayer" / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")
        with self.assertRaises(compile_index.CompileError):
            self.build()
        before = (self.root / "docs" / "index.json").read_bytes()
        self.assertEqual(self.run_main(), 1)
        self.assertEqual((self.root / "docs" / "index.json").read_bytes(), before)

    def test_readme_files_are_not_packages(self) -> None:
        for kind in ("skills", "plugins", "agents"):
            (self.root / kind / "README.md").write_text("# Read me\n", encoding="utf-8")
        index = self.build()
        self.assertEqual([len(index[s]) for s in ("plugins", "skills", "agents")], [1, 1, 1])


class RepositoryFeedTest(unittest.TestCase):
    def test_committed_feed_is_up_to_date(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(ROOT / "scripts" / "compile_index.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
