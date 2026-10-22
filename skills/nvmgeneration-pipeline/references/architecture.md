# NVMGeneration.groovy — Pipeline Architecture Deep-Dive

Full-depth reference for
[NVMGeneration.groovy](/home/sleonarx/ci-general/SDK/NVMGeneration.groovy) (in the
`firmware.ethernet.validation.tools.ci-general` repo, path `SDK/NVMGeneration.groovy`). Read
[known-issues.md](./known-issues.md) alongside this for bug history — this file is "how it's
built", that one is "what's already been found broken in it and fixed."

## 1. Purpose

A Jenkins declarative pipeline that regenerates NVM autoload images/headers across **4 product
lines** (MEV, MMG, MMG800, CNIC/LWC) from their respective `*-nvm-generator-config` repos, using
the shared `firmware.ethernet.mountevans.snic-nvm-generator` tool. It's triggered per-PR (any PR
in any of the config repos, or the generator repo itself) to validate that the PR's changes don't
break image generation anywhere, and — if generation produces new/changed derived artifacts
(caching files, hash maps, init-module JSONs) — it **commits those changes back** into the
relevant config repo(s) and opens/updates a PR for them.

## 2. Trigger parameters

```groovy
parameters {
    string(name: 'SCM_BRANCH', defaultValue: 'SyDK_tools')   // branch the groovy script itself is loaded from
    string(name: 'BRANCH',     defaultValue: 'main')         // branch to check out in every config repo
    string(name: 'REPO_NAME',  defaultValue: '')             // repo of the PR that triggered this build
    string(name: 'PR_ID',      defaultValue: '')             // PR number that triggered this build
}
```

`REPO_NAME`/`PR_ID` identify **the one PR that caused Jenkins to schedule this build** (e.g. via a
GitHub webhook). `BRANCH` is the actual mechanism tying multiple repos' PRs together into one
logical unit of work — see "The Bundle" below.

## 3. Repos cloned (all in parallel, in `CLONING_REPOS` stage)

| Constant | Repo | Branch resolution |
|---|---|---|
| `NVM_GENERATOR_REPO` | `firmware.ethernet.mountevans.snic-nvm-generator` | `check_branch()` → `nvm_gen_branch` |
| `UTILS_REPO` | `firmware.ethernet.mountevans.nvm-team-utils` | `params.UTILS_REPO_BRANCH` — **not declared in `parameters{}`**, see note below |
| `MEV_CONFIG_REPO` | `firmware.ethernet.mountevans.snic-nvm-generator-config` | `check_branch()` → `mev_branch` |
| `MMG_CONFIG_REPO` | `firmware.ethernet.mountmorgan.snic-nvm-generator-config` | `check_branch()` → `mmg_branch` |
| `MMG800_CONFIG_REPO` | `firmware.ethernet.mountmorgan800.snic-nvm-generator-config` | `check_branch()` → `mmg800_branch` |
| `SDK_TOOLS_REPO` | `firmware.ethernet.mountmorgan.sdk-tools` | fixed `SDK_TOOLS_REPO_BRANCH` ('main') — contains `scripts/update_pr.py`, see §6 |
| `CNIC_CONFIG_REPO` | `firmware.ethernet.cnic.nvm-generator-config` | `check_branch()` → `cnic_branch` |
| `HIFMC_REPO` | `firmware.ethernet.mountevans.imc.hif-shared` | fixed `"mmg_trunk"` — cloned sequentially with `HIFMC_NSC_REPO` (`firmware.ethernet.nsc.hifmc-nsc-common.fw`, fixed `"main"`) into the same `hifmc_path`, since both must land in the same directory |
| `DDR_REPO` | `firmware.ethernet.mountmorgan.ts.ddr` | fixed |
| `ROW_REPO` | `firmware.ethernet.imc-fw-row` | fixed |

