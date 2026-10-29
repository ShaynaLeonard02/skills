---
name: caching-pipeline
description: 'Explain, debug, and fix the CachingPipeline.groovy Jenkins pipeline (ci-general repo, SDK/CachingPipeline.groovy) - the nightly "Caching-Nightly" job that, for the MMG800 and CNIC NVM-generator config repos, flips latest symlinks across every uncached stepping, runs generate / generate_anvm / generate_super_image per step, and opens one squashed PR of caching_files*.tar.gz + al_path_to_hash*.json per repo (plus MEV/MMG caches). Use when asked to: diagnose a failed or red Caching Pipeline build or its "Caching Pipeline FAILED" email, analyze a Caching Pipeline Jenkins console log, explain why a PR was or was not created ("doesn''t have any caching updates", "DON''T MERGE - RUN FAILED"), debug "File not exists ... ALs/HIF/hif_*.nvm.csv", "caching_files/N.csv" FileNotFoundError, generate_anvm -caching_f retry, missing hifmc ALs, skip-logic / which steps ran, symlink/latest handling, cherry-pick/squash problems, push/PR/token failures, or propose a fix/change to this pipeline (add/skip/force a step, new image group, new HIF AL).'
argument-hint: "<jenkins_console_log_path> [--repo CNIC|MMG800]  |  a question about CachingPipeline.groovy"
---

# CachingPipeline.groovy — Debug & Fix Guide

Everything needed to reason about the nightly **Caching Pipeline**
([CachingPipeline.groovy](/home/sleonarx/ci-general/SDK/CachingPipeline.groovy), repo
`firmware.ethernet.validation.tools.ci-general`): what each stage does, how steps are selected and
built, which failures have already been found/fixed, which risks are still open, and a log-forensics
script that extracts the evidence from a Jenkins console log.

## When to Use

- "the caching pipeline failed / went red — why?" / "I got the *Caching Pipeline FAILED* email"
- "analyze this Jenkins console log for the Caching pipeline"
- "why was no PR created?" / "why was a PR created but marked *DON'T MERGE*?"
- `File not exists: .../FLASH_s/ALs/HIF/hif_*.nvm.csv`, `[Verify:MMG800 index N] ... missing`
- `FileNotFoundError: .../caching_files/<N>.csv`, "generate_anvm retried with `-caching_f True`"
- "which steps did it run / skip, and why?" (history-based skip logic, `perFolderSkips`, `perFolderForceRun`)
- "I want to add / skip / force-run a step", "add an image group", "add a required HIF AL"
- push rejected, `Failed to create PR`, token/proxy errors, cherry-pick conflicts, stale
  `caching-<date>-N` branches, CPS/`NotSerializableException` in this build

## Usage / Procedure

### 1. Run the log forensics script first, whenever a console log is available

```bash
python3 <skill-dir>/scripts/diagnose_caching_log.py <jenkins_console_log>

# One repo only (per-index sections, failures, skip lines)
python3 <skill-dir>/scripts/diagnose_caching_log.py <log> --repo MMG800
python3 <skill-dir>/scripts/diagnose_caching_log.py <log> --repo CNIC

# Structured output for scripting
python3 <skill-dir>/scripts/diagnose_caching_log.py <log> --json
```

Don't have the file? Download the console text from the Jenkins build (`.../consoleText`) and save
it locally; the workspace is wiped by `cleanWs`, so the log is the only evidence.

It reports (facts only, never a verdict): stage timeline; per-folder step lists and the history-based
skip decisions (`all steps` / `historicalSkips` / `current` / after-skip list / `max steps`); every
index's symlink state (which step each `latest` pointed at) with the failures that occurred under it;
hifmc/AL copy + `[Verify:...]` results; every `generate` / `generate_anvm` / `generate_super_image`
failure with the full command, plus whether the `-caching_f True` retry healed it; per-index
`iteration ... failed` aborts; PR outcomes (created / existing / identical-tree skip / no changes /
failed / clone failed); per-repo `Result:` lines; and which **known failure signatures** (S1…S13)
matched, each linked to a [known-issues.md](./references/known-issues.md) entry. Line numbers are
`grep -n`/`view`-cross-referenceable against the same log.

### 2. Match symptoms against known issues

Read **[references/known-issues.md](./references/known-issues.md)** before investigating further. It
separates **FIXED/MITIGATED** bugs (hifmc ALs in the wrong step dir, stale caching archive +
self-healing retry, dropped `.tar.gz`s, no-op `return` in `dir{}`, LazyMap serialization) from
**OPEN/SUSPECTED** risks found by code reading (silent hifmc failures, abort swallowed by the per-index
`catch`, silent `cherry-pick --skip`, MEV/MMG collected before `generate_anvm`, CNIC `latest`-only HIF
copy, pre-flight checks SIMICS but copy writes EMULATION, email flag mix-up). Check whether the log
matches one before treating it as new; if it is genuinely new, add a numbered entry once understood.

