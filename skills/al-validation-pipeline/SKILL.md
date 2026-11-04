---
name: al-validation-pipeline
description: 'Explain, debug, and fix the al_validation.groovy Jenkins pipeline (ci-general repo, SDK/al_validation.groovy) - the on-demand "AL validation" job that clones snic-nvm-generator + nvm-team-utils, resolves the OSAL (CRIF source) and AUTO_LOAD inputs from a disk path or an intel-restricted GitHub tree/blob URL, erases stale mini_crif folders, stages all AL CSVs into a secure_autoloads dir, runs NVMGenerator/validate_auto_load.py via the sdk_venv, archives log.txt + the regenerated <osal>.csv.bz2, and emails the result. Use when asked to: diagnose a failed or red AL-validation Jenkins build or its "AL validation failed, autoloads are invalid" email, analyze an AL-validation Jenkins console log, explain a stage or the resolveGithubRef branch resolution, debug "No CSV autoload files found" / "is not a CSV file" / clone failures / missing csv.bz2 artifact / UTILS_REPO_BRANCH null-branch quirk, or propose a fix/change to this pipeline (new parameter, osal handling, email/output behavior).'
argument-hint: "<jenkins_console_log_path> [--json]  |  a question about al_validation.groovy"
---

# al_validation.groovy — Debug & Fix Guide

Everything needed to reason about the on-demand **AL validation** Jenkins pipeline
([al_validation.groovy](/home/sleonarx/ci-general/SDK/al_validation.groovy), repo
`firmware.ethernet.validation.tools.ci-general`): what each stage does, how the OSAL/AUTO_LOAD
inputs are resolved, where the validation verdict comes from, which quirks exist in the code,
and a log-forensics script that extracts the evidence from a Jenkins console log.

This job is the **CI wrapper around the same `validate_auto_load.py`** that the local
`al-intake` skill runs by hand: it validates autoload (AL) NVM CSVs against an OSAL/CRIF, after
force-erasing any stale `mini_crif` so the mini-CRIF is regenerated from the given OSAL.

## When to Use

- "the AL validation Jenkins job failed / went red — why?" / "I got the *AL validation failed,
  autoloads are invalid* email"
- "analyze this Jenkins console log for the AL validation pipeline"
- "what do the OSAL / AUTO_LOAD / OUTPUT_PATH / EMAIL_ADDRESS parameters accept?"
- "why did my GitHub URL input fail?" (`Unsupported GitHub URL format`, branch not resolved,
  wrong org — only `intel-restricted` tree/blob URLs are supported for OSAL/AUTO_LOAD)
- `Autoload file '...' is not a CSV file`, `No CSV autoload files found in '...'`,
  `Autoload file or folder '...' does not exist`
- "why wasn't the `<osal>.csv.bz2` artifact archived?" / "why did the build fail *after*
  validation succeeded?"
- "what does `resolveGithubRef` / `removeFolderIfExists` / `clone_repo` do?"
- "I want to add a parameter / change which utils branch is cloned / change the email or
  output handling in this pipeline"

## Usage / Procedure

### 1. Run the log forensics script first, whenever a console log is available

```bash
python3 <skill-dir>/scripts/diagnose_al_validation_log.py <jenkins_console_log>

# Structured output for scripting
python3 <skill-dir>/scripts/diagnose_al_validation_log.py <log> --json
```

Don't have the file? Download the console text from the Jenkins build (`.../consoleText`) and
save it locally; the workspace is wiped by `cleanWs` in `post { cleanup }`, so the console log
and the emailed/archived `log.txt` are the only evidence.

It reports (facts only, never a verdict): the stage timeline; every `clone_repo` call and its
success/failure; the resolved GitHub refs for AUTO_LOAD and OSAL (`Repository URL:` /
`Branch:` / `Folder:` echoes); the utils→NVMGenerator copy result; every `mini_crif` removal
echo; AL staging errors (not-a-CSV / no CSVs / path missing); the full `validate_auto_load.py`
command; the validation verdict line (`validate_auto_load.py succeeded` / `failed!`) plus any
validator `ERROR`/`Traceback` fragments from the tee'd output; artifact archiving
(`New name: *.csv.bz2`, `log.txt`); the optional `Log file saved to:` copy; the
`Email was sent to ...` line; and `Finished:` result. Line numbers are
`grep -n`/`view`-cross-referenceable against the same log.

### 2. Understand the stage the failure happened in

Read **[references/architecture.md](./references/architecture.md)** — a per-stage walkthrough
(parameters → `cloning_repos` → `copying_utils_to_nvmgenerator` → `remove_mini_crif` →
`prepare_al_folders` → `al_validation` → `output`), the exact `validate_auto_load.py`
invocation, and a **Quirks & known gotchas** section (code-read findings such as the
`params.UTILS_REPO_BRANCH` null-branch bug, the unreliable `removeFolderIfExists` echo, the
`.csv.bz2`-skips-archiving substring check, and the unguarded `archiveArtifacts` that can fail
a build after a successful validation).

### 3. Classify the failure

1. **Input/parameter problem** — bad OSAL/AUTO_LOAD path or URL, wrong org (`intel-restricted`
   only), branch not resolvable via `git ls-remote`, AL path has no `*.csv`. Fails in
   `cloning_repos` or `prepare_al_folders`, *before* any validation ran.
2. **Real AL↔CRIF mismatch** — `validate_auto_load.py` exits non-zero in `al_validation`;
   the verdict and the per-register findings are in `log.txt` (attached to the email, archived
   as an artifact). This is the "autoloads are invalid" email. Debug the AL content itself
   (same method as the local `al-intake` skill: field names, values, widths vs the CRIF).
3. **Infra/tooling** — clone failures (.netrc/proxy/agent), sdk_venv python missing,
   utils copy failure, `archiveArtifacts` not finding `<osal>.csv.bz2`, email/`OUTPUT_PATH`
   copy failures. Validation may have been fine; check the verdict line before blaming the ALs.

Note: `al_validation` is wrapped in `catchError`, so the `output` stage **always runs** — a red
build still archives `log.txt` and sends the failure email; conversely a green validation can
still end red if the `output` stage itself fails (see quirks).

### 4. Honesty rule

The quirks in [architecture.md](./references/architecture.md) are **code-read** findings from
[al_validation.groovy](/home/sleonarx/ci-general/SDK/al_validation.groovy) unless marked as
log-confirmed. When diagnosing, say what the log actually shows vs what is inferred, and open
the groovy source before proposing a change.
