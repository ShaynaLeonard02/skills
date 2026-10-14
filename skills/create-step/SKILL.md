---
name: create-step
description: 'Create a new stepping/environment directory ("step") for an NVM-generator-config product repo (mmg-800, mmg, mev, cnic), e.g. a new EMULATION/R<N> revision, a new SIMICS/vp<N> tree, or a hybrid step that mixes two existing steppings (e.g. R27_R28_hybrid). Use when asked to: create a step, create a new step, build a hybrid step, make an R<N>_R<M>_hybrid, add a new stepping/revision folder, mirror an EMULATION revision into SIMICS, or compose one steppings AL/crif/mini_crif tree out of two source revisions.'
argument-hint: "<product repo> <new step name> <base source> [--override <source> for <what>]"
---

# Create Step (new stepping / hybrid stepping directory)

A "step" in these repos (`mmg-800`, `mmg`, `mev`, `cnic`) is a directory under
`<PRODUCT>/EMULATION/R<N>/` or `<PRODUCT>/SIMICS/vp<N>/` holding a full
`FLASH_s/` + `FS_s/` tree: `ALs/<IP>/*.csv`, `crifs/*.csv.bz2` +
`crifs/mini_crif/`, `features/`, `Autoload.json`, `Features.json`,
`Green_List.json`, `bid.json`. This skill builds a **new** step by composing
files from one or more **existing** steps already in the repo — it does not
invent register content.

## Two known construction patterns (verified in mmg-800 git history)

| Pattern | Example | Git mode | How it's built |
|---|---|---|---|
| SIMICS mirror | `MMG800/SIMICS/vp28` → `MMG800/EMULATION/R28` | `120000` (symlink) | Nearly every leaf file/folder under `ALs/<IP>/`, `crifs/*.bz2` (incl. `mini_crif/`), `Autoload.json`, `Green_List.json` is a **relative symlink** into the sibling EMULATION tree. A handful of paths are real files instead because they're env-specific: `FS_s/ALs/RDMA/*`, `FLASH_s/ALs/secure_autoloads/rdma_top_secure_*`, `FS_s/Features.json`, `FS_s/features/board_id.csv`, `FLASH_s/crifs/nlf_gpreg.csv.bz2`, `bid.json`. |
| EMULATION hybrid | `MMG800/EMULATION/R28_R29_hybrid` | `100644` (real copy) | The **whole tree is physically copied** from a base stepping, then a confirmed subset of ACC-related files is **also physically copied** (overlaid) from a second, newer stepping. Nothing here is a symlink. |

**These are genuinely different conventions** — don't assume one implies the
other. If the user asks for a step "like vpNN", they mean symlinks; if they
ask for a hybrid step "like R28_R29_hybrid", the only known precedent uses
real copies. **Always ask which one they want** (see Step 0) rather than
silently picking based on the step's name.

## Step 0 — Ask before building anything (every time)

Never run [build_step.py](./scripts/build_step.py) with `--yes` until you have
explicitly asked the user, in the current conversation, all of:

1. **Destination path** — which repo and exact new folder name/location
   (e.g. `MMG800/EMULATION/R27_R28_hybrid`).
2. **Base source** (the default/fallback tree) and, if it's a hybrid,
   **override source** (the tree that wins for a specific subset of files).
3. **Materialization method** — `copy` or `symlink` (see table above).
4. **The exact override file/glob set**, if building a hybrid — see Step 2.
   Never reuse a previous hybrid's override list verbatim for a new pair of
   steppings; file sets drift release to release (e.g. between mmg-800 R27
   and R28, `clkss_cpu.csv.bz2`/`clkss_ts.csv.bz2`/`DW_apb_gpio_crif.csv.bz2`
   exist only in R27, and `gic700_imc.csv.bz2` only in R28 — unrelated to any
   override reason, just normal file-set churn).
5. Whether to also register the new step in `ProjectSteppingSettings.json`
   and/or regenerate hash entries in `al_path_to_hash_bid.json` (repo root).
   Precedent evidence: `R28_R29_hybrid` was **never** added to either file,
   and `ProjectSteppingSettings.json` in mmg-800 only lists `R25`, `R27`, and
   `latest` even today (not `R28`) — hybrid/dev steps appear to be
   intentionally left out of that registry. Don't add entries unless asked.

## Step 1 — Discover the override candidates (for hybrid steps)

Run [diff_steppings.py](./scripts/diff_steppings.py) on the two source trees
to see every path that differs, plus a same-name-keyword hint (`acc`, `cmn`,
`gic700_imc`, `gic700_gicd_acc`, `imc_pre` by default — override with
`--keyword`):

