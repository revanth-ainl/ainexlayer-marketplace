"""JSON Schemas (draft 2020-12) for every format this repository reads or writes.

They are documentation for publishers and tools, generated from the same
constants the validator uses (``scripts/export_rules.py`` writes them to
``schemas/``), so a pattern, limit or allowed value cannot drift from the
code. The Python validator stays the authority: it also checks what a
schema cannot express (exact launcher pins, credential scans, UTF-16
description length, file layout, ownership). Anything the validator
accepts, these schemas accept.
"""

from __future__ import annotations

from typing import Any

from . import registry, rules, submission

DIALECT = "https://json-schema.org/draft/2020-12/schema"
BASE_ID = "https://raw.githubusercontent.com/revanth-ainl/ainexlayer-marketplace/main/schemas/"
AUTHORITY = "Documentation; scripts/validate.py is the authority and checks more than a schema can."

SHA256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
HTTPS_URL = {"type": "string", "pattern": "^[Hh][Tt][Tt][Pp][Ss]://"}
TIMESTAMP = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"}


def _name() -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": rules.NAME_RE.pattern,
        "not": {"pattern": "|".join(s.replace(".", r"\.") for s in rules.FORBIDDEN_NAME_SUBSTRINGS)},
    }


def _semver() -> dict[str, Any]:
    return {"type": "string", "pattern": rules.SEMVER_RE.pattern}


def _login() -> dict[str, Any]:
    return {"type": "string", "pattern": rules.GITHUB_LOGIN_RE.pattern}


def _categories() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": rules.MAX_CATEGORIES,
        "items": {"type": "string", "minLength": 1, "maxLength": submission.MAX_CATEGORY_CHARS},
    }


def _compatible_agents() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": rules.MAX_COMPATIBLE_AGENTS,
        "items": {"type": "string", "minLength": 1},
        "description": f"Each name at most {rules.MAX_COMPATIBLE_AGENT_CHARS} characters once spaces are collapsed.",
    }


def _description() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "description": f"At most {rules.MAX_DESCRIPTION_CHARS} UTF-16 code units (checked by the validator).",
    }


def _schema(file: str, title: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"$schema": DIALECT, "$id": BASE_ID + file, "title": title, "$comment": AUTHORITY, **body}


# ---------------------------------------------------------------------------
# Field type table shared by the auth blocks and the extension block
# ---------------------------------------------------------------------------


def _instance_url() -> dict[str, Any]:
    required, optional = rules.INSTANCE_URL_FIELDS
    return {
        "type": "object",
        "required": sorted(required),
        "properties": {k: _field_type(k, v, k in optional) for k, v in {**required, **optional}.items()},
        "additionalProperties": False,
    }


def _field_type(key: str, expected: Any, optional: bool) -> dict[str, Any]:
    if isinstance(expected, tuple):
        out: dict[str, Any] = {"enum": [*expected, None] if optional else list(expected)}
        return out
    if expected == "instance_url":
        base = _instance_url()
    elif expected == "list_str":
        base = {"type": "array", "items": {"type": "string"}}
    else:
        base = {"type": {"str": "string", "int": "integer", "bool": "boolean"}[expected]}
        if expected == "str" and key.endswith("_url"):
            base["pattern"] = HTTPS_URL["pattern"]
    if optional:
        return {"anyOf": [base, {"type": "null"}]}
    return base


def _auth_mode(mode: str) -> dict[str, Any]:
    required, optional = rules.AUTH_FIELDS[mode]
    properties: dict[str, Any] = {"mode": {"const": mode}}
    properties.update({k: _field_type(k, v, False) for k, v in required.items()})
    properties.update({k: _field_type(k, v, True) for k, v in optional.items()})
    return {
        "type": "object",
        "required": ["mode", *sorted(required)],
        "properties": properties,
        "additionalProperties": False,
    }


