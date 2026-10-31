# Agent templates

Each agent template lives in `agents/<name>/` as two files:

| File | Holds |
|---|---|
| `agent.json` | The template itself, exactly as AIAgentLayer installs it. [Schema](../schemas/agent-template.schema.json) |
| `listing.json` | The store listing: `version`, `title`, `description`, `categories`, and nothing else. |

The two are kept apart so listing text never becomes a template field: the
feed publishes `agent.json` unchanged as `agent`, and takes the card's title,
description, categories and version from `listing.json`.

Template fields AIAgentLayer accepts: `schema` (1), `name`, `title`,
`instructions`, `tier`, `effort`, `focus`, `grant_mode`, `grants`, `denies`,
`skills`, `require_approval`, `knowledge_scope`, `avatar`. Anything that
would point at the author's machine or widen what runs without asking on
the installer's machine is refused: `model`, `provider`, budgets, folders,
`approval_mode`, `always_allow`, `permission_ceiling` and the like. The name
`Jarvis` is reserved for the lead agent every install already has.

A maintainer adds each template with `scripts/expand.py` (see
[PUBLISHING.md](../PUBLISHING.md)).
