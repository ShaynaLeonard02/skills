---
name: al-intake
description: 'Run an "AL intake": validate Autoload (AL) NVM CSV changes against their CRIF via validate_auto_load.py (erasing/regenerating the corresponding mini_crif first), and - only after explicitly confirming destination path and method with the user - propagate the validated ALs and mini_crifs from one environment tree (e.g. an EMULATION_A0 stepping) into another (e.g. its SIMICS_A0/vpNN counterpart). Use when asked to: do an AL intake, validate autoload/AL changes, run AL validation, validate ALs from the last N commits or working tree, erase and regenerate a mini_crif, copy/sync ALs from emulation to simulation/simics, propagate validated autoloads to another environment.'
argument-hint: "<al_file>... | --git-changed <repo_root> [--since <git-rev>]"
---

# AL Intake (validate + optionally propagate Autoloads)

An "intake" is the two-stage process used whenever Autoload (AL) `*.csv` / `*.nvm.csv` files
change in an `nvm-generator-config-*` repo:

1. **Validate** every changed AL against its CRIF with `validate_auto_load.py`, after erasing the
   AL's cached `mini_crif` so it is regenerated fresh (a stale mini_crif can mask real problems).
2. **Optionally propagate** the validated ALs (and their regenerated mini_crifs) from an
   emulation-style environment tree into its simulation counterpart — **only after explicitly
   asking the user, in that conversation, which destination path and which method (copy vs
   symlink) to use.** Never assume this from a prior intake, a README convention, or what the
   destination currently looks like — those can *inform* the question, but the user must confirm
   it every time before anything is written.

## When to Use

- "do an AL intake for `<repo>`"
- "validate the AL changes I just made / the last N commits"
- "run AL validation on these files: ..." (mirrors a VS Code `"AL validation"` debugpy launch
  config that calls `validate_auto_load.py -c <crif> -al <al_file>`)
- "erase the mini_crif and re-validate `<al_file>`"
- "copy the new ALs to simulation/simics" / "sync these autoloads to the SIMICS environment"

## Step 1 — Validate

Resolution rules baked into [al_intake_common.py](./scripts/al_intake_common.py) (see it for the
exact logic):

- **Environment root**: an AL at `.../<env_root>/ALs/<IP>/<file>.csv` has its CRIFs at
  `<env_root>/crifs/*.bz2` and its mini_crif cache at `<env_root>/crifs/mini_crif/`.
- **mini_crif filename**: the AL's basename with only its final `.csv` replaced by
  `_mini_crif.bz2` — e.g. `ecq_init.nvm.csv` → `ecq_init.nvm_mini_crif.bz2`,
  `lantx_default.csv` → `lantx_default_mini_crif.bz2`. Always erase this before validating; the
  validator regenerates it.
- **CRIF resolution**: read the AL's first data row's `BlockName` (e.g. `ecq_top_map/...`), take
  the root before the first `/`, and match it to a `crifs/*.bz2` file — first via a known-alias
  table, then via a decompressed content scan as a fallback (never guess from the AL filename
  alone; `secure_autoloads/*_secure_init.nvm.csv` files don't encode the IP in their own name).
- **validate_auto_load.py location**: default search order is
  `/home/sleonarx/nvm-gen/NVMGenerator/validate_auto_load.py`, then the `ggl`/`row` project
  clones' `.../nvm-generator/NVMGenerator/validate_auto_load.py`. Override with `--validator` or
  `$NVM_VALIDATOR` if none of those exist in this environment.
- **PYTHONPATH**: defaults to `/home/sleonarx/.local/lib/python3.12/site-packages` (matches every
  observed `"AL validation"` launch config); override with `--pythonpath` if needed.
- **Pass/fail is judged from the validator's own output**, never just its exit code: look for
  `Validation of '<al>' succeeded!!!`. High-volume warnings like
  `has invalid AUTO_LOAD_EXT value` and the "only relevant in the case where there is only ONE
  autoload... missing: Register '<lockout-bit reg>'" note (when a separate `secure_autoloads` AL
  owns that register) are **expected noise**, not failures — the script filters them from the
  printed tail but keeps them in the full per-AL log for inspection.
