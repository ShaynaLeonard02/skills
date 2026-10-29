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

**Status: OPEN/SUSPECTED.**

The final branch does `git cherry-pick <sha> --allow-empty || git cherry-pick --skip` for every collected commit,
then `reset --soft`. Consequences to remember:
- A real conflict is **silently skipped** (`--skip`), so the PR can miss one index's cache with no error.
- Commits from different indices that touch the same `al_path_to_hash*.json` can conflict → same skip.
- If `cherry-pick --skip` itself fails (no cherry-pick in progress) the step errors and the repo/CNIC/MMG800
  processing stage dies after all generators already ran.
Look for `CONFLICT` / `could not apply` (S6) near `cachingFinal-` creation. Failed indices (`*_failed`) contribute
commits too — partial results are intentionally still PR'd, flagged "DON'T MERGE".

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
