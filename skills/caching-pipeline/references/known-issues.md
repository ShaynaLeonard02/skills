# CachingPipeline.groovy — Known Issues, Fixes & Open Risks

Institutional memory for [CachingPipeline.groovy](/home/sleonarx/ci-general/SDK/CachingPipeline.groovy).
**Read before proposing a fix** — it may already be fixed (check the code), or be a partial
re-implementation of one of these. When a genuinely new failure is understood, add a numbered entry
here (symptom → root cause → fix/status) instead of leaving it in chat history.

Status legend: **FIXED** (verified present in code, with commit), **MITIGATED**, **OPEN/SUSPECTED**
(derived from reading the code, *not* confirmed against a real failing log — verify before acting).
Refer to functions by name; line numbers drift. Headings are linked from the forensics script's
signature table, so keep the anchors stable.

---

## 1. hifmc ALs copied into the wrong step directory

**Status: FIXED** (commit `bd456fd3e`, "copying hifmc als to all steps that are going to be run").

**Symptom:** `generate`/`generate_anvm` dies inside the NVM Generator with
`File not exists: .../FLASH_s/ALs/HIF/hif_*.nvm.csv` (signature S1), or the pre-flight prints
`ERROR: [Verify:MMG800 index N] k required AL file(s) missing ...` and aborts that index.

**Root cause:** the hifmc CSVs are build artifacts that are intentionally **not committed**, so they exist only
on disk in the workspace. "Copying files" runs *before* `processMmg800Config()` repoints `latest`. Copying only
into `latest/` put them in whatever step `main` pointed at (e.g. R27) while the build later targeted another (R28).

**Fix in code:** copy into every `MMG800/EMULATION/<step>/FLASH_s/ALs/HIF`; `verifyRequiredAlFiles()` +
`MMG800_REQUIRED_HIF_ALS` (13 files) fail fast with context.

