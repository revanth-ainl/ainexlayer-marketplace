# AINexLayer Marketplace

Public registry of **skills**, **plugins**, and **agent templates** for
[AIAgentLayer](https://github.com/revanth-ainl/AIAgentLayer).

This repository is source only. There is no server, no GitHub Actions, no
GitHub Pages and no automatic publishing: a maintainer checks and adds every
package by hand, and AIAgentLayer reads the feed and downloads each skill
straight from the `main` branch:

```
https://raw.githubusercontent.com/revanth-ainl/ainexlayer-marketplace/main/docs/index.json
```

That is AIAgentLayer's default `community_index_url`, so no app configuration
is needed.

## Layout

| Path | Role |
|---|---|
| `skills/<name>/SKILL.md` | Skills; the listing (version, title, categories, flavor, compatible agents) is in the frontmatter. See [skills/README.md](skills/README.md) |
| `plugins/<name>/` | Agent Plugins v1.0.0: `plugin.json`, optional `mcp.json`, usage card, bundled `skills/<skill>/SKILL.md`. See [plugins/README.md](plugins/README.md) |
| `agents/<name>/agent.json` + `listing.json` | Agent templates: the template, and its store listing. See [agents/README.md](agents/README.md) |
| `registry.json` | Who owns each package name (numeric GitHub account id) and its latest version |
| `trusted-publishers.json` | First-party publishers; their packages are listed as `official` |
| `docs/index.json` | The feed AIAgentLayer reads (generated, committed) |
| `docs/rules.json` | Every validation rule, exported for reading (generated) |
| `schemas/` | JSON Schemas for every format above (generated) |
| `scripts/validate.py` | Checks every package, the registry and the trusted list; or one submission |
| `scripts/expand.py` | Turns one reviewed submission into package files and a registry claim |
| `scripts/compile_index.py` | Rebuilds `docs/index.json` from the package folders |
| `scripts/export_rules.py` | Rebuilds `docs/rules.json` and `schemas/` from the validation library |
| `scripts/ainexlayer_marketplace/` | The validation library all scripts share |

Everything runs with the Python standard library (3.9 or newer). Nothing
touches the network.

## Publishing, in short

1. **Prepare** the package (a skill, plugin or agent template).
2. **Validate** it locally: `python3 scripts/validate.py --submission package.json`.
3. **Submit** it: send the submission JSON, or publish from AIAgentLayer,
   which opens a `[publish]` issue with the submission in it.
4. A maintainer **reviews** it by hand.
5. The maintainer **expands** it: `python3 scripts/expand.py issue.md --publisher LOGIN --publisher-id ID`
   (a dry run), then again with `--write`.
6. **Regenerate** the feed: `python3 scripts/compile_index.py`.
7. **Validate** the repository: `python3 scripts/validate.py --base-ref origin/main`
   and `python3 -m unittest discover -s tests`.
8. **Commit and push** to `main` by hand, when authorised.

Issues opened by the app are read by a person; nothing processes them
automatically. Full guide: [PUBLISHING.md](PUBLISHING.md).

## Checks a maintainer runs

```bash
python3 -I scripts/validate.py                        # every package, registry.json, trusted-publishers.json
python3 -I scripts/validate.py --base-ref origin/main # plus ownership, version and content-change rules
python3 -I scripts/compile_index.py --check           # docs/index.json is up to date
python3 -I scripts/export_rules.py --check            # docs/rules.json and schemas/ are up to date
python3 -m unittest discover -s tests                 # the library's unit tests
../AIAgentLayer/.venv/bin/python -I -B tests/app_contract_check.py   # against the app itself, read-only
```

## Trust

- Every package is reviewed by a maintainer before it reaches `main`.
- The validator applies AIAgentLayer's own rules (names, versions, MCP
  launchers and pins, credential placeholders, agent template fields,
  file types and sizes, symlinks, eight credential families).
- A package name belongs to the numeric GitHub account that first
  published it; versions only go up.
- The feed carries a SHA-256 of every package and of each SKILL.md.
- AIAgentLayer shows community packages as unreviewed, asks before
  installing, and re-checks every package at install time.

Details: [SECURITY.md](SECURITY.md).

## License

AINexLayer License (proprietary). See [LICENSE](LICENSE).
Not open source; limited grants for AIAgentLayer users and publishers.
