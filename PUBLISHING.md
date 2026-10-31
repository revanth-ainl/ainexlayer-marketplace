# Publishing to the AINexLayer Marketplace

Publishing is manual from start to finish. The repository has no GitHub
Actions, no GitHub Pages, no bots and no automatic merging. A package is live
once a maintainer commits it to `main` together with the regenerated
`docs/index.json`; AIAgentLayer reads both from the raw GitHub URL of `main`.

## The eight steps

### 1. Prepare the package

A package is one **submission**: the JSON object AIAgentLayer builds when
you press **Publish**. Its fields, by kind (the full format is
[schemas/submission.schema.json](schemas/submission.schema.json)):

| Kind | Fields |
|---|---|
| `skill` | `name`, `version`, `title`, `description`, `categories`, `skill_md`, optional `flavor`, `compatible_agents` |
| `plugin` | `name`, `version`, `plugin_json`, optional `mcp_json`, `usage_card`, `skills` (bundled `[{name, skill_md}]`) |
| `agent` | `name`, `version`, `title`, `description`, `categories`, `agent` (the template) |

`publisher` and `publisher_id` are ignored if present: ownership comes from
the GitHub account that submits, as checked by the maintainer.

Rules every package meets (all of them: [docs/rules.json](docs/rules.json)):

- The name is a lowercase slug: `a-z`, `0-9`, `.`, `-`, at most 64
  characters, no leading or trailing separator, no `--` or `..`. Names
  AIAgentLayer ships itself are reserved.
- `version` is plain SemVer (`1.2.3`) and goes up with every change.
- No credentials anywhere. An MCP `env` value may only be a `$plugin_…`
  placeholder, which the app fills in when the user connects.
- MCP servers: exactly one, `streamable-http` over https (no static
  headers) or `stdio` through `npx`, `uvx` or `docker` with an exact
  version pin. `sse` is refused.
- A SKILL.md opens with frontmatter holding `name` (equal to the package
  or bundled skill name) and `description`, and never `risk_policy`.
- Agent templates carry only the template fields the app accepts; no
  model, provider, budget, folder or approval overrides.

### 2. Validate it locally

```bash
python3 scripts/validate.py --submission package.json
```

The same command reads an issue body that AIAgentLayer opened (step 3), so a
publisher and a maintainer see the same result.

### 3. Submit it

Either:

- press **Publish** in AIAgentLayer, which opens a `[publish]` issue here with
  the submission in a fenced `json` block; or
- open an issue with the
  [Publish package form](https://github.com/revanth-ainl/ainexlayer-marketplace/issues/new?template=publish.yml)
  and paste the submission JSON.

The issue is a request to a person. Nothing reads it automatically.

### 4. Review (maintainer)

Read the package as a person would install it: what the skill tells an
assistant to do, what the MCP server runs and where it connects, what the
agent template asks for. Confirm the issue author's GitHub login and numeric
account id (`https://api.github.com/users/<login>`, field `id`); those are
the owner.

### 5. Expand it (maintainer)

Save the issue body (or the JSON) to a file and run:

```bash
python3 scripts/expand.py issue.md --publisher LOGIN --publisher-id 123456          # dry run
python3 scripts/expand.py issue.md --publisher LOGIN --publisher-id 123456 --write  # write
```

The dry run is the default and writes nothing. It prints every file it
would create or update with its SHA-256, and the registry change. `expand.py`
refuses the submission, writing nothing, when:

- the submission breaks a rule;
- the name belongs to another account or another kind;
- the version is lower than the published one, or equal to it with
  different content;
- a path would leave the package folder, or a symlink lies on the way.

With `--write` it writes the package folder and records the claim in
`registry.json`. Running it again with the same input changes nothing.

For a skill, the listing fields AIAgentLayer sends separately (version,
title, categories, flavor, compatible agents) are written into the SKILL.md
frontmatter, because that is where the feed reads them; the rest of the
SKILL.md is kept byte for byte. The listing description is the frontmatter
`description`; if the submission's description differs, `expand.py` warns.

`--maintainer-managed` marks a package that trusted maintainers (see
`trusted-publishers.json`) may also update.

### 6. Regenerate the feed

```bash
python3 scripts/compile_index.py
```

It validates every package first and stops on any problem. Entries are
sorted; an unchanged package keeps its `published_at`; `revision` goes up by
one only when something changed. A second run changes nothing.

### 7. Validate the repository

```bash
python3 scripts/validate.py --base-ref origin/main
python3 scripts/compile_index.py --check
python3 scripts/export_rules.py --check
python3 -m unittest discover -s tests
```

`--base-ref` adds the rules that compare with what is already published:
the owner's numeric id must match, versions only go up, and a package whose
content changed must carry a new version.

### 8. Commit and push (maintainer, when authorised)

```bash
git add skills/ plugins/ agents/ registry.json docs/index.json
git commit -m "publish: <kind> <name> <version>"
git push origin main
```

AIAgentLayer picks the change up on its next feed refresh.

## Updating and removing

- **Update:** the publisher submits the package again with a higher
  version; repeat steps 4 to 8. Files the new version no longer has are
  removed from the folder.
- **Remove:** delete the package folder and its `registry.json` entry, then
  run steps 6 to 8. Keep the name out of reuse unless the owner agrees.

## Editing by hand

A maintainer may also edit package folders directly. The same checks apply:
run step 6 and step 7 before committing. Raise the version in the package
and in `registry.json` together; the validator refuses a mismatch.
