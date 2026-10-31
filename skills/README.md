# Skills

Each skill lives in `skills/<name>/SKILL.md` and nothing else: the folder
holds that one file. AIAgentLayer downloads it from the raw GitHub URL of
`main` and installs it as a skill folder of the same name.

The frontmatter carries the skill and its store listing
([schemas/skill-frontmatter.schema.json](../schemas/skill-frontmatter.schema.json)):

```markdown
---
name: meeting-recap                 # required; equals the folder name
description: Turns rough meeting notes into decisions, owners and next steps.   # required
version: 1.0.0                      # plain SemVer; goes up with every change
title: Meeting recap                # store title (default: the name in title case)
categories: [productivity]          # up to 10
flavor: portable                    # portable (a plain Agent Skill) or jarvis (the app's own profile)
compatible_agents: [Claude Code, Cursor]   # display only, up to 8
---

# Meeting recap

Instructions the assistant follows.
```

- `risk_policy` is never allowed: a marketplace skill may not decide which
  tools run without asking.
- At most 64 KB. No credentials.
- A skill without `flavor` is listed as `portable`.

A skill published from AIAgentLayer arrives with its version, title and
categories as separate fields; `scripts/expand.py` writes them into this
frontmatter. A skill added by hand needs them in the frontmatter directly,
and its owner in `registry.json` with the same version.

See [PUBLISHING.md](../PUBLISHING.md) for the publishing steps.
