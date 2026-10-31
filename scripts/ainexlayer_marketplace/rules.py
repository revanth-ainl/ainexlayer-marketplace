"""The rule set, as data: names, limits, patterns and reserved identifiers.

Every value mirrors AIAgentLayer at commit 03f4afc847 (2026-10-31). The
comment above each group names the app file it comes from. When the app
changes one of these, change it here too; ``tests/app_contract_check.py``
compares the two sides.

Stricter than the app is allowed (a submission refused here would only have
been refused later). Looser is a bug: the marketplace would list a package
the app then refuses to install.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The AIAgentLayer commit these rules were taken from.
APP_SOURCE_COMMIT = "03f4afc847"

KINDS = ("plugin", "skill", "agent")


@dataclass(frozen=True)
class FieldError:
    """One problem with a package, phrased for the person who has to fix it.

    ``field`` names the submission field (``name``, ``mcp_json``, ...) or is
    ``None`` for the package as a whole. ``severity`` is ``"error"`` (the
    package is refused) or ``"warning"`` (reported, not refused).
    """

    message: str
    field: str | None = None
    severity: str = "error"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def to_dict(self) -> dict[str, str | None]:
        """The app's own shape (``publish.FieldError``): ``{"error", "field"}``."""
        return {"error": self.message, "field": self.field}

    def __str__(self) -> str:
        where = f"{self.field}: " if self.field else ""
        return f"{where}{self.message}"


def warning(message: str, field: str | None = None) -> FieldError:
    return FieldError(message, field, "warning")


# ---------------------------------------------------------------------------
# Names and versions
# dodaios/marketplace/publish.py (_NAME_RE, _SEMVER_RE, _is_valid_name) and
# agent_plugins_loader.validate_spec_name. A package or skill name becomes a
# directory on the user's disk, so this is a path boundary.
# ---------------------------------------------------------------------------

NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
FORBIDDEN_NAME_SUBSTRINGS = ("--", "..")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
#: GitHub's login rule: 1-39 chars, alphanumeric or single hyphens.
GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


def is_valid_name(text: object) -> bool:
    return (
        isinstance(text, str)
        and bool(NAME_RE.fullmatch(text))
        and not any(part in text for part in FORBIDDEN_NAME_SUBSTRINGS)
    )


def is_semver(text: object) -> bool:
    return isinstance(text, str) and bool(SEMVER_RE.fullmatch(text))


def version_tuple(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


# ---------------------------------------------------------------------------
# Sizes
# publish.py (MAX_FILE_BYTES, MAX_DESCRIPTION_CHARS counted in UTF-16 units)
# and agent_plugins_loader.py (MAX_BUNDLED_SKILLS, MAX_SKILL_MD_BYTES).
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 128 * 1024
MAX_SKILL_MD_BYTES = 64 * 1024
MAX_DESCRIPTION_CHARS = 500
MAX_BUNDLED_SKILLS = 10
MAX_CATEGORIES = 10
#: Largest file of any kind allowed in a package folder.
MAX_PACKAGE_FILE_BYTES = MAX_FILE_BYTES


def utf16_len(text: str) -> int:
    """Length in UTF-16 code units, the unit the app's limit counts."""
    return len(text.encode("utf-16-le")) // 2


# ---------------------------------------------------------------------------
# Credentials
# publish.py (_SECRET_PATTERNS, the eight families every submission is
# scanned for). AGENT_SECRET_PATTERNS adds the generic "key = value" shape
# that dodaios/society/agent_template.py (_SECRET_RES) also refuses.
# ---------------------------------------------------------------------------

SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub token family
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),  # GitHub fine-grained token
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # OpenAI-style
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),  # Google API key
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}"),  # JWT
)
GENERIC_SECRET_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|bearer)\b\s*[:=]\s*[\"']?[^\s\"']{8,}"
)
AGENT_SECRET_PATTERNS = (*SECRET_PATTERNS, GENERIC_SECRET_RE)

