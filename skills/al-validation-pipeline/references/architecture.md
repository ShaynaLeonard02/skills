# al_validation.groovy — Architecture Walkthrough

Source: [al_validation.groovy](/home/sleonarx/ci-general/SDK/al_validation.groovy) in
`firmware.ethernet.validation.tools.ci-general` (loaded alongside `SDK/generalSdkScripts.groovy`,
`ci_groovy/*.groovy`, `mev/scripts/generalScripts.Groovy`).

Purpose: given an **OSAL** (CRIF source — a folder of per-IP register CSVs, or a pre-built
`.csv.bz2`) and a set of **autoload CSVs**, run `NVMGenerator/validate_auto_load.py` and report
pass/fail by email + archived `log.txt`. It is the Jenkins twin of the local `al-intake`
validation flow (same validator, same mini_crif-erase-first rule).

## 1. Job setup

- `agent { label 'SyDK_pipline' }` (note the historical typo in the label — do not "fix" it
  without relabeling the agents).
- `options`: `timestamps()`, 24h timeout, 30-day build/artifact retention.
- `post { cleanup { cleanWs(...) } }` — the workspace is **always wiped**; console text and
  archived artifacts are the only post-mortem evidence.

### Parameters

| Param | Meaning | Notes |
|---|---|---|
| `OSAL` | OSAL folder or `.csv.bz2` path on disk, **or** a GitHub tree/blob URL | URL must be `https://github.com/intel-restricted/<repo>/tree|blob/<branch>/<path>` |
| `AUTO_LOAD` | AL file or folder on disk, or a GitHub tree/blob URL | Single file must end `.csv`; folder must contain `*.csv` |
| `OUTPUT_PATH` | Optional disk path to also copy `log.txt` to | Plain `cp`, fails the output stage on error |
| `EMAIL_ADDRESS` | Recipient of the result email | Default `default@example.com` |

### Environment / fields

- `ws_path = ${pwd()}/ws`, `nvm_generator_path = ws/nvm_generator`, `utils_path = ws/utils`,
  `output = ${pwd()}` (workspace root — this is where the validator writes its outputs).
- Field constants: `PYTHON_VENV` / python exe under
  `/nfs/site/proj/mmg/SyDK/SDK-releases/sdk_venv/`, `NVM_GENERATOR_REPO =
  firmware.ethernet.mountevans.snic-nvm-generator` (branch `main`), `UTILS_REPO =
  firmware.ethernet.mountevans.nvm-team-utils`.

## 2. Stage `cloning_repos`

1. `generalScripts().CopyConfigurations(pwd(), 'sdk_sie')` — sets up `.netrc` for GitHub auth.
2. `clone_repo` of **snic-nvm-generator** (`intel-innersource`, branch `main`) into
   `ws/nvm_generator/`, and of **nvm-team-utils** (`intel-innersource`) into `ws/utils/`.
   `clone_repo` does `rm -rf <repo>` then `git clone` (optionally `-b <branch>`) and `error()`s
   the build on non-zero status.
3. **Input resolution** — for each of `AUTO_LOAD` and `OSAL`:
   - If it contains `https://github.com/` → `resolveGithubRef(url, 'restricted')`:
     - Splits on `https://github.com/intel-restricted/` and `/tree/` (or `/blob/` for a single
       file); errors on any other shape (`Unsupported GitHub URL format`).
     - Branch names may contain `/`, so the branch is found by `git ls-remote --heads` and
       taking the **longest branch name that is a prefix** of the remaining path; the rest is
       the folder/file path. Errors if no ref matches.
     - Clones that repo/branch into `ws/al/` or `ws/osal/` and sets
       `al_source_path` / `osal_source_path` to `<clone>/<repo>[/<folder>]`.
   - Otherwise the trimmed parameter is used as an on-disk path verbatim.

## 3. Stage `copying_utils_to_nvmgenerator`

`cp -r ws/utils/firmware.ethernet.mountevans.nvm-team-utils/* ws/nvm_generator/<repo>/` — the
team-utils tree (including validator helpers) is overlaid onto the NVMGenerator checkout.
Non-zero `cp` status → `error()`.

## 4. Stage `remove_mini_crif`

Deletes any `mini_crif` folder found (recursively, `find -type d -name mini_crif -exec rm -r`)
in **two** places:

1. `$output` (the workspace root, where the validator will run with `-o`), and
2. the **parent directory of `osal_source_path`** (so an on-disk OSAL next to a stale
   `mini_crif` gets regenerated too).

