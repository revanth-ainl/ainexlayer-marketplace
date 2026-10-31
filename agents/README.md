# Agent templates

Each agent template lives in `agents/<name>/agent.json`. AIAgentLayer accepts
only its own template fields and refuses anything that would point at the
author's machine or widen what runs without asking on the installer's
machine. The registry-side check of the same rules arrives with the shared
validation library (see [PUBLISHING.md](../PUBLISHING.md)).

Community templates are expanded here from `submissions/<name>.json`, and
`scripts/compile_index.py` embeds them into `docs/index.json`.