### 3. Reason over the evidence — don't just restate the extracted facts

Walk the failure through the architecture ([architecture.md §4](./references/architecture.md#4-the-per-repo-engine-cnic-and-mmg800-share-the-same-shape)):

1. **Which stage/repo/index failed?** (timeline → `iteration index N failed` → `Per-index symlink state`)
2. **Which step was actually being built?** Use the symlink rows, not the folder name — failures are about the
   step `latest` pointed at *for that index*.
3. **Which command?** `generate` (target×image) vs `generate_anvm` (image group; check retry outcome) vs
   `generate_super_image` (boot image). Pull the generator's own traceback just above the `ERROR:` lines
   (`grep -n` the line number from the report and `view` ±80 lines).
4. **Classify:** missing input file (#1/#2/#3/#11) · CRIF/AL data error (#8) · infra (#6/#7/#9) · pipeline logic (#4/#5/#10/#12).
5. **Did a PR result anyway?** Red build + PR + "DON'T MERGE" is the designed behavior for partial failures;
   "no PR" with failures means nothing cacheable was staged, or tree equals base (`Content identical`).
6. State a root cause **and say explicitly what you could not confirm** (e.g. "log shows the pre-flight passed
   for SIMICS, but the failing step was EMULATION — see known-issues #1 caveat").

### 4. Architecture / "how does X work" questions

Read **[references/architecture.md](./references/architecture.md)** — stage flow, repos and branch
resolution, the per-repo engine (skip logic → per-index branch + symlink flip → generate → squash → PR),
GitHub helpers, output strings other tooling depends on, and log markers. Prefer the live
[CachingPipeline.groovy](/home/sleonarx/ci-general/SDK/CachingPipeline.groovy) for exact current code.

### 5. Before changing the pipeline

- Re-read the relevant function in the live file; the references can drift. Search by `def <name>`.
- **Common edits and where they live**
  | Want to… | Edit |
  |---|---|
  | skip a broken step everywhere | `stepsToAlwaysSkip` (in both `processCnicConfig` / `processMmg800Config`) |
  | skip a step for one folder | `perFolderSkips` (never list the step `latest` currently points to) |
  | force a step to run despite history | `perFolderForceRun` (MMG800 only) |
  | add a generate target/stepping | `MMG800_GENERATE_COMBOS` |
  | add an ANVM image group | `ANVM_IMAGE_GROUPS` |
  | add a CNIC boot image | `CNIC_BOOT_IMAGES` |
  | new hifmc AL the generator requires | `MMG800_REQUIRED_HIF_ALS` (+ verify the copy stage places it) |
  | change what lands in the PR | the `git add` allow-list (root `al_path_to_hash*.json`, non-root `*.tar.gz`) in squash and in `processSharedConfigRepo` |
  | recipients / schedule | `EMAIL_RECIPIENT`, `cron(...)` |
- **Keep the hard-coded email footer warnings in sync** with skip/force lists (known-issues #12).
- **Preserve contract strings**: `Repo: <repo> has caching updates: <url>` / `... doesn't have any caching updates`
  (the email regex depends on them), branch names `caching-<date>-N`, `cachingFinal-*`, and the commit subjects
  filtered by `getCommitsOnBranch` (`[Caching]: Set symlinks`).
- **Never commit hifmc CSVs or generated ALs** — they are build artifacts by design; keep unstaging them.
- **Jenkins CPS rules**: no non-serializable objects (`LazyMap`, `Matcher`) in locals that live across `sh` steps
  — parse JSON in `@NonCPS` methods returning plain Maps; `return` inside `dir{}` only exits the closure (use flags);
  avoid `.sort{}`/`.unique{}` closures on the live stack.
- **Validate**: a plain Groovy syntax check catches typos but not CPS or sandbox issues. Use the
  Jenkins Replay feature or run the pipeline with `RUN_CNIC=false`/`RUN_MMG800=false` and a throwaway
  `CNIC_BRANCH`/`MMG800_BRANCH` + `SCM_BRANCH` to exercise changes without touching `main`. Note that real
  runs push branches and open PRs with the PAT credential — warn the user before suggesting a live run.
- `flow_manager.py` / `hifmc_db.py` live in **other repos** (`snic-nvm-generator`, `hif-shared`, `imc-fw-row`),
  not in `ci-general`; generator-internal failures (e.g. CRIF validation, caching index logic) need fixes there or
  in the config repos, not in this Groovy file.

## Reference Files

- [references/architecture.md](./references/architecture.md) — purpose, repos, stages, per-repo engine, helpers, output contract, log markers.
- [references/known-issues.md](./references/known-issues.md) — numbered fixed / mitigated / open issues with symptoms, root causes, and checks.
- [scripts/diagnose_caching_log.py](./scripts/diagnose_caching_log.py) — the log-forensics tool described above.