# agent_template.py (_HOME_RE): a folder on the author's machine.
_PATH_TAIL = r"[^\s\"'`)]*[^\s\"'`).,;:!?]"
HOME_PATH_RE = re.compile(
    r"(?i)(?:\b[A-Za-z]:[\\/]+Users[\\/]+" + _PATH_TAIL + r"|/Users/" + _PATH_TAIL + r"|/home/" + _PATH_TAIL + r")"
)


# ---------------------------------------------------------------------------
# Plugin manifests
# dodaios/core/branding.py (MARKETPLACE_EXTENSION_NAMESPACE and the legacy
# name) via agent_plugins_loader.py. These are the app's identifiers: every
# published plugin.json carries its block under them, so they stay even
# though they name the upstream project.
# ---------------------------------------------------------------------------

EXTENSION_NAMESPACE = "io.github.dodaios"
LEGACY_EXTENSION_NAMESPACES = ("io.github.personaljarvis",)
EXTENSION_NAMESPACES = (EXTENSION_NAMESPACE, *LEGACY_EXTENSION_NAMESPACES)

# dodaios/marketplace/catalog.py: the auth modes a PluginSpec accepts.
AUTH_MODES = frozenset(
    {
        "pat_paste",
        "instance_browser",
        "oauth_device_flow",
        "hosted_mcp_oauth_dcr",
        "oauth_pkce_loopback",
        "hosted_mcp_allowlist",
        "local",
        "hosted_mcp_open",
    }
)


# ---------------------------------------------------------------------------
# MCP servers
# agent_plugins_loader.py (validate_mcp_server and its helpers) plus the
# "exactly one server" check in publish.py._validate_mcp.
# ---------------------------------------------------------------------------

MCP_TRANSPORTS = ("streamable-http", "stdio")
DEPRECATED_TRANSPORTS = ("sse",)
STDIO_LAUNCHERS = ("npx", "uvx", "docker")
NPX_FLAGS = frozenset({"-y", "--yes"})
UVX_FLAGS = frozenset({"--from"})
DOCKER_FLAGS = frozenset({"-i", "--interactive", "--rm"})
DOCKER_VALUE_FLAGS = frozenset({"-e", "--env"})
NPM_PIN_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*@\d+\.\d+\.\d+[0-9A-Za-z.+-]*$")
PYPI_PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:==|@)\d+\.\d+\.\d+[0-9A-Za-z.+-]*$")
IMAGE_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*:[0-9][0-9A-Za-z._-]*$")
IMAGE_DIGEST_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RESERVED_ENV_NAMES = frozenset(
    {
        "PATH",
        "PATHEXT",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "NODE_OPTIONS",
        "NODE_PATH",
        "NPM_CONFIG_REGISTRY",
        "UV_INDEX_URL",
        "UV_EXTRA_INDEX_URL",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "DOCKER_HOST",
        "COMSPEC",
        "SYSTEMROOT",
        "WINDIR",
    }
)
#: Credential placeholders the app fills in at connect time:
#: ``$plugin_<id>_<field>`` or ``${plugin_<id>_<field>}``. Anything else in an
#: env value is a literal, and a literal is a smuggled credential.
ENV_PLACEHOLDER_RE = re.compile(r"^\$(?:plugin_[a-z0-9_.-]+|\{plugin_[a-z0-9_.-]+\})$")
HEADER_PLACEHOLDER_RE = re.compile(r"\$(?:plugin_[a-z0-9_.-]+|\{plugin_[a-z0-9_.-]+\})")
TOKEN_LITERAL_RE = re.compile(r"[A-Za-z0-9_\-]{20,}")
# publish.py._validate_mcp also searches the argv for "@1.2.3" or ":<tag with
# a digit>". The launcher rules above already require an exact pin, and that
# search wrongly refuses `uvx pkg==1.2.3`, so it is not mirrored here.


