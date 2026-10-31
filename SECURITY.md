# Security

## Reporting

Report a vulnerability privately through GitHub's **Security → Report a
vulnerability** on this repository. Do not open a public issue for it.

## No automation, on purpose

The repository runs no GitHub Actions, no GitHub Pages, no Dependabot and no
git hooks, and nothing publishes, merges or deploys by itself. A submission
is text written by a stranger; letting a workflow act on it would give that
stranger a say in what runs with this repository's permissions. Every change
reaches `main` through a maintainer, and `scripts/validate.py` refuses
workflow, Dependabot and Pages files if one is ever added.

## What a package must pass

`scripts/validate.py` (and `scripts/expand.py` before it writes anything)
applies AIAgentLayer's own install rules, so a package listed here is one the
app would install:

- **Names and paths:** lowercase slugs only, no `..` or `--`, no names the
  app ships itself; every file path is checked to stay inside its package
  folder.
- **Files:** only `.json` and `.md`, UTF-8, at most 128 KB each (SKILL.md 64
  KB); no hidden files; no symlinks anywhere in a package, and `expand.py`
  will not write through one.
- **Credentials:** eight credential families (GitHub, OpenAI-style, Slack,
  AWS, Google, private keys, JWTs) are refused everywhere; agent templates
  also refuse `key = value` secrets and folders on the author's machine.
  MCP `env` values must be `$plugin_…` placeholders.
- **MCP servers:** one server; https only; no static headers; stdio only
  through `npx`, `uvx` or `docker` with an exact version pin and only the
  launcher options the app allows; reserved environment names (`PATH`,
  `LD_PRELOAD`, `NODE_OPTIONS`, ...) refused; `sse` refused.
- **Plugins:** the AIAgentLayer extension block with an auth block the app's
  catalog accepts; no `client_secret`; no `native_tool`; https URLs only.
- **Skills:** frontmatter with `name` and `description`; never
  `risk_policy` (it decides which tools run without asking).
- **Agent templates:** only template fields; no model, provider, budget,
  folder, approval or pre-approval fields; the lead agent's name is
  reserved.

Submitted text is only ever parsed as JSON or Markdown. No submitted code is
run, imported or installed by any script here, and no script uses the
network.

## Ownership

A package name belongs to the GitHub account that first published it,
identified by its **numeric account id** in `registry.json`. Logins can be
renamed and then claimed by someone else; ids cannot. Another account cannot
publish under a taken name, a name cannot move to another kind, and versions
only go up. `validate.py --base-ref origin/main` checks all of this against
what is already on `main`, and also refuses a package whose content changed
without a new version.

## Trust levels

`trusted-publishers.json` lists first-party publishers by numeric GitHub id.
A package is `official` in the feed only when its owner's id is listed there;
everything else is `community`. A login alone never earns trust, and an
entry whose id has not been confirmed is ignored.

## Hashes

The feed records, for every package:

- `content_sha256`: a hash over all of the package's files (the recipe is in
  `scripts/ainexlayer_marketplace/hashing.py` and reproducible with
  `shasum`);
- `raw_sha256` (skills): the SHA-256 of the exact SKILL.md bytes behind
  `raw_url`;
- `sha256` for each bundled skill of a plugin.

They let anyone compare what they downloaded with what was reviewed.
AIAgentLayer does not check them at install time yet; until it does they
are information, not enforcement.

## What review does not promise

Passing review does not make a package safe to run everywhere. AIAgentLayer
shows community packages as unreviewed, asks the user before installing, and
re-validates every package with its own rules at install time. Downloads
use https only; the app refuses any non-https download link in the feed.
