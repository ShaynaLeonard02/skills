# CachingPipeline.groovy — Architecture

Source: [CachingPipeline.groovy](/home/sleonarx/ci-general/SDK/CachingPipeline.groovy)
(repo `firmware.ethernet.validation.tools.ci-general`, job runs on agent label `SyDK_pipline`,
Jenkinsfile branch param `SCM_BRANCH` default `SyDK_tools`). This describes behavior and intent —
the live file is authoritative; function names are stable, line numbers drift.

## 1. Purpose

The NVM Generator caches expensive per-AL work as `caching_files*.tar.gz` archives plus root-level
`al_path_to_hash*.json` hash maps inside each config repo. This nightly job **re-runs the generator
for every stepping that has not been cached yet**, then opens ONE PR per repo containing only the
refreshed cache artifacts. It never commits regenerated ALs / hifmc CSVs.

| Item | Value |
|---|---|
| Trigger | cron `0 1 * * *`, timeout 48h, logs kept 30 days |
| Params | `SCM_BRANCH`, `RUN_CNIC`, `RUN_MMG800`, `CNIC_BRANCH`, `MMG800_BRANCH` (default `main`) |
| Python | `/nfs/site/proj/mmg/SyDK/SDK-releases/sdk_venv/bin/python3` (`PYTHON`) |
| Proxy | `http_proxy`/`https_proxy` = `PROXY_URL` for all stages; curl calls pass `--proxy` |
| Git/GitHub auth | credential `shayna-leonard-github-pat` (a personal PAT — expiry/rotation breaks push+PR+delete) |
| Mail | `emailext` to `EMAIL_RECIPIENT`; failure mail with `attachLog` in `post{failure}` |
| Cleanup | `post{always{ cleanWs }}` — workspace is gone after the build; logs are the only evidence |

## 2. Repos cloned (stage "Clone Shared Repos", parallel, `cloneRepo()` = `rm -rf` + `git clone -b`)

| Constant | Repo | Branch | Role |
|---|---|---|---|
| `NVM_GENERATOR_REPO` | `...mountevans.snic-nvm-generator` | `main` | provides `NVMGenerator/flow_manager.py`; `nvm-team-utils` is copied over it |
| `MEV_CONFIG_REPO` | `...mountevans.snic-nvm-generator-config` | `main` | input to `generate_anvm` (`-c`, and `networking.ipu.anvm`) |
| `MMG_CONFIG_REPO` | `...mountmorgan.snic-nvm-generator-config` | `main` | input to `generate_anvm` (`-cm`); receives hifmc ALs |
| `MMG800_CONFIG_REPO` | `...mountmorgan800.snic-nvm-generator-config` | `MMG800_BRANCH` | **primary target**; cloned under `ws/mmg800_config/` |
| `CNIC_CONFIG_REPO` | `...cnic.nvm-generator-config` | `CNIC_BRANCH` | **primary target**; cloned under `ws/cnic_config/` |
| `HIFMC_REPO` | `...mountevans.imc.hif-shared` | `mmg_trunk` | `hifmc_db.py` for topology `mmg` |
| `HIFMC_NSC_REPO` | `...nsc.hifmc-nsc-common.fw` | `main` | cloned next to hifmc (not otherwise referenced) |
| `ROW_REPO` | `...imc-fw-row` | `main` | `hifmc_db.py` for `lwc`,`bts`,`row`,`fnic` |

Only MMG800 and CNIC get step branches and generator runs. MEV/MMG are inputs whose caching
output (if any) is picked up by `processSharedConfigRepo`.

## 3. Stage flow

1. **Initialize** — `generalScripts().CopyConfigurations`, `date_str = yyyy-MM-dd-HHmmss`.
2. **Clone Shared Repos** — see above, then `cp -r utils/* nvm_generator/`.
3. **Run hifmc script** — `hifmc_db.py --topology <t> headless --generate-autoloads --generate-headers --generate-memory-buffer || true`
   (`mmg` from HIFMC repo; `lwc,bts,row,fnic` from ROW repo). **Failures are swallowed** by `|| true`
   (the `status != 0` warning can never fire) — see known-issues #3.