# ---------------------------------------------------------------------------
# Skills
# agent_plugins_loader.py (_FORBIDDEN_SKILL_FRONTMATTER, required keys) and
# community_source.py (SKILL_FLAVORS, the compatible_agents bounds).
# ---------------------------------------------------------------------------

FORBIDDEN_SKILL_FRONTMATTER = ("risk_policy",)
REQUIRED_SKILL_FRONTMATTER = ("name", "description")
#: ``jarvis`` is the app's own skill profile name and stays as the app spells
#: it; ``portable`` is a plain Agent Skill any SKILL.md reader can use.
SKILL_FLAVORS = ("jarvis", "portable")
DEFAULT_SKILL_FLAVOR = "portable"
MAX_COMPATIBLE_AGENTS = 8
MAX_COMPATIBLE_AGENT_CHARS = 32


# ---------------------------------------------------------------------------
# Agent templates
# dodaios/society/agent_template.py (validate_template and its constants)
# and dodaios/society/roster.py (LEAD_AGENT_ROW_NAME).
# ---------------------------------------------------------------------------

TEMPLATE_SCHEMA = 1
MAX_INSTRUCTIONS_BYTES = 20_000
MAX_SUMMARY_CHARS = 500
MAX_TITLE_CHARS = 120
MAX_CAPABILITIES = 60
TEMPLATE_KEYS = frozenset(
    {
        "schema",
        "name",
        "title",
        "instructions",
        "tier",
        "effort",
        "focus",
        "grant_mode",
        "grants",
        "denies",
        "skills",
        "require_approval",
        "knowledge_scope",
        "avatar",
    }
)
#: Keys an agent record has in the app but a template must never carry,
#: with the reason shown to the author. TEMPLATE_KEYS already refuses them;
#: this only makes the message say why.
AGENT_FORBIDDEN_KEYS = {
    "account_id": "it names the author's own subscription",
    "always_allow": "a template may not pre-approve tools",
    "approval_mode": "a template may not switch approvals off",
    "approval_rules": "only require_approval may travel, never an allow rule",
    "browser_allowed_domains": "the installer's own browser settings apply",
    "browser_mode": "the installer's own browser settings apply",
    "computer_id": "it names a machine only the author has",
    "daily_budget_usd": "the installer's own budget applies",
    "max_concurrent_runs": "the installer's own limit applies",
    "model": "the installer chooses their own model",
    "parent_agent_id": "it is an id from the author's own team",
    "permission_ceiling": "a template may not widen what runs without asking",
    "provider": "the installer chooses their own model",
    "wiki_namespace": "it is set on the installing machine",
    "workspace_dir": "it is a folder on the author's machine",
}
AGENT_NAME_RE = re.compile(r"^[^/\\:@#<>\"'`\x00-\x1f]{1,40}$")
CAPABILITY_RE = re.compile(r"^[a-z]+:[A-Za-z0-9_.:-]{1,80}$")
TEMPLATE_SKILL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
EFFORT_RE = re.compile(r"^[a-z]{0,16}$")
TIERS = ("specialist", "orchestrator")
GRANT_MODES = ("all", "allowlist")
KNOWLEDGE_SCOPES = ("shared", "own")
CAPABILITY_LIST_KEYS = ("focus", "grants", "denies", "require_approval")
#: The lead agent every install already has; a template may not take its name.
LEAD_AGENT_NAME = "Jarvis"
AVATAR_ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
HEX_COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DRAWN_PET_RE = re.compile(r"^u[0-9a-f]{16}$")
AVATAR_STRING_KEYS = ("archetype", "base", "style", "hairStyle", "outfit", "eyewear")
AVATAR_MAP_KEYS = ("parts", "palette")
AVATAR_KEYS = frozenset({"contract", "inner", "heightM", "companion", *AVATAR_STRING_KEYS, *AVATAR_MAP_KEYS})
COMPANION_KEYS = frozenset({"shape", "color", "eyes", "enabled", "sizeM", "followDistanceM", "pet", "creature", "look"})
MAX_AVATAR_MAP_ENTRIES = 32


