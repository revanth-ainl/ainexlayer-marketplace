# Security

## Reporting

Report a vulnerability privately through GitHub's **Security → Report a
vulnerability** on this repository. Do not open a public issue for it.

## Threat model

Every submission, issue body and pull request is untrusted. The registry
never executes submitted JSON, scripts or issue text, and never trusts a
package just because it passed validation.

### Identity

- The publisher is the GitHub account that GitHub authenticated: the issue
  author (intake) or the pull request author (gate and publish). Identity
  fields in a submission are overwritten or must match.
- Only personal accounts publish. Bot and organisation authors are refused.
- Names registered without a numeric account id, or marked as maintained by
  the registry team, are closed to automated updates. So is a submission that
  is on `main` but not published yet.
- What gets published is pinned to the content that was checked and merged.
  A later change to the same submission is never published under an earlier
  author's name.
- Ownership keyed on the numeric account id (one account cannot update
  another's package) is enforced by the validator in the shared library,
  which has **not landed yet**. See the Status note in PUBLISHING.md.

### Workflows

| Workflow | Trigger | Token | Runs code from |
|---|---|---|---|
| AINexLayer Registry CI | `pull_request`, push to `main` | read-only | the pull request (no secrets, cannot publish) |
| AINexLayer Intake | `issues` | contents, issues: write | `main` only |
| AINexLayer Auto-merge Gate | `pull_request_target` | contents, pull-requests: write | `main` only |
| AINexLayer Publish | push to `main` under `submissions/` | contents, pull-requests: write | `main` only |
| AINexLayer Pages | push to `main`, or called by the three above | pages, id-token: write | `main` only |

The workflows need the shared validation library to run; until it lands
they stop at install or import and publish nothing.

Rules every workflow follows, enforced by `tests/test_workflows.py`:

- `permissions: {}` at the top; each job asks for what it needs.
- Every action is pinned to a full commit sha.
- Checkouts never persist the token. The push token is given only to the
  `git push` subprocess, through environment configuration, never on a
  command line.
- No `${{ … }}` expression inside a shell command. Event data reaches Python
  through the event file and is parsed as data.
- Privileged jobs check out `main`, install only hash-pinned dependencies,
  and run scripts with `python -I`.
- The gate never checks out the pull request. It fetches the head commit as
  git objects and reads the one submission file as bytes, and merges only if
  the head is still the commit it checked.
- Intake and auto-merge are off until a maintainer sets the repository
  variables.

### Concurrent publishes

Each source has its own concurrency group (per issue, per pull request, per
pushed commit), so one publish never cancels another one waiting. Writers
race at `git push`. The loser starts over from the new `main`, so every check
runs again against the registry as it is now, and gives up visibly after five
attempts with the `publish-failed` label.

### Client side

AIAgentLayer shows community packages as unreviewed, asks for consent, and
re-validates every package when it installs it, using its own copy of the
rules. Trust and consent fields in the feed arrive with the library.
