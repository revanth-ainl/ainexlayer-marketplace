"""The AIAgentLayer install contract: plugin manifests, MCP servers, agent templates.

Each check here is the judgment the app makes when it installs a package
(dodaios/marketplace/agent_plugins_loader.py, dodaios/society/agent_template.py),
re-implemented so the marketplace refuses what the app would refuse before
it is ever listed. Messages say what to change, not only that something is
wrong.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Callable

from .rules import (
    AGENT_FORBIDDEN_KEYS,
    AGENT_NAME_RE,
    AGENT_SECRET_PATTERNS,
    AUTH_MODES,
    AVATAR_ID_RE,
    AVATAR_KEYS,
    AVATAR_MAP_KEYS,
    AVATAR_STRING_KEYS,
    CAPABILITY_LIST_KEYS,
    CAPABILITY_RE,
    COMPANION_KEYS,
    DEPRECATED_TRANSPORTS,
    DOCKER_FLAGS,
    DOCKER_VALUE_FLAGS,
    DRAWN_PET_RE,
    EFFORT_RE,
    ENV_NAME_RE,
    ENV_PLACEHOLDER_RE,
    EXTENSION_NAMESPACE,
    EXTENSION_NAMESPACES,
    GRANT_MODES,
    HEADER_PLACEHOLDER_RE,
    HEX_COLOUR_RE,
    HOME_PATH_RE,
    IMAGE_DIGEST_RE,
    IMAGE_TAG_RE,
    KNOWLEDGE_SCOPES,
    LEAD_AGENT_NAME,
    MAX_AVATAR_MAP_ENTRIES,
    MAX_CAPABILITIES,
    MAX_DESCRIPTION_CHARS,
    MAX_INSTRUCTIONS_BYTES,
    MAX_SUMMARY_CHARS,
    MAX_TITLE_CHARS,
    NPM_PIN_RE,
    NPX_FLAGS,
    PYPI_PIN_RE,
    RESERVED_ENV_NAMES,
    STDIO_LAUNCHERS,
    TEMPLATE_KEYS,
    TEMPLATE_SCHEMA,
    TEMPLATE_SKILL_RE,
    TIERS,
    TOKEN_LITERAL_RE,
    UVX_FLAGS,
    FieldError,
    is_semver,
    is_valid_name,
    utf16_len,
)

# ---------------------------------------------------------------------------
# plugin.json
# ---------------------------------------------------------------------------


def extension_of(plugin_json: Any) -> tuple[Mapping[str, Any] | None, list[FieldError]]:
    """The plugin's AIAgentLayer block, read under any of the app's namespaces.

    A manifest carrying the block under several namespaces must carry the
    same block under each (the app refuses two blocks that disagree).
    """
    extensions = plugin_json.get("extensions") if isinstance(plugin_json, Mapping) else None
    if not isinstance(extensions, Mapping):
        return None, []
    found = [(ns, extensions[ns]) for ns in EXTENSION_NAMESPACES if isinstance(extensions.get(ns), Mapping)]
    if not found:
        return None, []
    first_ns, block = found[0]
    for ns, other in found[1:]:
        if dict(other) != dict(block):
            return None, [
                FieldError(f'extensions["{first_ns}"] and extensions["{ns}"] must be identical', "plugin_json")
            ]
    return block, []


def _http_urls(value: Any, where: str) -> list[FieldError]:
    if isinstance(value, str):
        if value.lstrip().lower().startswith("http://"):
            return [FieldError(f"{where} uses a non-https URL: {value!r}", "plugin_json")]
        return []
    if isinstance(value, Mapping):
        return [e for k, v in value.items() for e in _http_urls(v, f"{where}.{k}")]
    if isinstance(value, list):
        return [e for i, v in enumerate(value) for e in _http_urls(v, f"{where}[{i}]")]
    return []


def check_plugin_manifest(plugin_json: Any, *, expected_name: str | None = None) -> list[FieldError]:
    """Everything the app's ``convert_manifest`` would refuse in a plugin.json."""
    if not isinstance(plugin_json, Mapping):
        return [FieldError("plugin_json must be a JSON object", "plugin_json")]
    errors: list[FieldError] = []
    name = plugin_json.get("name")
    if not is_valid_name(name):
        errors.append(
            FieldError("plugin_json.name must follow the package name rules (a-z 0-9 - ., 1-64 chars)", "plugin_json")
        )
    elif expected_name is not None and name != expected_name:
        errors.append(
            FieldError(f"plugin_json.name {name!r} must equal the package name {expected_name!r}", "plugin_json")
        )
    version = plugin_json.get("version")
    if version is not None and not is_semver(version):
        errors.append(FieldError("plugin_json.version must be plain SemVer, e.g. 1.0.0", "plugin_json"))
    description = plugin_json.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(FieldError("plugin_json.description is required", "plugin_json"))
    elif utf16_len(description) > MAX_DESCRIPTION_CHARS:
        errors.append(
            FieldError(f"plugin_json.description is longer than {MAX_DESCRIPTION_CHARS} characters", "plugin_json")
        )

    extension, ext_errors = extension_of(plugin_json)
    errors.extend(ext_errors)
    if ext_errors:
        return errors
    if extension is None:
        errors.append(
            FieldError(
                f'plugin_json needs an extensions["{EXTENSION_NAMESPACE}"] block with the auth settings',
                "plugin_json",
            )
        )
        return errors
    if extension.get("native_tool"):
        errors.append(FieldError("community plugins cannot bind a native_tool (that needs app code)", "plugin_json"))
    auth = extension.get("auth")
    if not isinstance(auth, Mapping):
        errors.append(
            FieldError(f"the extension block needs an auth object (mode one of {sorted(AUTH_MODES)})", "plugin_json")
        )
    else:
        if auth.get("mode") not in AUTH_MODES:
            errors.append(FieldError(f"auth.mode must be one of {sorted(AUTH_MODES)}", "plugin_json"))
        errors.extend(_http_urls(auth, "auth"))
    fallback = extension.get("fallback_auth")
    if fallback is not None:
        if not isinstance(fallback, Mapping):
            errors.append(FieldError("fallback_auth must be an object", "plugin_json"))
        else:
            if fallback.get("mode") != "pat_paste":
                errors.append(FieldError("fallback_auth must use mode 'pat_paste'", "plugin_json"))
            errors.extend(_http_urls(fallback, "fallback_auth"))
            if isinstance(auth, Mapping) and auth.get("mode") == "pat_paste":
                errors.append(
                    FieldError("fallback_auth is redundant when auth.mode is already pat_paste", "plugin_json")
                )
    logo_url = extension.get("logo_url")
    if logo_url is not None:
        if not isinstance(logo_url, str) or not logo_url.strip():
            errors.append(FieldError("logo_url must be a non-empty string", "plugin_json"))
        elif not logo_url.strip().lower().startswith("https://"):
            errors.append(FieldError("logo_url must be an https:// URL", "plugin_json"))
    template = extension.get("mcp_auth_header_template")
    if template is not None:
        errors.extend(_check_header_template(template))
    return errors