# ---------------------------------------------------------------------------
# Reserved names
# community_install.seed_plugin_ids() reserves every plugin id the app ships
# in dodaios/marketplace/seed_catalog.json. The hyphenated spelling of each is
# reserved too, so "google-drive" cannot pose as the built-in google_drive.
# Skill names shadowing the app's built-in skills (dodaios/skills/builtin)
# are refused for the same reason.
# ---------------------------------------------------------------------------

_SEED_PLUGIN_IDS = (
    "agentmail", "airtable", "amd_gpu", "apollo", "asana", "atlassian", "aws", "azure", "cal_com",
    "canva", "clickup", "cloudflare", "discord", "dropbox", "figma", "github", "gitlab", "gmail",
    "google_calendar", "google_cloud", "google_drive", "granola", "higgsfield", "home_assistant",
    "hubspot", "linear", "linkedin", "meta", "microsoft_todo", "notion", "onedrive", "onenote",
    "outlook", "salesforce", "sharepoint", "slack", "spotify", "stripe", "supabase", "teams",
    "telegram", "todoist", "vercel", "x", "youtube_music", "youtube_studio", "zoom",
)  # fmt: skip
RESERVED_PLUGIN_IDS = frozenset(_SEED_PLUGIN_IDS) | frozenset(i.replace("_", "-") for i in _SEED_PLUGIN_IDS)

_BUILTIN_SKILLS = (
    "bdr", "bdr-compliance", "bdr-linkedin", "bdr-outreach", "bdr-prospecting", "bdr-qualification",
    "bdr-signals", "bg-ads", "bg-checkpoint", "bg-content", "bg-diagnose", "bg-discover",
    "bg-outreach", "bg-retro", "bg-seo", "bg-social", "bg-strategy", "cli-gcloud", "control-api",
    "deep-work-mode", "dodaios-doc-author", "erp-catalog", "erp-claims", "erp-compliance",
    "erp-customer", "erp-deal", "erp-discord", "erp-purchasing", "erp-sales", "erp-todo",
    "erp-warehouse", "erp-workflow", "founder-board", "founder-brand", "founder-cfo",
    "founder-competitors", "founder-consumer", "founder-launch", "founder-marketing",
    "founder-offer", "founder-ops", "founder-plan", "founder-pricing", "memory-save",
    "personal-calendar", "personal-coordinator", "personal-social", "personal-todos",
    "skill-creator",
)  # fmt: skip
RESERVED_SKILL_NAMES = (
    frozenset(_BUILTIN_SKILLS)
    | frozenset(f"plugin-{i}" for i in _SEED_PLUGIN_IDS)
    | frozenset(f"plugin-{i.replace('_', '-')}" for i in _SEED_PLUGIN_IDS)
)


def reserved_reason(kind: str, name: str) -> str | None:
    """Why ``name`` may not be published as ``kind``, or ``None``."""
    if kind == "plugin" and name in RESERVED_PLUGIN_IDS:
        return f"{name!r} is a plugin AIAgentLayer ships itself"
    if kind == "skill" and name in RESERVED_SKILL_NAMES:
        return f"{name!r} is a skill AIAgentLayer ships itself"
    return None


# ---------------------------------------------------------------------------
# The app's publish request (publish.py INTAKE_TITLE_TAG / INTAKE_BODY_MARKER).
# AIAgentLayer opens an issue carrying these; a maintainer reads it and runs
# scripts/expand.py by hand. Nothing processes these issues automatically.
# ---------------------------------------------------------------------------

INTAKE_TITLE_TAG = "[publish]"
INTAKE_BODY_MARKER = "<!-- jarvis-marketplace-submission -->"
