# Plugins

Each plugin lives in `plugins/<name>/` with Agent Plugins v1.0.0 manifests:

- `plugin.json` (required), carrying AIAgentLayer's extension block
- `mcp.json` (optional, exactly one server)
- a usage card (optional)
- `skills/<skill>/SKILL.md` for bundled skills (optional)

Community plugins are expanded here from `submissions/<name>.json` by the
publish pipeline; see [PUBLISHING.md](../PUBLISHING.md).
`scripts/compile_index.py` embeds the manifests into `docs/index.json`.
