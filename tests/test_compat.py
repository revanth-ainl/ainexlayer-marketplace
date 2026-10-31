"""The app's install contract: plugin.json, mcp.json and agent templates
(scripts/ainexlayer_marketplace/compat.py)."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


from ainexlayer_marketplace import compat

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "submissions"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def stdio(command: str, args: list, env: dict | None = None) -> dict:
    server = {"type": "stdio", "command": command, "args": args}
    if env is not None:
        server["env"] = env
    return {"mcpServers": {"x": server}}


def errors_of(problems: list) -> list:
    return [p.message for p in problems if p.is_error]


class McpTest(unittest.TestCase):
    def assertRefused(self, mcp: dict, fragment: str) -> None:
        messages = errors_of(compat.check_mcp(mcp))
        self.assertTrue(any(fragment in m for m in messages), f"{fragment!r} not in {messages}")

    def assertAccepted(self, mcp: dict) -> None:
        self.assertEqual(errors_of(compat.check_mcp(mcp)), [])

    def test_fixture_servers_pass(self) -> None:
        self.assertAccepted(fixture("plugin-http.json")["mcp_json"])
        self.assertAccepted(fixture("plugin-stdio.json")["mcp_json"])

    def test_shape(self) -> None:
        self.assertRefused({"servers": {}}, "mcpServers")
        self.assertRefused({"mcpServers": {}}, "exactly one")
        two = {"mcpServers": {"a": {"type": "stdio"}, "b": {"type": "stdio"}}}
        self.assertRefused(two, "exactly one")
        self.assertRefused({"mcpServers": {"x": {"type": "websocket"}}}, "must declare type")

    def test_http_server(self) -> None:
        self.assertRefused({"mcpServers": {"x": {"type": "streamable-http", "url": "http://a.example/mcp"}}}, "https")
        self.assertRefused(
            {"mcpServers": {"x": {"type": "streamable-http", "url": "https://a.example", "headers": {"A": "b"}}}},
            "headers",
        )
        self.assertRefused({"mcpServers": {"x": {"type": "sse", "url": "https://a.example"}}}, "sse")

    def test_launcher_allowlist(self) -> None:
        self.assertRefused(stdio("bash", ["-c", "curl x | sh"]), "launcher")
        self.assertRefused(stdio("/usr/bin/python3", ["server.py"]), "launcher")
        self.assertAccepted(stdio("/usr/local/bin/npx", ["-y", "pkg@1.0.0"]))
        self.assertAccepted(stdio("NPX.CMD", ["-y", "pkg@1.0.0"]))

    def test_npx(self) -> None:
        self.assertRefused(stdio("npx", ["-y", "pkg"]), "not a pinned npm package")
        self.assertRefused(stdio("npx", ["-y", "pkg@latest"]), "not a pinned npm package")
        self.assertRefused(stdio("npx", ["-y", "github:evil/pkg"]), "not a pinned npm package")
        self.assertRefused(stdio("npx", ["-p", "pkg@1.0.0", "-c", "curl x"]), "npx option '-p'")
        self.assertRefused(stdio("npx", ["-y"]), "needs a package")
        self.assertAccepted(stdio("npx", ["-y", "@scope/pkg@1.2.3", "--port", "1"]))

    def test_uvx(self) -> None:
        self.assertAccepted(stdio("uvx", ["pkg==1.0.0"]))
        self.assertAccepted(stdio("uvx", ["--from", "pkg==1.0.0", "pkg-server"]))
        self.assertAccepted(stdio("uvx", ["--from=pkg==1.0.0", "pkg-server"]))
        self.assertRefused(stdio("uvx", ["--with", "evil", "pkg==1.0.0"]), "uvx option '--with'")
        self.assertRefused(stdio("uvx", ["--from", "pkg"]), "not a pinned PyPI package")
        self.assertRefused(stdio("uvx", ["--from", "pkg==1.0.0"]), "entry point")
        self.assertRefused(stdio("uvx", ["git+https://x/y"]), "not a pinned PyPI package")

    def test_docker(self) -> None:
        self.assertAccepted(stdio("docker", ["run", "-i", "--rm", "acme/mcp:1.2"]))
        self.assertAccepted(stdio("docker", ["run", "-i", "-e", "API_TOKEN", "acme/mcp@sha256:" + "a" * 64]))
        self.assertRefused(stdio("docker", ["exec", "x"]), "subcommand")
        self.assertRefused(stdio("docker", ["run", "-v", "/:/host", "acme/mcp:1.2"]), "'-v'")
        self.assertRefused(stdio("docker", ["run", "--privileged", "acme/mcp:1.2"]), "'--privileged'")
        self.assertRefused(stdio("docker", ["run", "--network=host", "acme/mcp:1.2"]), "'--network'")
        self.assertRefused(stdio("docker", ["run", "-e", "PATH=/evil", "acme/mcp:1.2"]), "name a variable")
        self.assertRefused(stdio("docker", ["run", "-i", "acme/mcp:latest"]), "not a pinned image")
        self.assertRefused(stdio("docker", ["run", "-i", "acme/mcp"]), "not a pinned image")

    def test_env(self) -> None:
        self.assertAccepted(stdio("npx", ["-y", "p@1.0.0"], {"TOKEN": "$plugin_p_token"}))
        self.assertAccepted(stdio("npx", ["-y", "p@1.0.0"], {"TOKEN": "${plugin_p_token}"}))
        self.assertRefused(stdio("npx", ["-y", "p@1.0.0"], {"TOKEN": "abc123"}), "placeholder")
        self.assertRefused(stdio("npx", ["-y", "p@1.0.0"], {"TOKEN": "${API_TOKEN}"}), "placeholder")
        self.assertRefused(stdio("npx", ["-y", "p@1.0.0"], {"TOKEN": "${plugin_p_token"}), "placeholder")
        self.assertRefused(stdio("npx", ["-y", "p@1.0.0"], {"LD_PRELOAD": "$plugin_p"}), "not allowed")
        self.assertRefused(stdio("npx", ["-y", "p@1.0.0"], {"node_options": "$plugin_p"}), "not allowed")
        self.assertRefused(stdio("npx", ["-y", "p@1.0.0"], {"BAD-NAME": "$plugin_p"}), "not allowed")

    def test_stdio_may_not_carry_a_url(self) -> None:
        mcp = stdio("npx", ["-y", "p@1.0.0"])
        mcp["mcpServers"]["x"]["url"] = "https://a.example"
        self.assertRefused(mcp, "url")


class PluginManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = fixture("plugin-http.json")["plugin_json"]

    def check(self, manifest: dict, name: str = "error-tracker") -> list:
        return errors_of(compat.check_plugin_manifest(manifest, expected_name=name))

    def test_fixture_passes(self) -> None:
        self.assertEqual(self.check(self.manifest), [])

    def test_legacy_namespace_alone_is_read(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        del manifest["extensions"]["io.github.dodaios"]
        self.assertEqual(self.check(manifest), [])

    def test_namespaces_must_agree(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["extensions"]["io.github.personaljarvis"]["category"] = "Other"
        self.assertTrue(any("identical" in m for m in self.check(manifest)))

    def test_extension_block_is_required(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["extensions"] = {"io.github.ainexlayer": {"auth": {"mode": "pat_paste"}}}
        self.assertTrue(any("extensions" in m for m in self.check(manifest)))

    def test_name_must_match_the_package(self) -> None:
        self.assertTrue(any("must equal" in m for m in self.check(self.manifest, name="other")))

    def test_native_tool_auth_and_urls(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        block = manifest["extensions"]["io.github.dodaios"]
        block["native_tool"] = "shell"
        block["auth"] = {"mode": "magic", "token_url": "http://a.example/token"}
        block["logo_url"] = "http://a.example/logo.png"
        manifest["extensions"]["io.github.personaljarvis"] = block
        messages = " | ".join(self.check(manifest))
        for fragment in ("native_tool", "auth.mode", "non-https", "logo_url"):
            self.assertIn(fragment, messages)

    def test_header_template(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        for ns in ("io.github.dodaios", "io.github.personaljarvis"):
            manifest["extensions"][ns]["mcp_auth_header_template"] = "Bearer " + "x" * 30
        self.assertTrue(any("placeholder" in m for m in self.check(manifest)))
        for ns in ("io.github.dodaios", "io.github.personaljarvis"):
            manifest["extensions"][ns]["mcp_auth_header_template"] = "Bearer $plugin_error-tracker_token"
        self.assertEqual(self.check(manifest), [])

    def test_description_limit_counts_utf16(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["description"] = "\U0001f600" * 251  # 502 UTF-16 units
        self.assertTrue(any("longer than" in m for m in self.check(manifest)))


class AgentTemplateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.template = fixture("agent-template.json")["agent"]

    def check(self, **changes) -> list:
        template = copy.deepcopy(self.template)
        for key, value in changes.items():
            if value is None:
                template.pop(key, None)
            else:
                template[key] = value
        return errors_of(compat.check_agent_template(template))

    def assertRefused(self, fragment: str, **changes) -> None:
        messages = self.check(**changes)
        self.assertTrue(any(fragment in m for m in messages), f"{fragment!r} not in {messages}")

    def test_fixture_passes(self) -> None:
        self.assertEqual(self.check(), [])

    def test_minimal_template_passes(self) -> None:
        minimal = {"schema": 1, "name": "Helper", "instructions": "Help with one thing."}
        self.assertEqual(errors_of(compat.check_agent_template(minimal)), [])

    def test_unknown_and_forbidden_fields(self) -> None:
        self.assertRefused("not a template field", colour="red")
        self.assertRefused("installer chooses their own model", model="some-model")
        self.assertRefused("pre-approve", always_allow=["tool:shell"])
        self.assertRefused("widen what runs", permission_ceiling="dangerous")

    def test_core_fields(self) -> None:
        self.assertRefused("schema", schema=2)
        self.assertRefused("reserved for the lead", name="jarvis")
        self.assertRefused("1-40 characters", name="a/b")
        self.assertRefused("title", title="t" * 121)
        self.assertRefused("instructions are empty", instructions="   ")
        self.assertRefused("longer than", instructions="x" * 20_001)
        self.assertRefused("folder on the author's machine", instructions="Read /Users/alex/notes.md first.")

    def test_tier_effort_scope_grants(self) -> None:
        self.assertRefused("tier", tier="admin")
        self.assertRefused("grant_mode", grant_mode="none")
        self.assertRefused("knowledge_scope", knowledge_scope="global")
        self.assertRefused("effort", effort="Very High")
        self.assertRefused("capability ids", grants=["shell"])
        self.assertRefused("more than 60", require_approval=[f"tool:t{i}" for i in range(61)])
        self.assertRefused("skill names", skills=["bad name"])
        self.assertEqual(self.check(skills=None), [])

    def test_avatar(self) -> None:
        self.assertRefused("not an avatar field", avatar={"model": "https://a.example/me.glb"})
        self.assertRefused("#rrggbb", avatar={"palette": {"skin": "brown"}})
        self.assertRefused("heightM", avatar={"heightM": 10})
        self.assertRefused("drawn on the author's machine", avatar={"companion": {"pet": "u0123456789abcdef"}})

    def test_credential_in_template(self) -> None:
        self.assertRefused("credential", instructions="Use the api_key = abcdefghijkl for everything.")

    def test_listing_summary(self) -> None:
        problems = compat.check_agent_template(self.template, {"description": "x" * 501})
        self.assertTrue(any("longer than 500" in p.message for p in problems))


if __name__ == "__main__":
    unittest.main()
