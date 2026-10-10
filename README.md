# AINexLayer Marketplace

Public registry of community **skills**, **plugins**, and **agent templates** for
[AIAgentLayer](https://github.com/revanth-ainl/AIAgentLayer).

The desktop app fetches a single compiled feed:

```
https://revanth-ainl.github.io/ainexlayer-marketplace/index.json
```

No application server is required. GitHub is the upload API (pull requests /
issues), Actions validates packages, and GitHub Pages serves the index.

## Layout

| Path | Role |
|---|---|
| `skills/<name>/SKILL.md` | Portable or AIAgentLayer skills |
| `plugins/<name>/plugin.json` (+ optional `mcp.json`, usage card) | Agent Plugins v1.0.0 packages |
| `agents/<name>/agent.json` | Society agent templates |
| `registry.json` | Ownership ledger (first claim wins the name) |
| `submissions/` | Intake drop folder (optional automation) |
| `docs/index.json` | Compiled feed (GitHub Pages source) |
| `scripts/compile_index.py` | Builds `docs/index.json` from the trees above |
| `scripts/validate.py` | Naming, size, and secret-pattern gate |

## Add a skill

1. Create `skills/my-skill/SKILL.md` with YAML frontmatter (`name`, `description`,
   optional `flavor: portable|jarvis`, `version`).
2. Record ownership in `registry.json` under `owners`.
3. Run:

   ```bash
   python scripts/validate.py
   python scripts/compile_index.py
   ```

4. Open a pull request. After merge to `main`, Pages redeploys the index.

## Enable GitHub Pages (one-time)

After the first push of this repo:

1. Repo **Settings → Pages**
2. Source: **GitHub Actions**
3. Confirm the **Deploy Pages** workflow succeeds
4. Open `https://revanth-ainl.github.io/ainexlayer-marketplace/index.json`

Until Pages is live, AIAgentLayer can still be pointed at a raw URL override in
`jarvis.toml`:

```toml
[marketplace]
community_index_url = "https://raw.githubusercontent.com/revanth-ainl/ainexlayer-marketplace/main/docs/index.json"
publish_registry_repo = "revanth-ainl/ainexlayer-marketplace"
```

## In-app publish (GitHub App)

AIAgentLayer can open a publish issue on this repo after a GitHub device-flow
sign-in. Create a GitHub App (Device Flow enabled, no secrets needed for the
client id), install it on this repository, then set in the app config:

```toml
[marketplace]
publish_github_client_id = "Iv…"
```

Until that App exists, publishers can use the issue form at
[Issues → New → Publish package](https://github.com/revanth-ainl/ainexlayer-marketplace/issues/new?template=publish.yml)
or send a pull request.

## Trust model (summary)

- CI validates names, https-only URLs (when plugins land), size limits, and
  obvious secret patterns.
- `registry.json` records the first publisher per package name.
- The AIAgentLayer client re-checks packages at install time.
- Community cards are shown as unreviewed; users consent before install.

## License

AINexLayer License (proprietary) — see [LICENSE](LICENSE).
Not open source; limited grants for AIAgentLayer users and publishers.