def _check_header_template(template: Any) -> list[FieldError]:
    if not isinstance(template, str) or not template.strip():
        return [FieldError("mcp_auth_header_template must be a non-empty string", "plugin_json")]
    if not HEADER_PLACEHOLDER_RE.search(template):
        return [FieldError("mcp_auth_header_template must reference a $plugin_… placeholder", "plugin_json")]
    if TOKEN_LITERAL_RE.search(HEADER_PLACEHOLDER_RE.sub(" ", template)):
        return [FieldError("mcp_auth_header_template may not embed a literal credential", "plugin_json")]
    return []


# ---------------------------------------------------------------------------
# mcp.json
# ---------------------------------------------------------------------------


def check_mcp(mcp: Any, plugin_json: Any = None) -> list[FieldError]:
    """An mcp.json: exactly one server, which the app would install.

    Streamable HTTP servers must be https with no static headers. Stdio
    servers must use ``npx``, ``uvx`` or ``docker`` with only the launcher
    options the app allows, an exactly pinned package or image, and env
    values that are ``$plugin_…`` placeholders.
    """
    if not isinstance(mcp, Mapping):
        return [FieldError("mcp_json must be an object", "mcp_json")]
    servers = mcp.get("mcpServers")
    if not isinstance(servers, Mapping):
        return [FieldError("mcp_json must contain an mcpServers object", "mcp_json")]
    if len(servers) != 1:
        return [FieldError("mcp_json must define exactly one server", "mcp_json")]
    name, server = next(iter(servers.items()))
    name = str(name)
    if not isinstance(server, Mapping):
        return [FieldError(f"mcp server {name!r} must be an object", "mcp_json")]
    kind = server.get("type")
    if kind == "streamable-http":
        return _check_http_server(name, server)
    if kind == "stdio":
        return _check_stdio_server(name, server)
    if kind in DEPRECATED_TRANSPORTS:
        return [
            FieldError("the deprecated 'sse' transport is not accepted; publish a streamable-http endpoint", "mcp_json")
        ]
    return [
        FieldError(f"mcp server {name!r} must declare type 'streamable-http' or 'stdio' (got {kind!r})", "mcp_json")
    ]