- **Some CRIFs are large** (e.g. `lanpe.csv.bz2` ~8 MB compressed) and a single validation run can
  legitimately take up to ~10 minutes on first touch. The script streams output live and uses a
  generous default `--timeout` (1800s) — don't assume a long-running validation has hung; check
  the per-AL log / stdout for growing output before killing it.

```bash
# Explicit AL file(s)
python3 <skill-dir>/scripts/validate_als.py <al_file> [<al_file> ...]

# Every AL changed in the working tree of a repo
python3 <skill-dir>/scripts/validate_als.py --git-changed <repo_root>

# Every AL changed across the last N commits (or any git revspec vs HEAD)
python3 <skill-dir>/scripts/validate_als.py --git-changed <repo_root> --since HEAD~2

# Force a specific CRIF (skip auto-resolution) - only for a single AL file
python3 <skill-dir>/scripts/validate_als.py <al_file> --crif <path/to/crif.csv.bz2>
```

Report back a `PASS`/`FAIL` line per AL file (the script already prints this table) plus the
overall `N/M passed`. If anything failed, open its log under `--log-dir` (default
`/tmp/al_intake_logs/<al_basename>.log`) before concluding — don't guess at the cause.

## Step 2 — Propagate to another environment (requires user confirmation, every time)

**Hard rule: never invoke [copy_als_to_destination.py](./scripts/copy_als_to_destination.py) with
`--yes` until you have explicitly asked the user, in the current conversation, both (a) the exact
destination root path and (b) copy vs symlink — even if a previous intake in this same repo used
a particular answer, and even after inspecting the destination to see what convention it already
follows.** Inspecting the destination (e.g. "are the existing files there symlinks or regular
files?") is good practice to *inform* the question you ask, but it is not a substitute for asking.

Procedure:

1. Identify the candidate destination from context (sibling `*_SIMICS_A0/vp<N>` or
   `*_EMULATION_A0/R<N>` directory next to the source tree) but do **not** act on it yet.
2. Ask the user to confirm (or override) the destination root, and ask whether to copy or
   symlink. Optionally show them what the destination currently uses today (inspect for existing
   symlinks vs regular files there) as context for their decision.
3. Run a dry-run first (default behavior — omitting `--yes` only prints the plan, writes
   nothing) so you and the user can see exactly what would happen:

```bash
python3 <skill-dir>/scripts/copy_als_to_destination.py \
    --src-root  <env_root>/FLASH_s \
    --dest-root <other_env_root>/FLASH_s \
    --mode copy \
    --git-changed <repo_root> --since HEAD~2
```

4. Only after the user confirms, re-run the identical command with `--yes` appended to actually
   write the files:

```bash
python3 <skill-dir>/scripts/copy_als_to_destination.py \
    --src-root  <env_root>/FLASH_s \
    --dest-root <other_env_root>/FLASH_s \
    --mode copy --yes \
    --git-changed <repo_root> --since HEAD~2
```

   For symlinks use `--mode symlink` (add `--relative-symlink` if the user wants a relative link
   instead of absolute).
5. In `copy` mode the script preserves each destination file's pre-existing permission bits (some
   ALs in `SIMICS`/`SIMS` trees are `755`) and verifies every copy byte-for-byte with `filecmp`.
6. Report the final `N/M files applied successfully` summary and remind the user that **no
   commit is performed** — they should review `git status` in the destination repo themselves.

## Extending

- New alias needed for `resolve_crif()`? Add it to `KNOWN_CRIF_ALIASES` in
  [al_intake_common.py](./scripts/al_intake_common.py) — the content-scan fallback means an
  incomplete alias table degrades gracefully rather than mis-resolving.
- New environment (e.g. a repo where `validate_auto_load.py` lives somewhere else)? Add it to
  `DEFAULT_VALIDATOR_CANDIDATES` in [validate_als.py](./scripts/validate_als.py), or just pass
  `--validator`.