def plugin_schema() -> dict[str, Any]:
    modes = sorted(rules.AUTH_MODES)
    extension = {
        "type": "object",
        "required": ["auth"],
        "properties": {
            "auth": {"oneOf": [{"$ref": f"#/$defs/auth_{m}"} for m in modes]},
            "fallback_auth": {"$ref": "#/$defs/auth_pat_paste"},
            "native_tool": {"enum": [None, False, "", 0], "description": "Community plugins cannot bind one."},
            "logo_url": HTTPS_URL,
            "mcp_auth_header_template": {
                "type": "string",
                "pattern": rules.HEADER_PLACEHOLDER_RE.pattern,
                "description": "Must reference a $plugin_… placeholder and carry no literal token.",
            },
            **{k: _field_type(k, v, True) for k, v in rules.EXTENSION_FIELD_TYPES.items()},
        },
    }
    return _schema(
        "plugin.schema.json",
        "Plugin manifest (plugin.json, Agent Plugins format with the AIAgentLayer extension block)",
        {
            "type": "object",
            "required": ["name", "description", "extensions"],
            "properties": {
                "name": _name(),
                "version": _semver(),
                "description": _description(),
                "extensions": {
                    "type": "object",
                    "properties": {ns: {"$ref": "#/$defs/extension"} for ns in rules.EXTENSION_NAMESPACES},
                    "anyOf": [{"required": [ns]} for ns in rules.EXTENSION_NAMESPACES],
                },
            },
            "$defs": {
                "extension": extension,
                **{f"auth_{m}": _auth_mode(m) for m in modes},
            },
        },
    )


def mcp_schema() -> dict[str, Any]:
    launcher = "^(?:.*[/\\\\])?(?:" + "|".join(rules.STDIO_LAUNCHERS) + ")(?:\\.exe|\\.cmd)?$"
    return _schema(
        "mcp.schema.json",
        "MCP configuration (mcp.json): exactly one server",
        {
            "type": "object",
            "required": ["mcpServers"],
            "properties": {
                "mcpServers": {
                    "type": "object",
                    "minProperties": 1,
                    "maxProperties": 1,
                    "additionalProperties": {"oneOf": [{"$ref": "#/$defs/http"}, {"$ref": "#/$defs/stdio"}]},
                }
            },
            "$defs": {
                "http": {
                    "type": "object",
                    "required": ["type", "url"],
                    "properties": {"type": {"const": "streamable-http"}, "url": HTTPS_URL},
                    "not": {"required": ["headers"]},
                    "description": "No static headers: the app adds the token from mcp_auth_header_template.",
                },
                "stdio": {
                    "type": "object",
                    "required": ["type", "command"],
                    "properties": {
                        "type": {"const": "stdio"},
                        "command": {
                            "type": "string",
                            "pattern": launcher,
                            "description": f"One of {', '.join(rules.STDIO_LAUNCHERS)} (case-insensitive in the "
                            "validator), with only the launcher options the app allows and an exact pin.",
                        },
                        "args": {"type": "array", "items": {"type": "string"}},
                        "env": {
                            "type": "object",
                            "propertyNames": {
                                "pattern": rules.ENV_NAME_RE.pattern,
                                "not": {"enum": sorted(rules.RESERVED_ENV_NAMES)},
                            },
                            "additionalProperties": {"type": "string", "pattern": rules.ENV_PLACEHOLDER_RE.pattern},
                        },
                    },
                },
            },
            "description": f"Transports: {', '.join(rules.MCP_TRANSPORTS)}; "
            f"{', '.join(rules.DEPRECATED_TRANSPORTS)} is refused.",
        },
    )


def skill_frontmatter_schema() -> dict[str, Any]:
    return _schema(
        "skill-frontmatter.schema.json",
        "SKILL.md frontmatter (the YAML block between the opening --- lines)",
        {
            "type": "object",
            "required": list(rules.REQUIRED_SKILL_FRONTMATTER),
            "properties": {
                "name": _name(),
                "description": _description(),
                "version": _semver(),
                "title": {"type": "string", "minLength": 1, "maxLength": rules.MAX_TITLE_CHARS},
                "categories": _categories(),
                "flavor": {"enum": list(rules.SKILL_FLAVORS)},
                "compatible_agents": _compatible_agents(),
            },
            "not": {"anyOf": [{"required": [k]} for k in rules.FORBIDDEN_SKILL_FRONTMATTER]},
            "description": f"Missing flavor means {rules.DEFAULT_SKILL_FLAVOR!r} in this registry's feed. "
            f"SKILL.md at most {rules.MAX_SKILL_MD_BYTES} bytes.",
        },
    )


