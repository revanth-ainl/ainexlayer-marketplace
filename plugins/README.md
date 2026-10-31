# Plugins

Each plugin lives in `plugins/<name>/` with Agent Plugins v1.0.0 manifests:

| File | |
|---|---|
| `plugin.json` | Required. `name` (the folder name), `version`, `description`, and AIAgentLayer's extension block under `extensions["io.github.dodaios"]` (or the legacy `io.github.personaljarvis`) with an `auth` block. [Schema](../schemas/plugin.schema.json) |
| `mcp.json` | Optional. Exactly one MCP server. [Schema](../schemas/mcp.schema.json) |
| `usage_card.md` | Optional usage card (`usage-card.md` and `<namespace>/usage-card.md` are read too; only one). |
| `skills/<skill>/SKILL.md` | Optional bundled skills, at most 10. |

Nothing else may be in the folder.

MCP server rules, as AIAgentLayer's installer applies them:

- `streamable-http`: an https `url`, no static `headers` (the app adds the
  token from `mcp_auth_header_template`).
- `stdio`: `command` is `npx`, `uvx` or `docker`, with only the launcher
  options the app allows and an exact pin (`pkg@1.2.3`, `pkg==1.2.3`,
  `image:1.2` or `image@sha256:...`). `env` values are `$plugin_…`
  placeholders only, and reserved names such as `PATH` or `NODE_OPTIONS`
  are refused.
- `sse` is not accepted.

The auth block must match one of the app's auth modes exactly (required
fields present, no unknown fields, https URLs, no `client_secret`).

The feed embeds `plugin.json`, `mcp.json` and the usage card unchanged and
adds `transport`, `bundled_skills` (name, description, SHA-256) and a hash of
the whole folder. See [PUBLISHING.md](../PUBLISHING.md) for the publishing
steps.
