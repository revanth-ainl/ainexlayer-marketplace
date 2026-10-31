# Security

## Reporting

Report a vulnerability privately through GitHub's **Security → Report a
vulnerability** on this repository. Do not open a public issue for it.

## How packages stay safe

- The repository runs no GitHub Actions and has no automated publishing.
  Every change reaches `main` through a maintainer.
- Submissions (issues and pull requests) are treated as untrusted. A
  maintainer reviews each package before adding it.
- `scripts/validate.py`, run before every commit, rejects unsafe folder
  names, oversized files and common secret patterns.
- Passing review does not make a package trusted: AIAgentLayer shows community
  packages as unreviewed, asks the user before installing, and re-validates
  every package with its own rules at install time.
- Downloads use https only; AIAgentLayer refuses any non-https download link
  in the feed.
