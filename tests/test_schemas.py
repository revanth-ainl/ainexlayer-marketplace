"""schemas/: generated from the validator's own constants, so they agree with it.

Validation of real documents against these schemas needs the jsonschema
package; tests/app_contract_check.py does that in the app's environment.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ainexlayer_marketplace import registry, rules, schemas, submission

EXPECTED = {
    "submission.schema.json", "plugin.schema.json", "mcp.schema.json", "skill-frontmatter.schema.json",
    "agent-template.schema.json", "registry.schema.json", "trusted-publishers.schema.json", "feed.schema.json",
}  # fmt: skip


class SchemaFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.built = schemas.build_all()

    def test_every_schema_is_committed_as_generated(self) -> None:
        self.assertEqual(set(self.built), EXPECTED)
        self.assertEqual({p.name for p in (ROOT / "schemas").glob("*.json")}, EXPECTED)
        for name, schema in self.built.items():
            on_disk = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
            self.assertEqual(on_disk, schema, name)
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertTrue(schema["$id"].endswith("/schemas/" + name))

    def test_local_refs_resolve(self) -> None:
        def refs(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "$ref":
                        yield value
                    else:
                        yield from refs(value)
            elif isinstance(node, list):
                for value in node:
                    yield from refs(value)

        for name, schema in self.built.items():
            for ref in refs(schema):
                if ref.startswith("#/$defs/"):
                    self.assertIn(ref.split("/")[-1], schema["$defs"], f"{name}: {ref}")
                else:
                    self.assertIn(ref, EXPECTED, f"{name}: {ref}")


class AgreementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.s = schemas.build_all()

    def test_names_and_versions(self) -> None:
        frontmatter = self.s["skill-frontmatter.schema.json"]["properties"]
        self.assertEqual(frontmatter["name"]["pattern"], rules.NAME_RE.pattern)
        self.assertEqual(frontmatter["version"]["pattern"], rules.SEMVER_RE.pattern)
        self.assertEqual(frontmatter["flavor"]["enum"], list(rules.SKILL_FLAVORS))
        self.assertEqual(frontmatter["compatible_agents"]["maxItems"], rules.MAX_COMPATIBLE_AGENTS)
        self.assertEqual(self.s["skill-frontmatter.schema.json"]["required"], list(rules.REQUIRED_SKILL_FRONTMATTER))

    def test_submission_variants_match_allowed_keys(self) -> None:
        for variant in self.s["submission.schema.json"]["oneOf"]:
            kind = variant["properties"]["kind"]["const"]
            self.assertEqual(set(variant["properties"]), set(submission.ALLOWED_KEYS[kind]), kind)
            self.assertFalse(variant["additionalProperties"])

    def test_plugin_auth_modes_match(self) -> None:
        defs = self.s["plugin.schema.json"]["$defs"]
        for mode, (required, optional) in rules.AUTH_FIELDS.items():
            block = defs[f"auth_{mode}"]
            self.assertEqual(set(block["required"]), {"mode", *required}, mode)
            self.assertEqual(set(block["properties"]), {"mode", *required, *optional}, mode)
            self.assertFalse(block["additionalProperties"])
            for forbidden in rules.FORBIDDEN_AUTH_FIELDS:
                self.assertNotIn(forbidden, block["properties"])
        namespaces = self.s["plugin.schema.json"]["properties"]["extensions"]["properties"]
        self.assertEqual(set(namespaces), set(rules.EXTENSION_NAMESPACES))

    def test_mcp_rules(self) -> None:
        mcp = self.s["mcp.schema.json"]
        self.assertEqual(mcp["properties"]["mcpServers"]["maxProperties"], 1)
        transports = {mcp["$defs"][d]["properties"]["type"]["const"] for d in ("http", "stdio")}
        self.assertEqual(transports, set(rules.MCP_TRANSPORTS))
        env = mcp["$defs"]["stdio"]["properties"]["env"]
        self.assertEqual(env["propertyNames"]["not"]["enum"], sorted(rules.RESERVED_ENV_NAMES))
        self.assertEqual(env["additionalProperties"]["pattern"], rules.ENV_PLACEHOLDER_RE.pattern)

    def test_agent_template_keys(self) -> None:
        agent = self.s["agent-template.schema.json"]
        self.assertEqual(set(agent["properties"]), set(rules.TEMPLATE_KEYS))
        self.assertFalse(agent["additionalProperties"])
        self.assertEqual(agent["properties"]["tier"]["enum"], list(rules.TIERS))
        self.assertEqual(agent["properties"]["schema"]["const"], rules.TEMPLATE_SCHEMA)

    def test_registry_and_trust(self) -> None:
        owner = self.s["registry.schema.json"]["properties"]["owners"]["additionalProperties"]
        self.assertEqual(set(owner["properties"]), set(registry.OWNER_KEYS))
        self.assertEqual(owner["properties"]["kind"]["enum"], list(rules.KINDS))
        trusted = self.s["trusted-publishers.schema.json"]["properties"]["publishers"]["items"]
        self.assertEqual(trusted["properties"]["trust"]["enum"], list(registry.TRUST_LEVELS))

    def test_feed_requires_what_compile_index_writes(self) -> None:
        feed = json.loads((ROOT / "docs" / "index.json").read_text(encoding="utf-8"))
        skill_def = self.s["feed.schema.json"]["$defs"]["skill"]
        for entry in feed["skills"]:
            self.assertTrue(set(skill_def["required"]) <= set(entry), entry["name"])
            self.assertTrue(set(entry) <= set(skill_def["properties"]), entry["name"])


if __name__ == "__main__":
    unittest.main()
