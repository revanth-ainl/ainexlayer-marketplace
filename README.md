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
| `plugins/<name>/plugin.json` (+ optional `mcp.json`, usage card, bundled skills) | Agent Plugins v1.0.0 packages |
| `agents/<name>/agent.json` | Society agent templates |
| `submissions/<name>.json` | One submission per community package; the source the folders above are expanded from |
| `registry.json` | Ownership ledger: first claim wins the name |
| `docs/index.json` | Compiled feed (GitHub Pages source) |
| `scripts/validate.py` | Validation: today folder names, file size and secret patterns |
| `scripts/compile_index.py` | Builds `docs/index.json` |
| `scripts/intake.py`, `scripts/automerge_gate.py`, `scripts/publish.py` | Entry points of the publishing workflows |
| `scripts/ainexlayer_marketplace/` | Shared code of the publishing workflows (intake, gate, git, GitHub API) |
| `.github/workflows/` | CI, intake, auto-merge gate, publish and Pages |

Not in the repository yet; they arrive with the shared validation library,
and the publishing workflows need them to run (see the Status note in
[PUBLISHING.md](PUBLISHING.md)): `trusted-publishers.json`, `docs/rules.json`,
`scripts/export_rules.py`, `requirements-ci.txt`, and the library modules
`compat`, `submission`, `registry` and `pipeline`.

## Publish a package

See [PUBLISHING.md](PUBLISHING.md): publish from AIAgentLayer, use the
[issue form](https://github.com/revanth-ainl/ainexlayer-marketplace/issues/new?template=publish.yml),
or open a pull request that adds `submissions/<name>.json`.

Official sample packages without a submission file (such as
`skills/hello-ainexlayer`) are maintainer-managed: edit the folder, record the
owner in `registry.json`, regenerate `docs/` and open a pull request.

## Develop and test

```bash
uv run --no-project --python 3.11 --with-requirements requirements-dev.txt \
  python -m unittest discover -s tests -v
python scripts/validate.py
python scripts/compile_index.py && git diff --exit-code docs/
```

Once the library lands, CI also installs `requirements-ci.txt` and runs
`python scripts/export_rules.py --check`.

Lint and workflow checks used before a change lands:

```bash
uvx ruff@0.17.3 check scripts tests          # settings in ruff.toml
uvx ruff@0.17.3 format --check scripts tests
uvx --from actionlint-py actionlint
uvx zizmor .github/workflows
```

The library runs on Python 3.9 and later; CI uses 3.11. Tests never call
GitHub: the API client and git are replaced by fakes, and the publish race
tests use a local bare repository.

## Enable GitHub Pages (one-time)

After the first push of this repo:

1. Repo **Settings → Pages**
2. Source: **GitHub Actions**
3. Confirm the **AINexLayer Pages** workflow succeeds
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

## Repository setup (maintainers)

The publishing automation needs these settings; none of them is stored in
this repository.

1. **Settings → Pages**: source **GitHub Actions**. The `github-pages`
   environment must allow deployments from `main`.
2. **Settings → Actions → General**: workflow permissions "Read repository
   contents" (each workflow asks for more per job).
3. **Branch protection on `main`**: require the **Tests, validation and
   generated files** check and code-owner review. The intake, gate and publish
   workflows push the bot's publish commit to `main` with the workflow token,
   so allow GitHub Actions to bypass the pull-request requirement, or those
   workflows cannot publish.
4. **Repository variables**: set `AINEXLAYER_INTAKE` to `true` to turn on
   issue intake, and `AINEXLAYER_AUTOMERGE` to `true` to turn on the
   auto-merge gate. Both are off by default.
5. **Labels**: `publish`, `published`, `needs-changes`, `publish-failed`.
6. **GitHub App** for in-app publishing, with Issues read and write on this
   repository (see above).

## Trust model (summary)

- Every submission is untrusted data: never executed, validated by the
  registry's own code from `main`.
- The publisher is the GitHub account that opened the issue or pull request.
  Ownership keyed on its numeric id is enforced by the validator in the
  shared library, which has not landed yet.
- Passing validation does not make a package trusted. Marking publishers as
  official belongs to maintainers (`trusted-publishers.json`, arriving with
  the library).
- Community cards are shown as unreviewed; AIAgentLayer re-checks packages at
  install time and users consent before install.

Details: [SECURITY.md](SECURITY.md).

## License

AINexLayer License (proprietary) — see [LICENSE](LICENSE).
Not open source; limited grants for AIAgentLayer users and publishers.
