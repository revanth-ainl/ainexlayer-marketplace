# Publishing to the AINexLayer Marketplace

The registry has no automation. A package is live once it is committed to
`main` together with an updated `docs/index.json`; AIAgentLayer reads both
straight from `main`.

## For maintainers

1. Put the package in place:
   - skill: `skills/<name>/SKILL.md`
   - plugin: `plugins/<name>/plugin.json` (+ optional `mcp.json`, `usage_card.md`)
   - agent template: `agents/<name>/agent.json`
2. For a new name, add the owner to `registry.json`:

   ```json
   "my-skill": { "kind": "skill", "publisher": "<github-login>", "claimed_at": "2026-10-31T00:00:00Z" }
   ```

3. Run the checks and rebuild the feed:

   ```bash
   python3 scripts/validate.py
   python3 scripts/compile_index.py
   ```

4. Commit and push to `main`.

To update a package, edit its files, raise `version`, and repeat steps 3 and 4.
To remove one, delete its folder and its `registry.json` entry, then repeat
steps 3 and 4.

## For publishers

Send the package by either route; a maintainer reviews it and publishes it
with the steps above.

- **Issue:** use the
  [Publish package form](https://github.com/revanth-ainl/ainexlayer-marketplace/issues/new?template=publish.yml),
  or AIAgentLayer's in-app **Publish**, which opens the same kind of issue.
- **Pull request:** add your package folder under `skills/`, `plugins/` or
  `agents/`.

Rules for every package:

- The name is a lowercase slug (`a-z`, `0-9`, `.`, `-`, at most 64 characters)
  and matches the folder name.
- `version` is plain SemVer (`1.2.3`) and goes up with every change.
- No credentials anywhere. MCP configs may reference credentials only as
  placeholders.
- The first publisher of a name owns it; other accounts cannot take it over.
