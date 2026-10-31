# AINexLayer Marketplace

Public registry of **skills**, **plugins**, and **agent templates** for
[AIAgentLayer](https://github.com/revanth-ainl/AIAgentLayer).

Everything lives on the `main` branch. There is no server, no GitHub Actions
and no GitHub Pages: AIAgentLayer reads the feed and downloads each skill
straight from `main`.

```
https://raw.githubusercontent.com/revanth-ainl/ainexlayer-marketplace/main/docs/index.json
```

That is AIAgentLayer's default `community_index_url`, so no app configuration
is needed.

## Layout

| Path | Role |
|---|---|
| `skills/<name>/SKILL.md` | Skills (portable or AIAgentLayer) |
| `plugins/<name>/plugin.json` (+ optional `mcp.json`, usage card) | Agent Plugins v1.0.0 packages |
| `agents/<name>/agent.json` | Agent templates |
| `registry.json` | Who owns each package name |
| `docs/index.json` | The feed AIAgentLayer reads |
| `scripts/validate.py` | Local check: folder names, file size, secret patterns |
| `scripts/compile_index.py` | Rebuilds `docs/index.json` from the folders above |

## Add or update a skill

1. Create or edit `skills/<name>/SKILL.md`. The front matter needs `name`,
   `description` and `version`, plus optional `title`, `categories` and
   `flavor` (`portable` for a plain Agent Skill).
2. For a new name, record the owner in `registry.json` under `owners`.
3. Check it and rebuild the feed:

   ```bash
   python3 scripts/validate.py
   python3 scripts/compile_index.py
   ```

4. Commit everything, including `docs/index.json`, and push to `main`.

As soon as the push lands, AIAgentLayer shows the skill in its Marketplace
view (it refreshes the feed every couple of minutes) and installs it from
`skills/<name>/SKILL.md` on `main`.

Plugins and agent templates work the same way, in `plugins/<name>/` and
`agents/<name>/`.

## Submissions from other people

Publishers can open an issue with the
[Publish package form](https://github.com/revanth-ainl/ainexlayer-marketplace/issues/new?template=publish.yml)
(AIAgentLayer's in-app **Publish** opens the same kind of issue), or send a
pull request. Nothing is published automatically: a maintainer reviews the
package, adds it with the steps above and pushes to `main`. See
[PUBLISHING.md](PUBLISHING.md).

## Trust

- Every package is reviewed by a maintainer before it reaches `main`.
- `scripts/validate.py` rejects unsafe folder names, oversized files and
  common secret patterns.
- AIAgentLayer shows community packages as unreviewed, asks before
  installing, and re-checks every package at install time.

Details: [SECURITY.md](SECURITY.md).

## License

AINexLayer License (proprietary). See [LICENSE](LICENSE).
Not open source; limited grants for AIAgentLayer users and publishers.
