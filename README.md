# skills

This repository stores the skills and agents that I create.

## Structure

- `/skills/` — individual skills. See [skills/README.md](skills/README.md) for the full index,
  the required per-skill layout, and how each skill documents its own usage.
- `/agents/` — individual agents. See [agents/README.md](agents/README.md).
- `install.sh` — deploys a skill from this library into any target repo's `.github/skills/`
  (symlink by default, so edits here propagate instantly; `--copy` for a pinned snapshot).

## Quick start

```bash
# See what's available
cat skills/README.md

# Make a skill invocable inside another repo
./install.sh <skill-name> <target-repo-path>

# Install everything
./install.sh --all <target-repo-path>
```

## Adding new content

Create a new folder inside `/skills/` or `/agents/` for each item you add and keep any supporting
files with it. For skills, every `SKILL.md` must include a "When to Use" section and a
"Usage / Procedure" section with real invocation examples — see
[skills/README.md](skills/README.md) for the exact convention.