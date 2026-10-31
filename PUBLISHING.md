# Publishing to the AINexLayer Marketplace

There are three ways in. All three run the same validation and end in the same
feed, `docs/index.json`, served by GitHub Pages.

> **Status (2026-10-31).** The publishing workflows and the checks they make
> themselves are in place: who the publisher is, what a pull request may
> change, merging only the checked commit, and safe handling of concurrent
> publishes. The package rule set below is agreed but **not enforced yet**.
> It arrives with the shared validation library (`compat`, `submission`,
> `registry`, `pipeline`, `requirements-ci.txt`, `scripts/export_rules.py`).
> Until then the intake, gate and publish workflows stop at install and
> publish nothing. CI and Pages run today's checks and keep deploying the
> feed; their library steps switch on by themselves once the library's files
> are in the repository. Today `scripts/validate.py` checks
> only folder names, file size and common secret patterns in `skills/` and
> `plugins/`.

| Path | Who uses it | Publisher identity comes from | Default |
|---|---|---|---|
| Issue intake | AIAgentLayer's in-app **Publish**, or the issue form | The account that opened the issue | Off until `AINEXLAYER_INTAKE` is `true` |
| Pull request + auto-merge gate | Anyone with a fork | The account that opened the pull request | Off until `AINEXLAYER_AUTOMERGE` is `true` |
| Pull request + maintainer merge | Anyone with a fork | The author of the merged pull request | Always on |

Whatever `publisher` or `publisher_id` a submission contains is never taken on
trust: intake overwrites both, and on a pull request they must name the pull
request's author or the submission is refused.

## The submission

A submission is one JSON object. AIAgentLayer builds it for you; by hand it
looks like this.

Skill:

```json
{
  "kind": "skill",
  "name": "tidy-notes",
  "version": "1.0.0",
  "title": "Tidy notes",
  "description": "Turns rough notes into a short list.",
  "categories": ["productivity"],
  "skill_md": "---\nname: tidy-notes\ndescription: Turns rough notes into a short list.\n---\n\n# Tidy notes\n"
}
```

Plugin: `kind`, `name`, `version`, `plugin_json` (the Agent Plugins manifest),
optionally `mcp_json` (exactly one server), `usage_card` (Markdown) and
`skills` (bundled skills, each `{"name": …, "skill_md": …}`).

Agent template: `kind`, `name`, `version`, `title`, `description`,
`categories` and `agent` (the template itself; only the template fields
AIAgentLayer accepts are allowed).

Rules a submission must meet. The automation already refuses an unsafe file
name; the rest are the agreed rule set, **not enforced until the validation
library lands** (see Status):

- `name` is a lowercase slug (`a-z`, `0-9`, `.`, `-`, at most 64 characters)
  and, in a pull request, equals the file name `submissions/<name>.json`.
- `version` is plain SemVer (`1.2.3`) and must be higher than the version
  already published. The first account to publish a name owns it, by numeric
  GitHub account id, so renaming your account keeps your packages.
- No credentials anywhere. MCP configs reference credentials only as
  placeholders.

Once the library lands, `scripts/validate.py` is the authority for all of
these, and a machine-readable copy is published next to the feed as
`rules.json`.

## Issue intake

1. Open **Issues → New → Publish package**, or publish from AIAgentLayer.
2. The title starts with `[publish]`. The body holds exactly one ` ```json `
   block with the submission (the form adds the block for you).
3. The **AINexLayer Intake** workflow reads the block as data, sets the
   publisher to your account, validates, and commits the result to `main`.
4. You get a comment. On success the issue is labelled `published` and
   closed. On a refusal it is labelled `needs-changes` with the reasons; edit
   the issue to run the checks again.

## Pull request

Add or change exactly one file, `submissions/<name>.json`, and nothing else.

- **Registry CI** runs the tests and validation on every pull request.
- With auto-merge on, the **AINexLayer Auto-merge Gate** checks the pull
  request with the registry's own code from `main`, never with code from the
  pull request. It merges only the exact commit it checked, then publishes.
  A pull request that touches anything else is labelled `needs-changes`, and
  so is one that changes a submission which is not published yet, or a name
  the registry team maintains. Refusing a name owned by another account is
  the validator's job (pending, see Status).
- With auto-merge off, a maintainer reviews and merges, and the
  **AINexLayer Publish** workflow publishes it as the pull request's author.

Removing a published package needs a maintainer; the publish workflow does not
delist on its own.

## What "published" means

The bot commit expands the submission into `skills/`, `plugins/` or
`agents/`, records the claim in `registry.json`, and rebuilds `docs/` (that
expansion is the pipeline in the pending library; see Status). The bot
publishes exactly the content that was checked and merged. If the same
submission changed again on `main` before the run, it publishes nothing and
says so; the newer change is published by its own run, under its own author.
The **AINexLayer Pages** workflow then deploys the newest `main`. It refuses to
deploy a build that does not contain the commit just published.

Community packages are shown as unreviewed. Passing validation does not make
a package trusted; marking publishers as trusted belongs to maintainers, in a
code-owner protected `trusted-publishers.json` (arrives with the library).
AIAgentLayer re-checks every package at install time and asks the user before
installing.

## When something goes wrong

- `needs-changes`: the comment lists what to fix.
- `publish-failed`: the registry changed too often while the run was
  retrying, or an internal step failed. Nothing was published. A maintainer
  re-runs the workflow (for a merged pull request: **Actions → AINexLayer
  Publish → Run workflow** with the merge commit's sha).