def _check_http_server(name: str, server: Mapping[str, Any]) -> list[FieldError]:
    url = server.get("url")
    if not isinstance(url, str) or not url.strip():
        return [FieldError(f"mcp server {name!r}: url is required", "mcp_json")]
    errors: list[FieldError] = []
    if not url.strip().lower().startswith("https://"):
        errors.append(FieldError(f"mcp server {name!r}: url must be https:// (got {url!r})", "mcp_json"))
    if server.get("headers"):
        errors.append(
            FieldError(
                f"mcp server {name!r}: headers are not allowed; the app adds the token itself "
                "from mcp_auth_header_template",
                "mcp_json",
            )
        )
    return errors


def _check_stdio_server(name: str, server: Mapping[str, Any]) -> list[FieldError]:
    command = server.get("command")
    if not isinstance(command, str) or not command.strip():
        return [FieldError(f"mcp server {name!r}: command is required", "mcp_json")]
    launcher = command.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd"):
        launcher = launcher.removesuffix(suffix)
    if launcher not in STDIO_LAUNCHERS:
        return [
            FieldError(
                f"mcp server {name!r}: launcher {command!r} is not allowed (use one of {', '.join(STDIO_LAUNCHERS)})",
                "mcp_json",
            )
        ]
    errors: list[FieldError] = []
    if server.get("url"):
        errors.append(FieldError(f"mcp server {name!r}: a stdio server may not also declare a url", "mcp_json"))
    args = server.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        errors.append(FieldError(f"mcp server {name!r}: args must be a list of strings", "mcp_json"))
        args = []
    # The launcher rules below require an exactly pinned package or image
    # for every launcher, so no separate "is anything pinned" check is needed.
    problem = _stdio_args_problem(launcher, list(args))
    if problem:
        errors.append(FieldError(f"mcp server {name!r}: {problem}", "mcp_json"))
    env = server.get("env", {})
    if not isinstance(env, Mapping):
        errors.append(FieldError(f"mcp server {name!r}: env must be an object", "mcp_json"))
        env = {}
    for key, value in env.items():
        key_text = str(key)
        if not ENV_NAME_RE.fullmatch(key_text) or key_text.upper() in RESERVED_ENV_NAMES:
            errors.append(
                FieldError(
                    f"mcp server {name!r}: env {key_text!r} is not allowed (PATH and loader or runtime "
                    "variables are off limits)",
                    "mcp_json",
                )
            )
        elif not isinstance(value, str) or not ENV_PLACEHOLDER_RE.fullmatch(value):
            errors.append(
                FieldError(
                    f"mcp server {name!r}: env {key_text!r} must be a $plugin_… placeholder, never a literal value",
                    "mcp_json",
                )
            )
    return errors


