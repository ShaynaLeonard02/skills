# compare_drop_to_github.groovy — Architecture Walkthrough

Source: [compare_drop_to_github.groovy](/home/sleonarx/ci-general/SDK/compare_drop_to_github.groovy)
in `firmware.ethernet.validation.tools.ci-general`. The pipeline itself is a thin wrapper: it
clones `firmware.ethernet.mountmorgan.sdk-tools` and runs one python script,
`scripts/compare_drops2github.py`, which is **not** checked out in this workspace — read it live
via the `github_repo`/`github_text_search` tools against
`intel-restricted/firmware.ethernet.mountmorgan.sdk-tools` (`fetch_webpage` 404s on
intel-restricted GitHub).

## 1. Job setup

- `agent { label 'SyDK_pipline' }` (note the historical typo in the label, shared with other
  ci-general SDK pipelines — don't "fix" it without relabeling the actual agents).
- `options`: `timestamps()`, 24h timeout, 30-day build/artifact retention.
- `post { cleanup { cleanWs(deleteDirs: true, notFailBuild: true) } }` — workspace is always
  wiped; the console log and the archived `output/**` artifacts (if archiving happened — see
  known-issues.md #4) are the only post-mortem evidence.

### Parameters

| Param | Default | Meaning |
|---|---|---|
| `SCM_BRANCH` | `SyDK_tools` | Not referenced anywhere in the script body — it is consumed by Jenkins' own "Pipeline script from SCM" job config to pick which branch of `ci-general` to load `compare_drop_to_github.groovy` from. Don't go looking for in-script usage. |
| `SDK_TOOLS_REPO_BRANCH` | `main` | Branch of `firmware.ethernet.mountmorgan.sdk-tools` to clone — this one **is** used (passed to `clone_repo`). |
| `to_compare` | (none, required) | Path to a CSV file **on the Jenkins agent's disk** (e.g. mounted via VNC/NFS) — not uploaded through Jenkins. See §3 for schema. |
| `RELEASE` | `''` | Optional Artifactory URL ending in `osxml/` — see §4. |

## 2. Stage `compare_drop_2_github` (the only stage)

1. `generalScripts().CopyConfigurations(pwd(), LINUX_USER_NAME)` (loads
   `mev/scripts/generalScripts.Groovy`) — writes `.netrc` (GitHub OAuth token from Jenkins
   credential `CREDENTIALS_ID_GITHUB_TOKEN_MAIN`, via `github()`) and `.gitconfig` into both
   `$sourceRoot/home/$LINUX_USER_NAME/` and `~` for the `sdk_sie` linux user, so all subsequent
   `git clone`/`git.Repo.clone_from` calls (groovy's and the python script's) authenticate.
2. **Local `clone_repo(...)`** function (defined at the top of this file, not loaded from a
   shared lib) clones `firmware.ethernet.mountmorgan.sdk-tools` (branch
   `params.SDK_TOOLS_REPO_BRANCH`) into `${pwd()}/ws/sdk_tools/`. On non-zero git-clone exit it
   calls `error()` and the build fails here, before any comparison runs.
3. Builds the command:
   ```
   source <sdk_venv>/bin/activate
   python3 scripts/compare_drops2github.py -c <to_compare> [-r <RELEASE>]
   ```
   run from `dir("${sdk_tools_path}/${SDK_TOOLS_REPO}")`. The `-r` flag is only appended if
   `params.RELEASE?.trim()` is truthy (non-null, non-blank after trim).
   - Note: `script_command` is assigned without `def`/a type (`script_command = "..."`) —
     becomes an implicit script-binding variable. Harmless as written (used once, right after
     assignment) but a landmine if this stage is ever copy-pasted into a `parallel {}` block.
4. `int status = sh(... returnStatus: true ...)`. If `status != 0`,
   `error("scripts/compare_drop_2_github.py failed!")` — **note the error text uses the
   underscored/legacy filename `compare_drop_2_github.py`, not the real
   `compare_drops2github.py`**; this is a cosmetic mismatch in the message only, don't chase a
   "wrong file" theory from it.
5. `archiveArtifacts artifacts: 'output/**', fingerprint: true` — only reached if step 4 did
   **not** `error()`. See known-issues.md #4 for why a script-level crash loses these artifacts
   entirely even when `output/output.csv` was already fully written to disk.

## 3. `to_compare` CSV schema (enforced only by column *position*, not validated)

Confirmed from `compare_drops2github.py`'s `main()` (`csv.reader(..., delimiter=",")`) and the
live example at
[ci-general/SDK/example_compare_drop_to_github.csv](/home/sleonarx/ci-general/SDK/example_compare_drop_to_github.csv):

```
IP name, new release drop path, IP repo, branch name
HIF,/nfs/site/disks/mmg_drops/HIF/release_27,https://github.com/intel-restricted/rtls.ethernet.mountmorgan.registerdescription.hif.git,main
ATE,/nfs/site/disks/mmg_drops/ATE/release_27,https://github.com/intel-restricted/rtls.ethernet.mountmorgan.registerdescription.ate.git,main
FXP,/nfs/site/disks/mmg_drops/FXP/release_27,https://github.com/intel-restricted/rtls.ethernet.mountmorgan.registerdescription.fxp.git,dev/release_27
```

| Column (index) | Meaning |
|---|---|
| `row[0]` IP name | Free text, used only for display/output and the diff-file naming (`diff_github_<ip>_<n>.txt`). |
| `row[1]` drop path | Path to the drop **root** dir (not the `osxml` dir itself). `navigate_and_compare()` walks `<drop>/src/**`, collects any dir whose name contains `"reg"` (skipping a dir containing `"cxp"` if the drop path itself contains `"fxp"` — avoids matching `cxp`'s register dir inside an `fxp` drop), takes the **first** match, and appends `/src/osxml`. If none found, or `<drop>` isn't a directory at all, it prints an error and returns `None` — see known-issues.md #2. |
| `row[2]` IP repo | Full `git clone` URL (passed verbatim to `git.Repo.clone_from`), **not** a bare repo name. **May be left empty** to skip the GitHub comparison for that IP (then the "comparing to github" column is `"N/A"`) — but see known-issues.md #1 for the `has_tag` side effect of doing this. |
| `row[3]` branch name | Branch to `git clone -b <branch>` from `row[2]` and diff against. |

**The first row is always consumed as a header via `next(reader)` and is never validated** — if
the user's CSV is missing a header line, the first real IP's row silently becomes the "header"
and is dropped from the comparison with no error at all (known-issues.md #5).

## 4. `RELEASE` / `-r` parameter

Optional, a full Artifactory URL ending in `osxml/`, e.g.:
```
https://af01p-il.devtools.intel.com/artifactory/ipu-sdk-il-local/SDK/HW_RELEASES/SyDK.HW.MMG400_A0.rev2.0/osxml/
```
When given, `main()` adds a **second**, independent diff per CSV row (drop vs. that release's
`osxml/<ip>`), regardless of whether the GitHub comparison ran. Parsing is pure positional string
slicing on `my_release.split('/')` (known-issues.md #6):
- `segments[4:]` (drops `https:`, `''`, host, `artifactory`) = the Artifactory repo-relative
  download path passed to `download_directory_to_ip()` (from `artifactory_controller.py`, which
  shells out to the `jf rt dl` JFrog CLI) — **still includes** the repo key (e.g.
  `ipu-sdk-il-local`).
- `segments[5:]` (also drops the repo key) = the local path JFrog preserves under the download
  dir — used to build `release_dir_to_compare = abspath(join(release_dir, path_for_osxml, row[0]))`.
- The `release/` scratch dir (`create_dir('release')`) is **recreated per CSV row**, not
  cumulative across rows.
- If `-r` is omitted, the "comparing to release" output column is `"N/A"` for every row — the
  column header is appended either way.

## 5. `compare_drops2github.py` — function-by-function (source: sdk-tools `scripts/`)

- `parse_args()` — `-c` (required, csv path), `-r` (optional, release URL).
- `clean(this_dir)` — `shutil.rmtree` if the dir exists.
- `clone_repo(repo_url, branch, clone_dir="repo_clone")` — **always clones into the same fixed
  `repo_clone` dir** (`clean()`s it first), so each CSV row's github clone overwrites the
  previous row's. This is fine functionally since rows are processed strictly sequentially and
  each row's diff happens before the next clone, but it means **only one IP's clone exists on
  disk at a time** (relevant if you're trying to inspect a clone after a failure — it'll be the
  *last* row's, not necessarily the failing one's).
- `check_branch_exist(repo_url, branch)` — calls `clone_repo`; returns the cloned path on
  success, or `False` (catching only `git.exc.GitCommandError`, e.g. bad branch/URL) — other
  exceptions (auth failures, network) propagate up to the per-row `try/except` in `main()`.
- `navigate_and_compare(drop)` — see §3; returns the resolved `<drop>/src/.../osxml` path or
  `None`.
- `diff_file_name(ip_name, comparing)` — `comparing` is `"github"` or `"release"`; maintains a
  **global per-comparing-kind counter** keyed by the *previous* ip name seen for that kind, so
  repeated IP names (shouldn't normally happen in one CSV) get `_1`, `_2`, ... suffixes; a new IP
  name resets the counter to 1.
- `create_dir(dir_name)` — erases and recreates; returns the absolute path.
- `delete_empty_files(folder_path)` — removes any 0-byte `*.txt` (i.e., diff files where the diff
  produced no output — typically a genuinely-equal comparison that still created an empty diff
  file before being overwritten by the `' equal'` row suffix logic... in practice, empty diff
  files are the normal "equal" case and are cleaned up so `output/` only contains diffs that show
  real differences).
- `check_tag(release_drop_path, repo_path, branch_name)` — opens the **already-cloned** github
  repo at `repo_path` (the clone from `check_branch_exist`) and checks whether a tag named
  `"<branch_name>_<basename(release_drop_path)>"` exists — i.e., whether this exact drop
  (identified by its directory basename, e.g. `release_27`) was already tagged into GitHub for
  that branch. Requires `repo_path` to be valid (a prior successful `check_branch_exist`); see
  known-issues.md #1 for what happens when the github comparison was skipped.
- `main()` — per CSV row (inside a `try/except Exception as e: print(RED + f'ERROR: {e}' + RESET)`
  that **does not re-raise**, so one bad row never aborts the whole run — but see
  known-issues.md #2/#1 for cases where this swallows the row **out of `output.csv` entirely**):
  1. `drop_to_compare = navigate_and_compare(row[1]).strip()`.
  2. If `row[2]` non-empty: clone+diff against GitHub (`check_branch_exist`, `check_tag`,
     `diff -rw <github_osxml> <drop_to_compare> > diff_github_<ip>_<n>.txt`, bucket result by
     exit code: `0` → `' equal'`, `1` or `"No such file or directory"` in stderr → `' not equal'`,
     else → `' ERROR'` (with stderr printed). Else: append `"N/A"` for that column (and see
     known-issues.md #1 re: `has_tag`).
  3. If `-r` given: same `diff -rw` pattern against the Artifactory release's `osxml/<ip>` (via
     `download_directory_to_ip`), independent of step 2. Else: `"N/A"`.
  4. Appends the `has_tag` column, writes the row to `output/output.csv`.
  - After the loop: `delete_empty_files(output_dir)` then `clean(repo)` — `repo` is whatever the
    **last** successful `check_branch_exist()` call returned (or still `None` if no row ever
    reached that line successfully) — see known-issues.md #3.
