# Plugins

Each plugin lives in `plugins/<name>/` with Agent Plugins v1.0.0 manifests:

- `plugin.json` (required)
- `mcp.json` (optional)
- `usage_card.md` (optional)

`scripts/compile_index.py` embeds those manifests into `docs/index.json`.
