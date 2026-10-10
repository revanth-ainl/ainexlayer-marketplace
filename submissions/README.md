# Submissions

Drop one JSON file per package here (or open a GitHub Issue with the publish
template once enabled). CI validates the payload and the compile step folds
accepted packages into `docs/index.json`.

Until intake automation is wired, add skills under `skills/<name>/SKILL.md`
(and plugins under `plugins/<name>/`) and run:

```bash
python scripts/compile_index.py
```