This enforces the same rule as the local `al-intake` skill: never validate against a stale
mini_crif — erase it and let `validate_auto_load.py` rebuild it from the given OSAL.

## 5. Stage `prepare_al_folders`

1. Creates `ws/secure_autoloads/` and stages the AL input into it:
   - `al_source_path` is a **file** → must end with `.csv`
     (`error("Autoload file '...' is not a CSV file")` otherwise) → copied in.
   - `al_source_path` is a **directory** → must contain at least one `*.csv`
     (`error("No CSV autoload files found in '...'")`) → all `*.csv` copied in.
   - Neither → `error("Autoload file or folder '...' does not exist")`.
2. Builds (does **not** run) the command:

```bash
/nfs/site/proj/mmg/SyDK/SDK-releases/sdk_venv/bin/python3 \
    ws/nvm_generator/<repo>/NVMGenerator/validate_auto_load.py \
    -c <osal_source_path> \
    -al ws/secure_autoloads \
    -o <workspace_root> \
    2>&1 | tee ws/nvm_generator/<repo>/NVMGenerator/log.txt
```

## 6. Stage `al_validation`

Runs the command above inside `catchError`, appending `exit ${PIPESTATUS[0]}` so the **tee does
not mask the validator's exit code**. Non-zero → `error("validate_auto_load.py failed!")`,
which `catchError` converts to: build result = FAILURE, **but the next stage still runs**.
`script_status` keeps the raw exit code for the email subject.

## 7. Stage `output`

1. If `params.OSAL` does **not** contain the substring `.csv` (i.e. it was a folder, so the
   validator compiled it), `archiveArtifacts '<osal_dirname>.csv.bz2'` from the workspace root
   — the OSAL compiled by the validator (`-o $output` puts it there).
2. `archiveArtifacts 'log.txt'` from the `NVMGenerator/` dir.
3. If `OUTPUT_PATH` is set: `cp log.txt <OUTPUT_PATH>` (errors the stage on failure).
4. `emailext` to `EMAIL_ADDRESS` with `log.txt` attached; subject is
   `"AL validation succeeded!!!!!"` when `script_status == 0`, else
   `"AL validation failed, autoloads are invalid"`.

## Quirks & known gotchas (code-read, not log-confirmed unless noted)

1. **`params.UTILS_REPO_BRANCH` does not exist.** The utils clone passes
   `branch=params.UTILS_REPO_BRANCH`, but no such *parameter* is declared (only the `@Field`
   constant `UTILS_REPO_BRANCH='main'`). `params.UTILS_REPO_BRANCH` is null → the clone runs
   **without `-b`**, i.e. the utils repo's default branch — fine while the default is `main`,
   silently wrong the moment it isn't. Fix: use the field constant (or add the parameter).
2. **`removeFolderIfExists` echo is unreliable.** `find ... -exec rm -r {} +` exits 0 even when
   nothing matched, so the stage echoes `Folder 'mini_crif' has been removed from ...` whether
   or not a mini_crif existed. Don't trust that line as proof a stale mini_crif was present.
3. **`.csv.bz2` OSAL inputs skip artifact archiving.** The output stage checks
   `!params.OSAL.contains('.csv')` — a pre-built `foo.csv.bz2` path *contains* `.csv`, so no
   compiled-OSAL artifact is archived (intended: nothing was compiled). But an OSAL **folder**
   whose path merely contains `.csv` anywhere would also be mis-skipped.
4. **Unguarded `archiveArtifacts` can fail a green validation.** If the validator did not
   produce `<osal_dirname>.csv.bz2` at the workspace root (naming change, early validator
   exit), `archiveArtifacts` fails the `output` stage — the build goes red *after*
   `validate_auto_load.py succeeded`. Check the verdict line before blaming the ALs.
5. **Only `intel-restricted` GitHub URLs are supported** for OSAL/AUTO_LOAD
   (`resolveGithubRef(url, 'restricted')` is hardcoded); the tool repos themselves come from
   `intel-innersource`. An innersource/public URL fails with `Unsupported GitHub URL format`.
6. **`catchError` semantics.** A validation failure marks the build FAILED but still archives
   `log.txt` and sends the failure email. A *stage* failure before `al_validation`
   (clone/copy/prepare) aborts normally — no email is sent in that case.
7. **Default email recipient is `default@example.com`** — if the parameter is left untouched,
   the result email goes nowhere useful.
8. **Evidence lifetime.** `cleanWs` in `post{cleanup}` wipes everything; only the console text,
   the archived `log.txt` (+ optional `OUTPUT_PATH` copy) and the `.csv.bz2` artifact survive.
