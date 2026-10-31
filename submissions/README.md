# Submissions

One file per package: `submissions/<name>.json`, holding the submission
object described in [PUBLISHING.md](../PUBLISHING.md).

Files arrive here three ways: the intake workflow writes them from a publish
issue, a contributor adds one in a pull request, or a maintainer merges one.
The publish pipeline then expands each file into `skills/`, `plugins/` or
`agents/`, records the claim in `registry.json` and rebuilds `docs/`. Do not
edit the expanded folders of a submitted package by hand; change the
submission and bump its version.

A pull request that publishes must change exactly one file in this folder and
nothing else.
