# Agent templates

Each agent template lives in `agents/<name>/agent.json`. AIAgentLayer accepts
only its own template fields and refuses anything that would point at the
author's machine or widen what runs without asking on the installer's
machine; a maintainer reviews every template before adding it (see
[PUBLISHING.md](../PUBLISHING.md)).

`scripts/compile_index.py` embeds the templates into `docs/index.json`.