**If it recurs, check:** a new required HIF AL not in `MMG800_REQUIRED_HIF_ALS`; a step whose dir has no
`FLASH_s/ALs/HIF` (the `find ... -exec test -d` skips it); a hifmc topology that silently failed (#3); `.gitignore`
rules; and — important — the pre-flight only checks **`MMG800/SIMICS/latest/...`** while the copy loop writes only
under **`MMG800/EMULATION/*`**. Confirm how SIMICS gets its HIF files (symlink to EMULATION? separate copy?) before
trusting a green pre-flight. (OPEN/SUSPECTED mismatch — not verified.)

## 2. Stale or incomplete caching archive

**Status: MITIGATED** (self-healing retry, commit `bd456fd3e`).

**Symptom:** `FileNotFoundError: .../caching_files/<N>.csv` or `No such file or directory: .../caching_files/N.csv`
(S2), first printed under `WARNING: generate_anvm FAILED ... (cached path)`.

**Root cause:** a committed `caching.json` references indices (e.g. R28 `FS_s/ALs/FXP` has 0-7 but `PHASE2_HMA`
needs 9). On the cached path the generator only points `autoload_df_path` at `caching_files/<idx>` without
regenerating it. The archive is rewritten only by `compress_caching_directories()` **after** a successful
generation, so a crash means it can never self-repair and every nightly fails identically.

**Mitigation:** `generate_anvm` is retried once with `-caching_f True`. Interpretation of the log:
- `WARNING ... (cached path)` then no `also on -caching_f retry` error → healed; the new archive is committed.
  Expected, not a bug, but means the committed cache was stale.
- `ERROR ... (also on -caching_f retry)` → not a cache problem; look at the **retry's** traceback (CRIF validation
  #8, missing source AL, generator bug, bad config).

`generate` (non-anvm) and `generate_super_image` have **no** retry.

**Related fixed bug (do not reintroduce):** the post-generate unstage used to `git reset HEAD` the whole
`FLASH_s/ALs/HIF` dir to keep hifmc CSVs out, which also dropped `caching_files*.tar.gz` from every commit
(R28 ended with no FLASH_s cache; later cached runs then hit this exact error). Now only `*.csv` under
`MMG800/EMULATION/*/FLASH_s/ALs/HIF` are unstaged.

## 3. hifmc stage failures are silent

**Status: OPEN/SUSPECTED (by design of the code).**

`sh(returnStatus:true, script: "cd ... && <cmd> || true")` — `|| true` forces exit 0, so
`WARNING: hifmc <topology> failed` can never print. A broken `hifmc_db.py` (bad branch of `mmg_trunk`, python/venv
issue, repo layout change) shows up *later* as #1 (missing ALs) or, worse, **stale ALs from a prior step get used
silently**. When #1 appears with no hifmc warning, inspect the "Run hifmc script" stage output by hand.
Also, `cp ... output_mmg/*.csv` in "Copying files" fails the stage with a glob error if hifmc produced nothing.

## 4. Aborts are swallowed by the per-index catch

**Status: OPEN/SUSPECTED.**

`catch (Exception e)` around each index also catches Jenkins' `FlowInterruptedException` (user abort / timeout),
logging `ERROR: <REPO> iteration index N failed: ...`, setting FAILURE, then **continuing to the next index** (and
into squash/PR creation). A manually aborted or 48h-timed-out build can therefore still push branches/PRs.
Signature: `iteration index N failed` containing `Aborted by` / `Timeout`. A fix is to rethrow
`FlowInterruptedException` (or catch only `AbortException`) inside the catch.

## 5. Squash cherry-pick problems

**Status: FIXED (artifact-exclusion + conflict logging in `CachingPipeline.groovy`).**

The final branch cherry-picks every collected per-index commit onto `cachingFinal-*`, then
`reset --soft`. Historically this was `git cherry-pick <sha> --allow-empty || git cherry-pick --skip`,
with three failure modes (all observed/possible):
- A real conflict was **silently skipped** (`--skip`), so the PR could miss one index's cache with no error.
- Commits from different indices touching the same `al_path_to_hash*.json` — or the same committed
  build artifacts — conflicted → same silent skip. The dominant conflict source was that the per-index
  `git add -A` committed `generate_output/` (MMG800), `cnic_boot_image_output/` (CNIC) and root-level
  `image_*` dirs (`generate_anvm` has no `-o` and writes image dirs into the repo root); neither config
  repo's `.gitignore` covers them, and each index produces different versions of the same artifact paths.
- If `cherry-pick --skip` itself failed (no cherry-pick in progress) the step errored and the
  CNIC/MMG800 processing stage died after all generators already ran.

**Fix (in the pipeline, both repo engines):**
- Per-index commits no longer contain the artifacts: `git clean -fdq -- <output_dir> 'image_*'` runs
  before `git add -A`, plus a defensive `git reset HEAD -- <output_dir> 'image_*'` for any
  already-tracked leftovers (git clean never touches tracked files, so legit repo content is safe).
- `cherryPickWithConflictLog()` replaces the blind skip: on a failed pick it **prints the conflicting
  files (`git diff --name-only --diff-filter=U`) and the full conflict hunks
  (`git --no-pager diff --diff-filter=U`)** — so an `al_path_to_hash*.json` collision is visible in the
  log — then skips with a guarded `--skip || --abort || true` (stage can no longer die), and the caller
  sets `currentBuild.result='FAILURE'` + `env.<repo>_failed='true'` so a PR built with skipped commits
  is flagged "DON'T MERGE" instead of looking green.
- When a conflict is detected, the pipeline now keeps the per-index `caching-<date>-N` branches on
  GitHub instead of deleting them, and the results email lists the retained branches and warns that
  the conflicting changes were not included in the PR.

Look for `CONFLICT` / `could not apply` (S6) and the new
`ERROR: <REPO>: cherry-pick of <sha> FAILED` / `merge conflicts in:` markers near `cachingFinal-`
creation. *(Superseded by #16: failed indices no longer contribute commits; `*_failed` now only flags a
skipped cherry-pick.)*

## 6. GitHub / auth / proxy failures

**Status: environmental.**

- `Failed to create PR for <repo>: {...}` — the response body is in the message: `Bad credentials` (PAT
  `shayna-leonard-github-pat` expired/revoked or SSO not authorized), `Validation Failed` (no commits between
  head/base — usually means tree equal, should have been caught by `treesAreIdentical`; or base branch typo in
  `CNIC_BRANCH`/`MMG800_BRANCH`), `Not Found` (token lacks repo access / wrong repo name constant).
- `! [rejected]` / `stale info` on `git push --force-with-lease` — a branch of the same name already exists with
  different history (two runs the same second, or a previous aborted run's leftover `caching-<date>-N`).
- `Clone of <repo> failed!` — proxy/credential/branch-does-not-exist (e.g. `MMG800_BRANCH` mistyped).
- `deleteBranch()` ignores the API response; failed deletes leave stale `caching-<date>-N` branches on GitHub.
  Those are cosmetic but accumulate; clean up manually.

## 7. Workspace / disk / timeout

**Status: environmental.** Step builds are heavy (3 generate + 3 generate_anvm in parallel per index, many
indices). `No space left on device` (S9) → agent workspace/`/tmp`. The 48h `timeout` kills a run mid-way
(see #4). Files >100MB are unstaged with `WARNING: Unstaging large file` (S10) — if a *cache archive* shows up
there it is being **dropped** from the PR (GitHub's 100MB limit); that is data loss for that step's cache.

## 8. generate_anvm validate_crif failures

**Status: environmental/data.** `generate_anvm ... -validate_crif True` fails when an AL row references a
register/field that is absent from (or inconsistent with) the CRIF of the step being built. This is the real
signal the nightly exists to catch: a bad AL/CRIF combination in a stepping. Fix belongs in the config repo
(AL, mini_crif, CRIF), not in the pipeline. The failing command, `Current symlinks:` and the traceback in the
log identify the step and image group. Use the `al-intake` skill's `validate_auto_load.py` workflow to
reproduce locally. A retry with `-caching_f True` will not help here.

## 9. CPS serialization / agent flapping

**Status: FIXED for PR JSON** (`@NonCPS` `parsePrResponse`/`parsePrListResponse` return plain Maps).
Any `java.io.NotSerializableException` (S12) or bursts of `seems to be removed or offline ... cannot find
current thread in CpsStepContext` (S13) point to a non-serializable object (`LazyMap`, `Matcher`, closures
holding them) living in a local variable on the CPS call stack across an `sh` step. Notably the Send-Email
stage uses `result =~ /regex/` and indexes `matcher[0][1]` inside a closure — fine today because the matcher is
used immediately, but do not store `Matcher` objects in variables that outlive the statement. See also the
`nvmgeneration-pipeline` skill's known-issues #1 for the identical LazyMap incident.

## 10. MEV/MMG caching output is collected *before* `generate_anvm` runs

**Status: OPEN/SUSPECTED (ordering).**

Stage order is `Collect MMG/MEV changes` → `Process CNIC` → `Process MMG800`. `generate_anvm` (which consumes the
MEV and MMG config repos via `-c`/`-cm` and may write caching artifacts into them) only runs in the MMG800 stage,
i.e. **after** `processSharedConfigRepo()` already ran. Unless the generator wrote cache files into MEV/MMG
earlier, new MEV/MMG caches produced by this run are never committed, and `env.mev_result`/`env.mmg_result`
will say "no updates" every night. Verify by checking whether a MEV/MMG PR has *ever* been produced, and whether
`git status` in those repos is non-empty after the MMG800 stage; if caches are written there, move the collect
stage after `Process MMG800 Config`.

## 11. CNIC hifmc ALs go only into `latest`

**Status: OPEN/SUSPECTED (same class as #1).**

"Copying files" places CNIC hifmc output under `NSC/CNIC_800_*_A0/latest/FLASH_s/ALs/HIF[/row|/fnic]` — `latest`
as it stood on `CNIC_BRANCH` *before* `processCnicConfig()` repoints it per index. There is no equivalent of the
MMG800 per-step copy nor of `verifyRequiredAlFiles()` for CNIC. If a CNIC `generate_super_image` fails with
`File not exists ... ALs/HIF/...` for a step other than the original `latest` target, apply the #1 fix pattern
(copy into every step dir that is going to run + pre-flight verify).

## 12. Failure flags / email wording quirks

**Status: OPEN (cosmetic but misleading).**

- The email uses `mmg800Failed` for the **MEV and MMG** items too, so MMG800 failures label unrelated repos
  "DON'T MERGE"/"Run FAILED".
- The footer warnings (skipping `CNIC_A0`, skipping `vp23`, force-running `R28_R29_hybrid`) are hard-coded
  strings; they do not follow `perFolderSkips`/`perFolderForceRun` when those change — keep them in sync manually.
- `getStepsToSkip` reads the current target via `readlink <folder>/<first symlink>`, so for CNIC folders it
  only considers `latest` (not `fw_latest`/`sw_latest`), while history is collected over all symlinks.
- `perFolderSkips` must never contain the step `latest` currently points at (comment in code): the list is applied
  *after* the "current target always runs" rule and silently overrides it, leaving the live config unvalidated.
- Step selection is positional: index *i* takes folder step *i* or the **last** step when a folder has fewer. Two
  folders therefore rebuild their last step repeatedly (wasteful, not wrong) and a skipped/forced step changes
  which steps pair up.

## 13. Fixed history (don't regress)

| Commit | Fix |
|---|---|
| `aba762df9` | `return` inside `dir{}` was a no-op → PR/push of `cachingFinal` branch even when nothing staged or tree unchanged. Now uses `hasUpdates` flag. |
| `bd456fd3e` | Step skip lists updated; hifmc ALs copied into all steps that will run; `generate_anvm` self-healing `-caching_f True` retry. |
| `a7c90ee30`, `36b8ccc01` | No empty PRs; only cache artifacts committed; trimmed commit metadata. |
| `b46ea05b5`, `d801bba97` | "DON'T MERGE"/"Run FAILED" mail warnings. |
| `4e7bba8ba` | Branch parameters for testing; force-run `R28_R29_hybrid`; mail warning. |
| `0cdda6b39` | Branch/PR naming aligned per project (CNIC/MMG800). |
| `b1c6254bd` | Initial nightly caching for all steps. |

## 14. Unpacked caches from unrelated steps enter per-index commits

**Status: CONFIRMED in the 2026-11-01 unsquashed debug run.**

**Symptom:** an index whose `latest` links select hybrid steps also adds thousands
of files under older steps' `caching_files/` and `caching_files_bid/`.

**Root cause:** generator archive extraction scans whole config roots, not only
the current `latest` target. Compression leaves extracted directories available
for other processes; per-index `git add -A` stages them. Each branch starts from
the base branch, so the same unpacked files can appear in multiple index commits.
The unsquashed debug mode exposes these artifacts directly instead of filtering
them at final consolidation.

**Evidence:** MMG800 commit `1281e920a` adds 1,216 paths under R25 and 1,433 each
under R27 and R27_R28_hybrid, all within unpacked cache directories; none of those
steps' archives changes. A PKB `0.csv` sample from each matches both its parent's
archive member and index-0 commit `b28d0cae`. Do not interpret these additions as
proof that the older steps were rebuilt.

**Fix direction:** restrict per-index staging to intended cache artifacts,
excluding unpacked cache directories and image/output leftovers. Consolidate
root hash registries separately; do not silently skip conflicting commits.
*(Partially addressed via #5: `generate_output`/`cnic_boot_image_output`/root
`image_*` leftovers are no longer committed, and conflicting cherry-picks are
now logged with their conflict hunks and flagged as FAILURE instead of being
skipped silently. The unpacked `caching_files*/` staging itself is still OPEN.)*

## 15. Parallel branch circles show WHITE (no status) in the Jenkins graph views

**Status: FIXED — `stagedParallel()` wrapper (working tree after `34b7b3c24`).**

**Symptom:** in Blue Ocean / Pipeline Graph View, the parallel branch circles
(`Branch: generate_Veloce_FS_s`, `generate_anvm_PHASE2_*`, `super_image_*`, the
clone branches) are neither green nor red — they stay white/grey — even in a run
that ends `Finished: FAILURE` with real `generate`/`generate_anvm` failures
(e.g. the 2026-11-02 run on `dev/sleoarx/fix` @ `34b7b3c24`, MMG800 PR #474).

**Root cause:** the scripted `parallel` branches were bare closures with **no
`stage` step inside**. A branch without an enclosing stage has no stage node to
carry a SUCCESS/FAILURE status, so graph views render it as a "no status" white
circle. Additionally, `catchError(buildResult: 'FAILURE', stageResult:
'FAILURE')` **silently ignores `stageResult` when there is no enclosing stage**
— so even genuinely failed branches had nothing to paint red (the build-level
result still went red via `buildResult`). Commit `c73c435d6` (cherry-pick
conflict handling + email reminder) did not touch this, which is why it did not
fix the circles.

**Fix:** the `stagedParallel(Map branchBodies, String stagePrefix = '')` helper
wraps every branch body in `stage("${stagePrefix}${name}")` before calling
`parallel`. Used at all three call sites: clone stage (no prefix), CNIC
(`"[CNIC idx ${i}] "`), MMG800 (`"[MMG800 idx ${i}] "`) — the per-index prefix
keeps stage names unique across the per-index loop. The `parallel` map keys are
unchanged, so the `Branch: <name>` log lines (relied on by
`diagnose_caching_log.py`'s timeline) are preserved.

**Check:** after the fix, each branch circle must be green on success and red
when its `catchError` catches a `error(...)`. If circles go white again, look
for a new `parallel` call site that bypasses `stagedParallel`.

## 16. Failed indices are excluded from the final PR (per-index isolation)

**Status: IMPLEMENTED (working tree on top of `7dec74e08`).**

**Behavior:** every index still runs and reports as before (red branch circles, `ERROR:` log lines, build
`FAILURE`, "Caching Pipeline FAILED" mail). If any step of index *N* fails (`generate`, `generate_anvm` incl. its
`-caching_f` retry, `generate_super_image`, the MMG800 HIF pre-flight, or an exception in the index), then:
- its commits are **not** added to `allCommits`, so they never reach `cachingFinal-*`;
- its step labels (`<folder>/<symlink>:<step>`, e.g. `NSC/CNIC_800_EMULATION_A0/latest:R51`) go to
  `env.<repo>_failed_steps` and the email shows `🔴👮‍♀️ERROR in these steps: [...]`;
- its `caching-<date>-N` branch is kept on GitHub (listed in the email) instead of deleted;
- the PR from the remaining good indices is mailed **without** a DON'T MERGE warning.
Example: MMG800 with 3 indices, #3 fails → PR combines #1+#2 (clean line), plus an ERROR line for #3's steps.
If all indices fail → "no PR needed" + the ERROR line.

**Log markers:** `ERROR: <REPO> index N had failed steps (...) - its K commit(s) on caching-<date>-N are EXCLUDED`,
or `ERROR: <REPO> index N commits (if any) are EXCLUDED` for the exception path.
**Caveats:** `env.<repo>_failed` is now set only by a skipped cherry-pick (→ DON'T MERGE). A folder with fewer steps
repeats its last step, so that step's label can appear under a failed index even if the other folder caused it.
MEV/MMG email items still take the MMG800 PR-level flag (#12 quirk unchanged).

## 17. CNIC is run-only (no branches, commits or PR)

**Status: IMPLEMENTED (working tree).**

**Behavior:** `processCnicConfig()` only flips symlinks in the working tree and runs the 4x `generate_super_image`
per index, to detect errors in upcoming steps. It creates no `caching-<date>-N` / `cachingFinal-CNIC-*` branches,
no commits, pushes, cherry-picks or PR, and deletes nothing on GitHub. Cache artifacts it produces are discarded
by `cleanWs`. The tracked tree is not reset between indices (HIF ALs copied into `latest` must survive), so cache
files from one index remain visible to the next.
- Mail line: `🟢 CNIC succeeded in the run` (no failed index) or `🔴 CNIC failed in the run`, printed verbatim.
  Failed indices additionally appear in the `🔴👮‍♀️ERROR in these steps` line (no branch note). Build is FAILURE on any failure.
- **Log markers:** `ERROR: CNIC index N had failed steps (...)` and `ERROR: CNIC iteration index N failed: ...`; no
  `EXCLUDED`, `PR created`, `Content identical` or `has caching updates` lines for CNIC. `CNIC Result: <line>` is still echoed.
- **Consequences to remember:** no symlink commits means the history-based skip (`getStepsToSkip`) never learns which
  steps CNIC ran, so the step list only changes when someone else merges symlink changes into `CNIC_BRANCH`; cache
  fixes for CNIC must be done manually (the pipeline no longer proposes them); #5 (cherry-pick conflicts) and the
  CNIC part of #16 (exclusion/kept branches) no longer apply to CNIC.
