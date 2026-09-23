# Skills

This is the master library of every skill I've built. Each skill lives in its own folder here,
following the same layout the assistant expects for a project skill:

```
skills/<skill-name>/
├── SKILL.md        # required — frontmatter (name, description, argument-hint) + "When to Use"
│                    # + "Usage / Procedure" (how to invoke it) + any deeper reference docs
└── scripts/         # optional — the actual implementation the SKILL.md procedure calls into
```

**Rule: every skill's `SKILL.md` must have a "When to Use" section and a "Usage / Procedure"
section with copy-pasteable invocation examples.** That's the "how to use it" requirement — kept
inside the skill itself (not a separate doc) so it can never drift out of sync with the skill.

## Index

| Skill | Description | Invoke example |
|---|---|---|
| [csr-checks](./csr-checks/SKILL.md) | Address sanity checks on CNIC register definition files (overlaps, out-of-range, intra-IP conflicts) | `python3 <skill-dir>/scripts/check_addr_conflicts.py` |
| [extract-csr-access](./extract-csr-access/SKILL.md) | Extract fully RO / WO / sensitive registers from MMG AL register CSVs | `python3 <skill-dir>/scripts/extract_ro_registers.py` |
| [generate-hifmc-als](./generate-hifmc-als/SKILL.md) | Generate HIF-MC autoload CSVs via `hifmc_db.py` and copy them into mmg / mmg-800 / cnic | `python3 <skill-dir>/scripts/generate_hifmc_als.py <target_repo_path>` |
| [al-intake](./al-intake/SKILL.md) | Validate AL/NVM CSV changes against their CRIF (erase+regen mini_crif) and, only after user confirms path+method, propagate to another environment | `python3 <skill-dir>/scripts/validate_als.py --git-changed <repo_root> --since HEAD~2` |

## Installing a skill into a repo

This library repo is a **source of truth**, not itself a place the assistant auto-discovers
skills from. To make a skill invocable in a given project, it needs to exist under that project's
`.github/skills/<name>/`. Use the installer from the repo root:

```bash
./install.sh <skill-name> <target-repo-path> [--copy]
```

- Default: creates a **symlink** `<target-repo>/.github/skills/<skill-name>` → this repo's copy,
  so edits here are picked up everywhere instantly (recommended while iterating on a skill).
- `--copy`: copies the folder instead of symlinking (use this if the target repo needs a
  self-contained, version-pinned snapshot — e.g. before a release/tag).

## Adding a new skill

1. `mkdir -p skills/<new-skill-name>/scripts`
2. Write `SKILL.md` with YAML frontmatter (`name`, `description`, `argument-hint`), a
   "When to Use" section, and a "Usage / Procedure" section with real invocation examples.
3. Put any supporting scripts under `scripts/`.
4. Add a row to the Index table above.
5. Install it into whichever repo(s) need it: `./install.sh <new-skill-name> <target-repo>`.