def _stdio_args_problem(launcher: str, args: list[str]) -> str | None:
    """What is wrong with the launcher half of a stdio argv, or ``None``.

    Everything up to and including the package belongs to the launcher and
    is checked; what follows is the server's own arguments.
    """
    if launcher == "npx":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] not in NPX_FLAGS:
                return f"npx option {args[i]!r} is not allowed; launch as `npx -y <name>@<version>`"
            i += 1
        if i >= len(args):
            return "npx needs a package, e.g. `npx -y my-mcp@1.2.0`"
        if not NPM_PIN_RE.fullmatch(args[i]):
            return f"{args[i]!r} is not a pinned npm package (`my-mcp@1.2.0`); git refs, URLs and paths are refused"
        return None

    if launcher == "uvx":
        i = 0
        from_package = False
        while i < len(args) and args[i].startswith("-"):
            flag, sep, inline = args[i].partition("=")
            if flag not in UVX_FLAGS:
                return f"uvx option {flag!r} is not allowed; launch as `uvx <name>==<version>`"
            if sep:
                value, i = inline, i + 1
            else:
                if i + 1 >= len(args):
                    return f"uvx option {flag!r} is missing its value"
                value, i = args[i + 1], i + 2
            if not PYPI_PIN_RE.fullmatch(value):
                return f"{value!r} is not a pinned PyPI package (`--from my-mcp==1.2.0`)"
            from_package = True
        if from_package:
            return None if i < len(args) else "uvx --from needs an entry point, e.g. `--from my-mcp==1.2.0 my-mcp`"
        if i >= len(args):
            return "uvx needs a package, e.g. `uvx my-mcp==1.2.0`"
        if not PYPI_PIN_RE.fullmatch(args[i]):
            return f"{args[i]!r} is not a pinned PyPI package (`my-mcp==1.2.0`); git refs, URLs and paths are refused"
        return None

    # docker
    if not args or args[0] != "run":
        return "the only allowed docker subcommand is `run`"
    i = 1
    while i < len(args) and args[i].startswith("-"):
        flag, sep, inline = args[i].partition("=")
        if flag in DOCKER_VALUE_FLAGS:
            if sep:
                value, i = inline, i + 1
            else:
                if i + 1 >= len(args):
                    return f"docker option {flag!r} is missing its value"
                value, i = args[i + 1], i + 2
            if not ENV_NAME_RE.fullmatch(value):
                return f"docker {flag} {value!r} must name a variable only; values belong in the env block"
            continue
        if flag not in DOCKER_FLAGS:
            return (
                f"docker option {flag!r} is not allowed; run as `docker run -i --rm <image>:<version>` "
                "(-v, --mount, --privileged and --network would open the user's machine)"
            )
        i += 1
    if i >= len(args):
        return "docker needs an image, e.g. `docker run -i --rm my/mcp:1.2`"
    if not (IMAGE_TAG_RE.fullmatch(args[i]) or IMAGE_DIGEST_RE.fullmatch(args[i])):
        return f"{args[i]!r} is not a pinned image; use a version tag (`my/mcp:1.2`) or a digest, never latest"
    return None


# ---------------------------------------------------------------------------
# Agent templates
# ---------------------------------------------------------------------------


def _avatar_problem(avatar: Any) -> str | None:
    """The app keeps only catalog ids and colours in an avatar; a recipe
    carrying anything else is refused rather than silently trimmed."""
    if not isinstance(avatar, Mapping):
        return "avatar must be an object"
    for key, value in avatar.items():
        if key not in AVATAR_KEYS:
            return f"avatar.{key} is not an avatar field"
        if key == "contract" and value != 1:
            return "avatar.contract must be 1"
        if key in AVATAR_STRING_KEYS and not (isinstance(value, str) and AVATAR_ID_RE.fullmatch(value)):
            return f"avatar.{key} must be a catalog id"
        if key in AVATAR_MAP_KEYS:
            pattern = HEX_COLOUR_RE if key == "palette" else AVATAR_ID_RE
            if (
                not isinstance(value, Mapping)
                or len(value) > MAX_AVATAR_MAP_ENTRIES
                or not all(
                    isinstance(k, str) and AVATAR_ID_RE.fullmatch(k) and isinstance(v, str) and pattern.fullmatch(v)
                    for k, v in value.items()
                )
            ):
                return f"avatar.{key} may only map catalog ids to {'#rrggbb colours' if key == 'palette' else 'catalog ids'}"
        if key == "inner" and not (isinstance(value, str) and HEX_COLOUR_RE.fullmatch(value)):
            return "avatar.inner must be a #rrggbb colour"
        if key == "heightM" and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.2 <= value <= 4
        ):
            return "avatar.heightM must be a number from 0.2 to 4"
        if key == "companion":
            if not isinstance(value, Mapping) or not value or any(k not in COMPANION_KEYS for k in value):
                return f"avatar.companion must be a non-empty object with only {sorted(COMPANION_KEYS)}"
            if isinstance(value.get("pet"), str) and DRAWN_PET_RE.fullmatch(value["pet"]):
                return "avatar.companion.pet names a pet drawn on the author's machine; remove it"
    return None