4. **Copying files** — places the generated CSVs on disk only (they are *build artifacts, never committed*):
   - strip `_mmg` from `output_mmg/*_mmg*.csv`;
   - MMG repo: `MMG/{HW,SIMICS}/MMG_A0/FLASH_s/ALs/HIF` ← `output_mmg` + `output_lwc`;
   - MMG800: into **every** `MMG800/EMULATION/<step>/FLASH_s/ALs/HIF` (`copyGeneratedFiles` + `rename_mmg800_csv_files` strips `_mmg800`);
   - CNIC: into `NSC/CNIC_800_{EMULATION,SIMICS}_A0/latest/FLASH_s/ALs/HIF[/row|/fnic]` (**`latest` only**, see known-issues #11).
5. **Collect MMG/MEV changes** — `processSharedConfigRepo()` for MEV then MMG (see §6; ordering concern in known-issues #10).
6. **Process CNIC Config** (`when RUN_CNIC`) — `processCnicConfig()`.
7. **Process MMG800 Config** (`when RUN_MMG800`) — `processMmg800Config()`.
8. **Send Email** — one `<li>` per repo; `has caching updates` → PR link (+ "DON'T MERGE - RUN FAILED" if the flag is set);
   otherwise "no PR needed" (+ "Run FAILED" warning). Footer warnings are **hard-coded text** (CNIC_A0, vp23, R28_R29_hybrid).
9. **post** — `cleanWs`; on failure a second mail with the log attached.

## 4. The per-repo engine (CNIC and MMG800 share the same shape)

```
folders[]  = [{path, symlinks[]}]        # MMG800: EMULATION,SIMICS (latest)  CNIC: 4 NSC folders (latest[,fw_latest,sw_latest])
for each folder:
    stepList   = dirs in folder (sorted)
    skip       = stepsToAlwaysSkip (vp20.2.2.0) + perFolderSkips + getStepsToSkip(history)
    force      = perFolderForceRun (MMG800 only: EMULATION -> R28_R29_hybrid) bypasses ALL skips
    stepsPerFolder[path] = stepList - skip (+force)
maxSteps = max(len(stepsPerFolder[*]))
for i in 0..maxSteps-1:                       # one iteration = one branch "caching-<date>-<i+1>"
    try:
        checkout <branch>; checkout -b caching-<date>-<i+1>
        for each folder: target = steps[i] if i < len else steps.last ; repoint every symlink to target
        deterministic commit "[Caching]: Set symlinks for index i" (allow-empty); push
        [MMG800] verifyRequiredAlFiles(MMG800/SIMICS/latest/FLASH_s/ALs/HIF, MMG800_REQUIRED_HIF_ALS) -> error() if missing
        parallel:
            MMG800: 3x generate (Veloce/Simics/SILICON, stepping latest, FS_s)
                    3x generate_anvm (PHASE2_COMMON_SIMICS_VELOCE_MMG, PHASE2_LM, PHASE2_HMA) + self-healing -caching_f retry
            CNIC  : 4x generate_super_image (CNIC_BOOT_IMAGES, --boot-only)
        git add -A (minus NVMGenerator.log, hifmc *.csv under EMULATION/*/FLASH_s/ALs/HIF, files >100MB)
        deterministic commit "[Caching]: generate results ..." ; push
        allCommits += commits on branch (excluding "[Caching]: Set symlinks" commits)
    catch (Exception): log, currentBuild.result='FAILURE', env.<repo>_failed='true'   # loop continues
final: checkout <branch>; checkout -b cachingFinal-<REPO>-<date>; cherry-pick allCommits;
       reset --soft <branch>; rm generate_output|cnic_boot_image_output;
       unstage all; stage ONLY root al_path_to_hash*.json + non-root *.tar.gz;
       if nothing staged  -> abandon, "doesn't have any caching updates"
       commit (deterministic); if tree == <branch> tree -> abandon;  else push + createPR
delete every caching-<date>-N step branch from GitHub
```

Key properties:
- **Symlink flip is the mechanism.** `latest` is repointed per index so `generate -s latest` / `generate_anvm`
  builds that stepping. Anything placed under `latest/` *before* the flip lands in the **old** target.
- **Skip history** (`getHistoricalSymlinkTargets`): `git log <branch> -- <folder>/<symlink>`, `git show <commit>:<path>`
  for each commit, `sort -u`. A step that was *ever* a `latest` target on `<branch>` is considered cached and skipped —
  except the **current** target (`readlink`), which always runs. A brand-new step (never a target) always runs.
- **Deterministic commits** (fixed author/committer/date `2020-01-01`) → identical content gives identical SHAs, so
  `treesAreIdentical()` (compare `rev-parse <ref>^{tree}`) reliably suppresses no-op PRs.
- **Only cache artifacts reach the PR**: root `al_path_to_hash*.json` and `*.tar.gz` with depth ≥2. Everything else
  (incl. `generate_output`, hifmc CSVs, `NVMGenerator.log`) is discarded at squash time, even if it was committed on a step branch.
- **Failure model is "keep going, mark red"**: per-branch `catchError(buildResult:'FAILURE', stageResult:'FAILURE')`,
  per-index `try/catch`, and `env.cnic_failed` / `env.mmg800_failed`. A failed run can still open a PR (flagged
  "DON'T MERGE" in the mail) — **a red build with a PR is expected**.
- **generate_anvm self-healing**: first run uses the cache; on failure it re-runs once with `-caching_f True`
  (force rebuild) so the post-run `compress_caching_directories()` writes a complete archive that this stage commits.
  Only if the retry also fails is the branch marked failed. `-validate_crif True` is always on.

## 5. GitHub helpers

| Function | Notes |
|---|---|
| `pushBranch` | sets `origin` URL (no token embedded; uses stored credential binding) and `git push --force-with-lease origin <branch>` |
| `createPR` | `curl -u user:token --proxy` POST `/pulls`; on "A pull request already exists" lists open PRs for `head`; else `error("Failed to create PR ...")`. JSON parsed in `@NonCPS` helpers (`parsePrResponse`/`parsePrListResponse`) returning plain Maps to avoid `LazyMap` serialization crashes |
| `deleteBranch` | `curl -X DELETE .../git/refs/heads/<b>` — response is **not checked** |
| `getCommitsOnBranch` | `git log <base>..<branch> --oneline --no-merges | grep -v '[Caching]: Set symlinks'` |
| `createDeterministicCommit(msg, allowEmpty)` | message is interpolated inside single quotes — avoid `'` in messages |

## 6. `processSharedConfigRepo` (MEV, MMG)

`git status --porcelain` empty → "no updates". Otherwise branch `cachingFinal-<date>`, stage only
`al_path_to_hash*.json` (root) + `*.tar.gz` (depth ≥2), deterministic commit via `printf '<msg>' | git commit -F -`,
tree-compare vs `main`, push, PR `[Caching] All updates <date10>`. Note `return` inside `dir{}` is a no-op, so
decisions use the `hasUpdates` flag (fix commit `aba762df9`).

## 7. Output contract (strings other tooling/mail parse)

- `Repo: <repo> has caching updates: <url>` / `Repo: <repo> doesn't have any caching updates` — the mail regex
  `Repo: (.+?) doesn't have any caching updates` depends on this exact text.
- Branches: `caching-<date>-<N>` (temp, deleted), `cachingFinal-<date>` (MEV/MMG), `cachingFinal-CNIC-<date>`, `cachingFinal-MMG800-<date>`.
- PR titles: `[Caching] All updates <yyyy-MM-dd>`, `[Caching] CNIC All updates <date>`, `[Caching] MMG800 All updates <date>`.
- Commit subjects: `[Caching]: Set symlinks for index N`, `[Caching]: generate results for index N`,
  `[Caching]: generate_super_image results for index N`, `[Autoload MMG800 all]: Updates`, `[Autoload CNIC all]: Updates`.

## 8. Log markers (what the forensics script keys off)

`=== Processing <REPO> Config ===`, `<REPO> folder <path>: all steps=[..], historicalSkips=[..]`,
`<REPO> steps per folder (after skip logic): ...`, `<REPO> max steps: N`,
`[Caching] <REPO> index N symlink state:` (+ indented `path -> target` rows),
`[Copy:<label>] Copied into <dir> (<n> entries)`, `[Verify:<label>] ...`,
`ERROR: generate|generate_anvm|generate_super_image FAILED for ...`, `WARNING: generate_anvm FAILED ... (cached path)`,
`ERROR: <REPO> iteration index N failed: ...`, `Content identical to ... Skipping PR.`, `PR created:` / `Found existing PR:`,
`<REPO> Result: ...`, `WARNING: Unstaging large file ...`.