def agent_template_schema() -> dict[str, Any]:
    capability_list = {
        "type": "array",
        "maxItems": rules.MAX_CAPABILITIES,
        "items": {"type": "string", "pattern": rules.CAPABILITY_RE.pattern},
    }
    return _schema(
        "agent-template.schema.json",
        "Agent template (agents/<name>/agent.json)",
        {
            "type": "object",
            "required": ["schema", "name", "instructions"],
            "properties": {
                "schema": {"const": rules.TEMPLATE_SCHEMA},
                "name": {
                    "type": "string",
                    "pattern": rules.AGENT_NAME_RE.pattern,
                    "description": f"{rules.LEAD_AGENT_NAME!r} is reserved (any case).",
                },
                "title": {"type": "string", "maxLength": rules.MAX_TITLE_CHARS},
                "instructions": {
                    "type": "string",
                    "minLength": 1,
                    "description": f"At most {rules.MAX_INSTRUCTIONS_BYTES} UTF-8 bytes; no home-folder paths.",
                },
                "tier": {"enum": list(rules.TIERS)},
                "effort": {"type": "string", "pattern": rules.EFFORT_RE.pattern},
                "grant_mode": {"enum": list(rules.GRANT_MODES)},
                "knowledge_scope": {"enum": list(rules.KNOWLEDGE_SCOPES)},
                **{k: capability_list for k in rules.CAPABILITY_LIST_KEYS},
                "skills": {
                    "anyOf": [
                        {"type": "array", "items": {"type": "string", "pattern": rules.TEMPLATE_SKILL_RE.pattern}},
                        {"type": "null"},
                    ]
                },
                "avatar": {
                    "type": "object",
                    "propertyNames": {"enum": sorted(rules.AVATAR_KEYS)},
                    "properties": {
                        "companion": {"propertyNames": {"enum": sorted(rules.COMPANION_KEYS)}},
                        **{k: {"maxProperties": rules.MAX_AVATAR_MAP_ENTRIES} for k in rules.AVATAR_MAP_KEYS},
                    },
                },
            },
            "additionalProperties": False,
            "description": "Fields an agent record has but a template may not carry: "
            + ", ".join(sorted(rules.AGENT_FORBIDDEN_KEYS)),
        },
    )


def submission_schema() -> dict[str, Any]:
    keys = submission.ALLOWED_KEYS
    common = {
        "name": _name(),
        "version": _semver(),
        "publisher": {"description": "Ignored; ownership comes from registry.json."},
        "publisher_id": {"description": "Ignored; ownership comes from registry.json."},
    }
    listing = {
        "title": {"type": "string", "minLength": 1, "maxLength": rules.MAX_TITLE_CHARS},
        "description": _description(),
        "categories": _categories(),
    }

    def variant(kind: str, required: list[str], extra: dict[str, Any]) -> dict[str, Any]:
        properties = {"kind": {"const": kind}, **common, **extra}
        assert set(properties) == set(keys[kind]), kind  # noqa: S101 - generation-time consistency check
        return {
            "type": "object",
            "required": ["kind", *required],
            "properties": properties,
            "additionalProperties": False,
        }

    return _schema(
        "submission.schema.json",
        "Marketplace submission (what AIAgentLayer's Publish button sends)",
        {
            "oneOf": [
                variant(
                    "skill",
                    ["name", "version", "title", "description", "skill_md"],
                    {
                        **listing,
                        "skill_md": {"type": "string", "minLength": 1},
                        "flavor": {"enum": list(rules.SKILL_FLAVORS)},
                        "compatible_agents": _compatible_agents(),
                    },
                ),
                variant(
                    "plugin",
                    ["name", "version", "plugin_json"],
                    {
                        "plugin_json": {"$ref": "plugin.schema.json"},
                        "mcp_json": {"anyOf": [{"$ref": "mcp.schema.json"}, {"type": "null"}]},
                        "usage_card": {"type": ["string", "null"]},
                        "skills": {
                            "type": "array",
                            "maxItems": rules.MAX_BUNDLED_SKILLS,
                            "items": {
                                "type": "object",
                                "required": ["name", "skill_md"],
                                "properties": {"name": _name(), "skill_md": {"type": "string", "minLength": 1}},
                                "additionalProperties": False,
                            },
                        },
                    },
                ),
                variant(
                    "agent",
                    ["name", "version", "title", "description", "agent"],
                    {**listing, "agent": {"$ref": "agent-template.schema.json"}},
                ),
            ],
            "description": f"The serialised submission is at most {rules.MAX_FILE_BYTES} bytes.",
        },
    )


def registry_schema() -> dict[str, Any]:
    return _schema(
        "registry.schema.json",
        "Package ownership (registry.json)",
        {
            "type": "object",
            "required": ["version", "owners"],
            "properties": {
                "version": {"enum": [1, registry.REGISTRY_FORMAT]},
                "owners": {
                    "type": "object",
                    "propertyNames": _name(),
                    "additionalProperties": {
                        "type": "object",
                        "required": ["kind", "publisher"],
                        "properties": {
                            "kind": {"enum": list(rules.KINDS)},
                            "publisher": _login(),
                            "publisher_id": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
                            "version": _semver(),
                            "claimed_at": {"type": "string"},
                            "maintainer_managed": {"type": "boolean"},
                        },
                        "propertyNames": {"enum": sorted(registry.OWNER_KEYS)},
                    },
                },
            },
        },
    )