def check_agent_template(agent: Any, listing: Mapping[str, Any] | None = None) -> list[FieldError]:
    """Everything the app's ``validate_template`` would refuse.

    ``listing`` is the store card (``description`` at least); its summary
    length is checked here because the app checks it together with the
    template.
    """
    if not isinstance(agent, Mapping):
        return [FieldError("agent must be a JSON object (the template)", "agent")]
    errors: list[FieldError] = []
    add: Callable[[str], None] = lambda message: errors.append(FieldError(message, "agent"))

    for key in agent:
        if key in AGENT_FORBIDDEN_KEYS:
            add(f"agent.{key} may not be published: {AGENT_FORBIDDEN_KEYS[key]}")
        elif key not in TEMPLATE_KEYS:
            add(f"agent.{key} is not a template field (allowed: {', '.join(sorted(TEMPLATE_KEYS))})")
    if agent.get("schema") != TEMPLATE_SCHEMA:
        add(f"agent.schema must be {TEMPLATE_SCHEMA}")
    name = agent.get("name")
    if not isinstance(name, str) or not AGENT_NAME_RE.fullmatch(name.strip() or "/"):
        add("agent.name must be 1-40 characters without / \\ : @ # < > or quotes")
    elif name.strip().lower() == LEAD_AGENT_NAME.lower():
        add(f"agent.name {LEAD_AGENT_NAME!r} is reserved for the lead agent every install already has")
    title = agent.get("title", "")
    if not isinstance(title, str) or len(title) > MAX_TITLE_CHARS:
        add(f"agent.title must be at most {MAX_TITLE_CHARS} characters")
    instructions = agent.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        add("agent.instructions are empty; write what this agent does")
    elif len(instructions.encode("utf-8")) > MAX_INSTRUCTIONS_BYTES:
        add(f"agent.instructions are longer than {MAX_INSTRUCTIONS_BYTES} bytes")
    elif HOME_PATH_RE.search(instructions):
        add("agent.instructions name a folder on the author's machine")
    if agent.get("tier", "specialist") not in TIERS:
        add(f"agent.tier must be one of {', '.join(TIERS)}")
    if agent.get("grant_mode", "all") not in GRANT_MODES:
        add(f"agent.grant_mode must be one of {', '.join(GRANT_MODES)}")
    if agent.get("knowledge_scope", "shared") not in KNOWLEDGE_SCOPES:
        add(f"agent.knowledge_scope must be one of {', '.join(KNOWLEDGE_SCOPES)}")
    effort = agent.get("effort", "")
    if not isinstance(effort, str) or not EFFORT_RE.fullmatch(effort):
        add("agent.effort must be a short lowercase word")
    for key in CAPABILITY_LIST_KEYS:
        value = agent.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and CAPABILITY_RE.fullmatch(v) for v in value):
            add(f"agent.{key} must be a list of capability ids like 'tool:web_search'")
        elif len(value) > MAX_CAPABILITIES:
            add(f"agent.{key} has more than {MAX_CAPABILITIES} entries")
    skills = agent.get("skills")
    if skills is not None and (
        not isinstance(skills, list) or not all(isinstance(v, str) and TEMPLATE_SKILL_RE.fullmatch(v) for v in skills)
    ):
        add("agent.skills must be a list of skill names, or null")
    avatar_problem = _avatar_problem(agent.get("avatar", {}))
    if avatar_problem:
        add(avatar_problem)
    text = json.dumps(agent, ensure_ascii=False)
    if any(p.search(text) for p in AGENT_SECRET_PATTERNS):
        add("the template contains something that looks like a credential")
    if listing is not None:
        description = str(listing.get("description") or "")
        if not description.strip():
            errors.append(FieldError("description is required: a one-line summary for the store card", "description"))
        elif len(description) > MAX_SUMMARY_CHARS:
            errors.append(FieldError(f"description is longer than {MAX_SUMMARY_CHARS} characters", "description"))
    return errors