`check_branch(repo_name)` (see [known-issues.md §3](./known-issues.md#3-silent-bundle-repo-exclusion-on-transient-branch-lookup-failure))
does `git ls-remote --heads <repo> refs/heads/<params.BRANCH>` and returns `params.BRANCH` if it
exists there, else `DEFAULT_REPO_BRANCH` ('main'). This per-repo branch variable is what later
decides both (a) whether `repoCommitWrapper()` is allowed to commit to that repo this run, and (b)
whether that repo is eligible for "the bundle."

> **Observed oddity (not yet fixed, flagged for awareness):** the `Utils` clone passes
> `branch=params.UTILS_REPO_BRANCH`, but no parameter named `UTILS_REPO_BRANCH` is declared
> anywhere in the `parameters {}` block — so `params.UTILS_REPO_BRANCH` is always `null` at
> runtime. Since `clone_repo()` only appends `-b <branch>` when `branch` is truthy, this silently
> falls back to cloning the repo's default branch, not the `@Field public static final String
> UTILS_REPO_BRANCH = 'main'` constant that sits right above it (which is otherwise unused/dead).
> Almost certainly benign today (the repo's default branch is presumably `main` too), but it's a
> landmine if that default ever changes, or if someone "fixes" it by wiring the constant in and
> accidentally changes behavior for everyone. Worth a cleanup pass, out of scope for this skill's
> checker-status work.

## 4. The Bundle concept

**A "bundle" is the set of PRs — one per repo — that all share the exact same branch name
(`params.BRANCH`).** The idea: a developer working across multiple repos (e.g. an mmg AL change
that also needs a matching mmg800 AL change) pushes both to branches with the *same name* in each
repo, opens a PR in each, and this pipeline treats all of them as one logical change under test —
so that **every PR in the set gets the same pass/fail checker status**, not just the one that
happened to trigger the build.

`getBundlePullRequests()`:
1. Always includes `[repo: params.REPO_NAME, prId: params.PR_ID]` (the triggering PR).
2. For each of `NVM_GENERATOR_REPO`, `MEV_CONFIG_REPO`, `MMG_CONFIG_REPO`, `MMG800_CONFIG_REPO`,
   `CNIC_CONFIG_REPO`: if that repo's branch variable (from `check_branch()`, captured during
   cloning) equals `params.BRANCH` (i.e. a same-named branch really exists there) **and** it's not
   the repo that already triggered the build, look up its open PR on that branch via
   `findOpenPullRequestByBranch()` and add it.
3. Deduplicates manually (not via `.unique(Closure)` — see the code comment: that pattern can
   silently corrupt data under Jenkins' CPS sandbox).

`findOpenPullRequestByBranch(repoName, branch)` calls `github().GithubGet(".../pulls", ...,
[state:'open', head:branch, per_page:100])` and filters client-side on `head.ref == branch`
(GitHub's own `head` filter is unreliable across forks/orgs). Throws if more than one open PR
matches (ambiguous).

**If `params.BRANCH == 'main'` (the default), there is no bundle** — only the triggering PR is
ever touched, since no non-default branch search happens.

## 5. Stage-by-stage flow

```
Checkout SCM                       - checks out this groovy script's own repo (not the config repos)
CLONING_REPOS                      - parallel: clones all 10 repos above, resolves *_branch vars
Set Username, Email                - getUserDetails() -> env.user / env.email (for PR comments/emails)
Mark Bundle PRs Pending            - updateBundleCheckerStatus('PENDING', false) - see known-issues.md #7
Extracting Request Parameters      - just echoes params.REPO_NAME / params.PR_ID (no-op otherwise)
Run hifmc script                   - hifmc_db.py for mmg/mmg800/lwc/bts/row/fnic topologies
Copying files                      - stages generated autoload CSVs into each config repo's AL dirs
Generating header files            - buildHeaderFileParallelJobs(): ~12 parallel flow_manager.py
                                      generate_header_files calls across MEV/MMG/MMG+MEV-chain/LWC/
                                      CNIC/MMG400_TS combinations (several permanently commented out
                                      as [KNOWN FAILURE] - see inline comments in the function)
Extracting all image names         - walks MEV config's networking.ipu.anvm/images tree
Running command on all images      - checkIfHashEnabled() gate; parallel flow_manager.py
                                      generate_anvm per image group across MEV+MMG+MMG800 combined
Running cnic image                 - checkIfHashEnabled() gate; parallel generate_super_image
                                      --boot-only per CNIC/MGVL boot image
Running lwc image                  - checkIfHashEnabled()/checkIfKeyEnabled() gated; LWC + MMG400_TS
                                      generate_super_image calls
Running non anvm images            - buildNonAnvmCommands()+buildMmgNonAnvmCommands()+
                                      buildMmg800NonAnvmCommands(): the "plain" (non-ANVM) FLASH_s/
                                      FS_s generate calls per stepping/type, for MEV/MMG/MMG800
Compare init module jsons          - runInitModuleJsonComparisons()/compareAndUpdateInitModuleJsons():
                                      diffs newly generated init_module_jsons against each config
                                      repo's existing copies (DeepDiff), copies in any that changed
Commit and create PR               - repoCommitWrapper() x5 (nvm_gen, mmg, mmg800, mev, cnic) -> if
                                      changed, createPRWrapper() -> createPR() -> notifyRepoPullRequest()
post { always }                    - updateBundleCheckerStatus(job_status, true) FIRST, then (in its
                                      own try/catch, non-fatal) a GitHub rate-limit diagnostic curl
        failure / success          - emailext() notifications (unrelated to checker status)
        cleanup                    - cleanWs()
```

`checkIfHashEnabled(repoPaths)` / `checkIfKeyEnabled(repoPaths, key)` read each repo's
`al_path_to_hash*.json` and gate whole stages on a `use_hash` (or named) boolean flag inside it —
these are config-repo-side kill switches, independent of anything in this groovy file.

## 6. Commit → PR → Checker-status pipeline (the part most relevant to "why didn't it go red")

```
repoCommitWrapper(repoName, branchName, file_changed, repoPath)
  │  gitStatus() -> status file; checks if any of file_changed[] substrings appear in git status
  │  if branchName == 'main': no-op (never commits to the default branch)
  │  gitAdd() (excludes Makefile + caching_files dirs from the generic add, re-adds anvm caching dirs explicitly)
  ├─ if nothing added: return false
  └─ if added:
        gitCommit(commitMessagePerRepo(repoName))   # per-repo canned commit message/type tag
        repoHeadShas[repoName] = `git rev-parse HEAD`   # <- captured for waitForPrHeadSha() later
        notifyRepoPullRequest(repoName, branchName, "please re-run gaters" warning)
        pushChanges(repoName, branchName)            # sets remote url + `git push origin <branch>`
        return true

createPRWrapper(REPO)                                  # called only if repoCommitWrapper() returned true
  └─ createPR(REPO, params.BRANCH, prTitle)
        - checks for an existing open PR via a hand-rolled curl (NOT findOpenPullRequestByBranch()
          - a separate, parallel implementation; see known-issues.md #1 for why this duplication
          once caused a problem)
        - if none found: git push + POST a new PR
        - returns the PR url either way; createPRWrapper() emails env.email with the result

post { always { script {
    job_status = (currentBuild.currentResult == 'SUCCESS') ? 'SUCCESS' : 'FAILURE'
    updateBundleCheckerStatus(job_status, true)       # <- THE final checker-status update
    ...non-fatal rate-limit diagnostic...
}}}

updateBundleCheckerStatus(status, failBuildOnError)
  │  bundleTargets = getBundlePullRequests()           # falls back to just the triggering PR if this throws
  └─ for each target:
        if repoHeadShas has this repo: waitForPrHeadSha(...)   # poll PR head sha before posting (known-issues.md #2)
        up to 3x: sh "python3 scripts/update_pr.py -r <repo> -a update_status -s <status> -p <prId> ..."
        (scripts/update_pr.py lives in SDK_TOOLS_REPO, cloned separately - its internals are OUTSIDE
        this file and out of scope for fixes here; it resolves the PR's head commit itself and posts
        a GitHub commit-status with context "NVMGeneration_pipeline")
```

**Key insight**: there are **two independent GitHub "does a PR exist" checks** in this file —
`createPR()`'s own hand-rolled `curl .../pulls?head=...` (used only to decide whether to create a
new PR vs. reuse one), and `findOpenPullRequestByBranch()` (used by `getBundlePullRequests()` and
`notifyRepoPullRequest()` to find which PR to comment on / include in the bundle). They can
disagree (see the real incident in [known-issues.md](./known-issues.md)) — a `404`/transient
failure in one doesn't mean the other will also fail.

## 7. Where `update_pr.py` actually lives

`SDK_TOOLS_REPO` (`firmware.ethernet.mountmorgan.sdk-tools`) is a **separate repo**, not available
in this workspace at the time this skill was written. Its `scripts/update_pr.py` is invoked as:
```bash
python3 scripts/update_pr.py -t <token> -r <repo> -a update_status -s <status> -p <prId> -c NVMGeneration_pipeline -e <email>
```
It is responsible for resolving `<prId>`'s current head commit and posting a GitHub commit status
(`context: NVMGeneration_pipeline`) against it. **Any fix to how it resolves the target commit must
happen in that repo** — this file can only work around timing issues from the outside (see
[known-issues.md §2](./known-issues.md#2-stale-commit-race-in-checker-status-updates)).

## 8. Key field/global state

```groovy
@Field private Map<String, String> repoHeadShas = [:]   // repoName -> sha just pushed this run
```
Populated only by `repoCommitWrapper()` on an actual commit; consumed by
`updateBundleCheckerStatus()`/`waitForPrHeadSha()`. Several other mutable pipeline-wide variables
(`mmg_branch`, `mmg800_branch`, `mev_branch`, `nvm_gen_branch`, `cnic_branch`,
`mmg_changed`/`mmg800_changed`/etc.) are declared in the `environment {}` block or assigned without
`def` inside `script {}` blocks — Jenkins' CPS sandbox tolerates this but logs a
`"Did you forget the def keyword?"` warning each time; harmless, just noisy in the console log.