def trusted_publishers_schema() -> dict[str, Any]:
    return _schema(
        "trusted-publishers.schema.json",
        "First-party publishers (trusted-publishers.json)",
        {
            "type": "object",
            "required": ["version", "publishers"],
            "properties": {
                "version": {"const": registry.TRUSTED_FORMAT},
                "publishers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["login", "id", "trust"],
                        "properties": {
                            "login": _login(),
                            "id": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
                            "status": {"const": "pending"},
                            "name": {"type": "string"},
                            "trust": {"enum": list(registry.TRUST_LEVELS)},
                            "roles": {"type": "array", "items": {"const": registry.MAINTAINER_ROLE}},
                            "verified": {"type": "object"},
                        },
                        "if": {"properties": {"id": {"type": "null"}}},
                        "then": {"required": ["status"]},
                    },
                },
            },
        },
    )


def feed_schema() -> dict[str, Any]:
    ownership = {
        "publisher": _login(),
        "publisher_id": {"type": "integer", "minimum": 1},
        "trust": {"enum": list(registry.TRUST_LEVELS)},
        "version": _semver(),
        "published_at": TIMESTAMP,
        "source_url": HTTPS_URL,
        "content_sha256": SHA256,
    }
    skill = {
        "type": "object",
        "required": ["name", "title", "description", "publisher", "version", "published_at", "categories",
                     "source_url", "raw_url", "flavor", "compatible_agents", "trust", "content_sha256", "raw_sha256"],
        "properties": {
            "name": _name(),
            "title": {"type": "string"},
            "description": {"type": "string"},
            "categories": _categories(),
            "raw_url": HTTPS_URL,
            "flavor": {"enum": list(rules.SKILL_FLAVORS)},
            "compatible_agents": _compatible_agents(),
            "raw_sha256": SHA256,
            **ownership,
        },
    }  # fmt: skip
    plugin = {
        "type": "object",
        "required": ["name", "publisher", "version", "published_at", "source_url", "plugin_json", "mcp_json",
                     "usage_card", "trust", "content_sha256", "transport", "bundled_skills"],
        "properties": {
            "name": _name(),
            "plugin_json": {"$ref": "plugin.schema.json"},
            "mcp_json": {"anyOf": [{"$ref": "mcp.schema.json"}, {"type": "null"}]},
            "usage_card": {"type": ["string", "null"]},
            "transport": {"enum": [*rules.MCP_TRANSPORTS, None]},
            "bundled_skills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "description", "sha256"],
                    "properties": {"name": _name(), "description": {"type": "string"}, "sha256": SHA256},
                },
            },
            **ownership,
        },
    }  # fmt: skip
    agent = {
        "type": "object",
        "required": ["name", "title", "description", "publisher", "version", "published_at", "categories",
                     "source_url", "agent", "trust", "content_sha256", "schema"],
        "properties": {
            "name": _name(),
            "title": {"type": "string"},
            "description": {"type": "string"},
            "categories": _categories(),
            "agent": {"$ref": "agent-template.schema.json"},
            "schema": {"const": rules.TEMPLATE_SCHEMA},
            **ownership,
        },
    }  # fmt: skip
    return _schema(
        "feed.schema.json",
        "Marketplace feed (docs/index.json, read by AIAgentLayer's CommunityIndex)",
        {
            "type": "object",
            "required": ["revision", "generated_at", "plugins", "skills", "agents"],
            "properties": {
                "revision": {"type": "integer", "minimum": 0},
                "generated_at": {"anyOf": [TIMESTAMP, {"type": "null"}]},
                "plugins": {"type": "array", "items": {"$ref": "#/$defs/plugin"}},
                "skills": {"type": "array", "items": {"$ref": "#/$defs/skill"}},
                "agents": {"type": "array", "items": {"$ref": "#/$defs/agent"}},
            },
            "$defs": {"skill": skill, "plugin": plugin, "agent": agent},
            "description": "The app tolerates fields it does not know, so entries are open to additions.",
        },
    )


def build_all() -> dict[str, dict[str, Any]]:
    """File name -> schema, for every schema in ``schemas/``."""
    schemas = [
        submission_schema(),
        plugin_schema(),
        mcp_schema(),
        skill_frontmatter_schema(),
        agent_template_schema(),
        registry_schema(),
        trusted_publishers_schema(),
        feed_schema(),
    ]
    return {s["$id"].rsplit("/", 1)[-1]: s for s in schemas}
