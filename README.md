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

## Using a skill yourself, locally (no chat/AI involved)

Every skill is just plain scripts plus a doc — you can always run it directly, by hand, whether
or not the assistant is involved:

```bash
# Read the skill's own usage instructions
cat skills/<skill-name>/SKILL.md

# Run its script(s) directly, from whatever workspace root the skill expects
# (see that skill's "Usage / Procedure" section for the exact command/flags)
python3 skills/<skill-name>/scripts/<script>.py --help
```

For example:

```bash
cd /home/sleonarx/mmg    # workspace the skill operates on
python3 /home/sleonarx/skills/skills/generate-hifmc-als/scripts/generate_hifmc_als.py /home/sleonarx/mmg --dry-run
```

Nothing here requires an AI chat — it's exactly what the assistant itself runs under the hood.

## How the assistant/chat knows to use these skills (auto-discovery)

The assistant only auto-discovers **project skills** from a `.github/skills/<name>/SKILL.md` file
that sits at the **top level of whatever folder is currently open as your workspace** in the chat
(VS Code workspace folder, or the CLI's working directory). This library repo intentionally keeps
skills under a plain `/skills/` folder instead (so it can be a clean, browsable catalog), which
means there are two ways to make a skill *actually invocable* by chat:

**Option A — install into the project you're working in (recommended for real work)**

```bash
./install.sh <skill-name> <target-repo-path>       # symlink (default) — edits here propagate live
./install.sh <skill-name> <target-repo-path> --copy # copy — a frozen, version-pinned snapshot
./install.sh --all <target-repo-path>               # install every skill in this library
```

This drops a symlink (or copy) at `<target-repo-path>/.github/skills/<skill-name>/`. Then, in
that project:

1. Open (or reload) that repo as your chat/VS Code workspace.
2. Reload the window (`Developer: Reload Window` in VS Code, or restart the CLI session) so the
   assistant re-scans `.github/skills/`.
3. Just describe what you want in plain language — the assistant matches your request against
   each skill's `description` frontmatter (the "Use when..." text in `SKILL.md`) and loads the
   matching skill automatically. You don't need to name the skill explicitly, e.g.:
   - "generate the hifmc als for the repo `/home/sleonarx/mmg`" → loads `generate-hifmc-als`
   - "check for CSR address overlaps in cnic" → loads `csr-checks`
   - "extract the read-only registers for MMG" → loads `extract-csr-access`
   You *can* also name it explicitly ("use the csr-checks skill to...") if you want to be sure.

**Option B — work directly inside this skills repo**

This repo already has `.github/skills` symlinked to `/skills/`, so if you open
`/home/sleonarx/skills` itself as your chat workspace, every skill here is auto-discoverable
immediately — no install step needed. Useful while authoring/testing a new skill before deploying
it elsewhere with `install.sh`.

> Note: `~/.github/skills/*` (your personal home-level skills, always visible regardless of which
> repo you have open) are already symlinked into this library — see `ls -la ~/.github/skills/`.
> Editing a skill here updates it everywhere it's installed, instantly.

## Adding new content

Create a new folder inside `/skills/` or `/agents/` for each item you add and keep any supporting
files with it. For skills, every `SKILL.md` must include a "When to Use" section and a
"Usage / Procedure" section with real invocation examples — see
[skills/README.md](skills/README.md) for the exact convention.