```bash
python3 <skill-dir>/scripts/diff_steppings.py \
    /home/sleonarx/mmg-800/MMG800/EMULATION/R27 \
    /home/sleonarx/mmg-800/MMG800/EMULATION/R28
```

Important: the candidate scan covers **every file in either tree**, not just
ones that differ — an override reason like "ACC-related" is about a file's
*identity*, not whether its bytes currently happen to match between the two
revisions (several ACC files are byte-identical between R27 and R28 today,
but still belong in the override set because their ownership is ACC, not
because they changed).

**The keyword hint is not a decision — verify every hit against the IP-block
glossary before trusting it.** Naive substring matching produces false
positives: in mmg-800 R27 vs R28, `hif_acc_rp.nvm.csv` and
`hif_acc_usp.nvm.csv` matched `acc` but are **HIF** root-port/upstream-port
registers, unrelated to the ACC (Arm Compute Complex) block — they must be
excluded from an "ACC-related" override set. Conversely a block that doesn't
contain "acc" in its filename can still be ACC's (e.g. `cmn700_mmg.csv.bz2`,
`gic700_gicd_acc.csv.bz2` — CMN and GIC are infrastructure *for* the ACC per
the repo's IP-block glossary). Always show the user the full candidate list
with your reasoning, and let them add/remove entries, before building.

**Verified ACC-related override set for mmg-800 R27→R28** (confirmed by
diff + glossary cross-check, reusable as a starting point for this exact
pair, but re-verify for any other pair of steppings):

```
FLASH_s/ALs/IMC/acc_*.csv
FLASH_s/ALs/IMC/cmn_*.csv
FLASH_s/ALs/IMC_PRE/*
FLASH_s/crifs/cmn*.bz2
FLASH_s/crifs/gic700_gicd_acc.csv.bz2
FLASH_s/crifs/gic700_imc.csv.bz2
FLASH_s/crifs/mini_crif/acc_*.bz2
FLASH_s/crifs/mini_crif/cmn*.bz2
```

(`gic700_imc.csv.bz2` and its mini_crif don't exist in R27 at all, so they
can only come from R28 regardless of the override rule.)

## Step 2 — Build (dry-run first, always)

[build_step.py](./scripts/build_step.py) composes `--dest-root` from
`--base-root` (everything, by default) plus `--override-root` (only paths
matching `--override-glob`, repeatable). Omitting `--yes` only prints the
plan — nothing is written:

```bash
python3 <skill-dir>/scripts/build_step.py \
    --base-root     /home/sleonarx/mmg-800/MMG800/EMULATION/R27 \
    --override-root /home/sleonarx/mmg-800/MMG800/EMULATION/R28 \
    --dest-root     /home/sleonarx/mmg-800/MMG800/EMULATION/R27_R28_hybrid \
    --mode copy \
    --override-glob 'FLASH_s/ALs/IMC/acc_*.csv' \
    --override-glob 'FLASH_s/ALs/IMC/cmn_*.csv' \
    --override-glob 'FLASH_s/ALs/IMC_PRE/*' \
    --override-glob 'FLASH_s/crifs/cmn*.bz2' \
    --override-glob 'FLASH_s/crifs/gic700_gicd_acc.csv.bz2' \
    --override-glob 'FLASH_s/crifs/gic700_imc.csv.bz2' \
    --override-glob 'FLASH_s/crifs/mini_crif/acc_*.bz2' \
    --override-glob 'FLASH_s/crifs/mini_crif/cmn*.bz2'
```

Review the printed plan (`N files total -> base/override split`, and the
full list of override-sourced files) with the user. Only after they confirm,
re-run the identical command with `--yes` appended. For a pure SIMICS mirror
(no override tree), omit `--override-root`/`--override-glob` and use
`--mode symlink` (add `--relative-symlink` for relative instead of absolute
targets, matching the vpNN convention).

## Step 3 — After building

1. Report the final file count and the override-sourced list (already
   printed by the script) so the user can spot-check it.
2. Remind the user **no commit is performed** — review `git status` /
   `git add -N` in the product repo themselves.
3. Do **not** touch `ProjectSteppingSettings.json` or `al_path_to_hash_bid.json`
   unless the user asked for that in Step 0.

## Extending

- New product repo or layout quirk? The scripts are layout-agnostic (they
  just diff/copy/symlink two directory trees) — only the SKILL.md's "known
  patterns" table and default `--keyword` list are mmg-800-specific
  knowledge; update both as you learn the equivalent conventions in `mmg`,
  `mev`, or `cnic`.
- If a future override reason isn't glossary-backed, ask the user to name
  the exact files/globs directly instead of trying to keyword-guess it.
