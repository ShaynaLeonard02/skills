# NVMGeneration.groovy — Known Issues & Fixes Applied

Institutional memory for bugs already found and fixed in
[NVMGeneration.groovy](/home/sleonarx/ci-general/SDK/NVMGeneration.groovy). **Read this before
proposing a fix for a checker/PR-status/commit problem** — it may already be fixed, or the fix
you're about to suggest may be a partial re-implementation of one of these. If you find a *new*
issue of the same shape, add a new numbered entry here rather than letting it live only in chat
history.

All line/function references are to the function name (not line number, which drifts); search the
file for the `def <name>` to find current line numbers.

---

## 1. `JsonSlurper()` vs `JsonSlurperClassic()` — CPS serialization crash

**Status: FIXED** (in `createPR()`, both call sites).

`new JsonSlurper().parseText(...)` returns a `groovy.json.internal.LazyMap`, which is **not
`Serializable`**. Jenkins' CPS (Groovy continuation-passing-style) engine periodically
checkpoints/persists the entire pipeline execution state to disk (e.g. before/after every `sh`
step). If a `LazyMap` is sitting anywhere in a local variable reachable from the running call
stack when that checkpoint happens, the persist throws `java.io.NotSerializableException:
groovy.json.internal.LazyMap` and the whole CPS thread becomes unstable.

**Symptom signature** (recognize this pattern in a console log):
- A burst of `"<agent> seems to be removed or offline (java.io.IOException: cannot find current
  thread in CpsStepContext[...])"` / `"<agent> is back online"` pairs flapping repeatedly over a
  short window (seconds), where **none occurred anywhere earlier in the same build**.
- The flapping starts at or within a few lines of a `createPR()` call path — specifically right
  after its `git push origin <branch>` (the PR-existence-check `curl` response was parsed with the
  buggy `JsonSlurper()` just before the push).
- The build eventually dies for good (often at "End of Pipeline") with a full stack trace ending
  in `Caused: java.io.NotSerializableException: groovy.json.internal.LazyMap`, frequently anchored
  at `@WorkflowScript.createPR(WorkflowScript:<line>)` in the trace.
- **It is a race, not a guaranteed failure**: if every CPS checkpoint during the flapping window
  happens to succeed before the one holding the bad reference, the build can still limp to
  `Finished: FAILURE`/`SUCCESS` with both (or all) `update_pr.py` calls having gone through — this
  is exactly what happened in the build #4523 log analyzed to find this bug (see §4 real incident
  below). Don't assume "the build finished and posted statuses" means this bug isn't present.

**Fix**: use `new JsonSlurperClassic()` (already imported via `import groovy.json.JsonSlurperClassic`
at top of file) everywhere a GitHub API JSON response is parsed inside pipeline-script code.
`JsonSlurperClassic` returns plain `LinkedHashMap`/`ArrayList` — ordinary, `Serializable` JDK
collections. Both occurrences in `createPR()` (the PR-existence check and the PR-creation
response) were switched, and the new `waitForPrHeadSha()` helper (added for issue §2) uses
`JsonSlurperClassic` from the start.

**If you see `new JsonSlurper()` anywhere else in this file** (new code, a future contribution),
flag it — it's the same bug waiting to happen.

## 2. Stale-commit race in checker status updates

**Status: MITIGATED, not fully eliminated** (root cause is outside this repo).

`scripts/update_pr.py` (lives in the separate `firmware.ethernet.mountmorgan.sdk-tools` repo, not
in `ci-general`) resolves a PR's current head commit itself when posting a GitHub commit status.
If this pipeline just pushed a new commit to that PR's branch moments earlier, GitHub's API can
have propagation lag — `update_pr.py` may resolve and post the status against the commit that was
HEAD *before* this run's push, not the one this run just produced. The PR's UI then shows no
checker update at all on its actual current head (the status exists, just on an orphaned/stale
commit), which looks indistinguishable from "the pipeline forgot about this PR."

**Fix (mitigation)**: `repoCommitWrapper()` now captures the exact SHA it just committed
(`git rev-parse HEAD` right after `gitCommit()`) into the `repoHeadShas` map (repo name → sha).
`updateBundleCheckerStatus()` calls `waitForPrHeadSha()` before invoking `update_pr.py` for any
repo present in that map: it polls the PR's `head.sha` via the GitHub API (up to 6 attempts, 5s
apart — ~30s total) and only proceeds once it matches, logging a warning and proceeding anyway if
it times out (**never fails the build for this** — best-effort only).

**What would fully close this**: fixing `update_pr.py` itself to accept and use an explicit target
SHA instead of re-resolving the PR's head — out of scope for this file since that script lives in
a different repo. If you get access to `sdk-tools`, that's the real fix.

## 3. Silent bundle-repo exclusion on transient branch-lookup failure

**Status: FIXED** (`check_branch()`).

`check_branch()` determines whether `params.BRANCH` exists in each repo (to decide whether that
repo participates in "the bundle" — see `references/architecture.md` for what a bundle is). The
original implementation was:
```groovy
sh(script: "git ls-remote --heads ${repo_url} refs/heads/${params.BRANCH} | wc -l", returnStdout: true)
```
`wc -l` **always exits 0** regardless of whether `git ls-remote` upstream failed (network blip,
auth hiccup, etc.) — so a transient failure produces `count == 0`, which is silently
indistinguishable from "branch genuinely doesn't exist." The repo then falls back to
`DEFAULT_REPO_BRANCH` ('main'), is excluded from `getBundlePullRequests()`, and **its PR's checker
is never touched again this run — not updated to FAILURE, not updated to SUCCESS, nothing.**

**Fix**: added `retry(3)`, explicit `set -o pipefail` (via an inline `#!/bin/bash` script) so the
real exit code of `git ls-remote` propagates through the pipe, wrapped in try/catch. On persistent
failure after retries, it now logs a loud `WARNING` and **optimistically returns `params.BRANCH`**
(assumes the branch might exist) rather than silently assuming it doesn't — erring toward
"over-included in the bundle" rather than "silently dropped."

## 4. `addPRMessage()` always commented on the triggering PR, not the repo that changed

**Status: FIXED** (new `notifyRepoPullRequest()`, used by `repoCommitWrapper()`).

The "⚠️ A commit was made... please re-run all gater pipelines" warning comment was posted via
`addPRMessage()`, which unconditionally targets `params.REPO_NAME`/`params.PR_ID` — i.e. whichever
PR triggered this build — regardless of which repo `repoCommitWrapper()` actually just committed
to. Concretely: if the pipeline was triggered by an **mmg** PR, but only **mmg800** had real
autoload changes this run, the warning about the mmg800 commit landed as a comment on the **mmg**
PR, not mmg800's own PR. This reinforced a false "only mmg was touched" impression even when
mmg800 was the repo that actually changed.

**Fix**: `notifyRepoPullRequest(repoName, branchName, msg)` resolves the actual open PR for
`repoName`/`branchName` (via `findOpenPullRequestByBranch()`) and comments there, falling back to
`addPRMessage()` (the triggering PR) only if no open PR can be resolved for that repo. Wired into
`repoCommitWrapper()` in place of the old direct `addPRMessage()` call.

## 5. `post { always {} }` — single point of failure ahead of the checker-status loop

**Status: FIXED**.

Originally, the GitHub API rate-limit diagnostic `curl` ran *inside the same `withCredentials`
block, before* the loop that updates every bundle PR's checker status. Any exception there (or
anywhere before the loop starts) meant **the entire bundle's checker-status update never ran at
all** — not just for one repo, for every repo in the bundle, on every single build regardless of
whether the pipeline itself passed or failed.

**Fix**: reordered so `updateBundleCheckerStatus(job_status, true)` runs **first**, standalone; the
GitHub rate-limit check now runs afterward in its own `try/catch` that only logs a warning on
failure and can never block or fail the checker update.

## 6. Non-`SUCCESS` results weren't normalized before being reported as a checker status

**Status: FIXED**.

`job_status` was previously `currentBuild.currentResult` passed straight through — which can be
`UNSTABLE`, `ABORTED`, or `NOT_BUILT`, not just `SUCCESS`/`FAILURE`. It was never verified that
`update_pr.py`/GitHub's status API treats those as "red"; if not, an unstable/aborted run could
post a status that doesn't render as failing on the PR.

**Fix**: `job_status = (currentBuild.currentResult == 'SUCCESS') ? 'SUCCESS' : 'FAILURE'` — any
non-`SUCCESS` result is now explicitly normalized to `FAILURE` before being reported.

## 7. Catastrophic failure before `post {}` ever executes

**Status: MITIGATED, not solvable from pipeline code.**

If the Jenkins controller or the build agent dies completely (hard kill, OOM, host crash, network
partition that never reconnects) before `post { always {} }` gets a chance to run at all, **no
code running inside that build can retroactively post anything** — there's nothing left executing.
A PR's checker is left showing whatever its *previous* status was (which could be a stale green
from an earlier, unrelated run), silently implying "this PR is fine" when the real answer is
"nobody knows, the pipeline never finished."

**Mitigation**: a new early stage, **"Mark Bundle PRs Pending"**, runs right after `CLONING_REPOS`
(as soon as branches are resolved) and posts a `PENDING` GitHub status on every bundle PR via
`updateBundleCheckerStatus('PENDING', false)` — the `false` means failures here are only logged as
warnings, **never fail the build** (a diagnostic/best-effort stage must never become a reason the
real work doesn't run). If the build then dies catastrophically, the PR is left on `PENDING`
instead of a stale prior result — not perfect (GitHub won't flag a stuck pending as red on its
own), but strictly more honest than leaving old state untouched.

**This is the one category of failure genuinely outside any pipeline code's control.** If asked
"why didn't the checker go red when X crashed," and the evidence shows `post{}` never ran at all
(no `post{always{}}`-attributable log lines, log simply stops), this is the answer — not a bug to
keep chasing in `NVMGeneration.groovy` itself.

---

## Real incident this was diagnosed from (build #4523)

A bundle of 2 PRs: **mmg** PR #2265 (no new AL changes this run — `mmg config changed status
false`) + **mmg800** PR #427 (real AL changes — new commit, `mmg800 config changed status true`).
Expectation: since mmg800 changed and the run ended `Finished: FAILURE`, both PRs' checkers should
show red.

What the log showed:
1. `repoCommitWrapper()` for mmg800 committed and pushed a new commit (`382ed06`) to
   `dev/sleonarx/cosq-testing`.
2. `createPRWrapper()` → `createPR()` ran for mmg800. Its PR-existence lookup (hand-rolled `curl
   .../pulls?head=...`) got a literal GitHub `404 Not Found` for a branch/repo that demonstrably
   existed (the separate `findOpenPullRequestByBranch()` found it fine, `200`, moments later) —
   sending `createPR()` down the "no existing PR, create a new one" path, which `git push`es again
   and POSTs a new PR.
3. **Exactly at that second push**, the buggy `JsonSlurper()`/`LazyMap` object (parsed moments
   earlier from the 404 response) triggered the first-ever CPS "agent seems offline" flap of the
   entire build — see issue §1. The CPS thread flickered online/offline 14 times over ~23 seconds.
4. Both bundle PRs' `update_pr.py -s FAILURE` calls happened to squeeze through successfully during
   recovery windows in that flapping — `Finished: FAILURE` was reached — but the build then died
   permanently at "End of Pipeline" with the full `NotSerializableException` stack trace.
5. Separately, mmg800's `FAILURE` status update likely still landed on a stale commit (the
   `update_pr.py` call resolved the PR's head without any wait — see issue §2, added afterward) —
   plausibly explaining why the mmg800 PR's checker didn't visibly turn red even though the script
   reported success, while mmg's PR (unchanged this run, so no race) correctly showed red.

This is the log that [diagnose_pipeline_log.py](../scripts/diagnose_pipeline_log.py) was built to
replay-diagnose automatically — run it against a similar log and compare its "Jenkins CPS agent
stability" and "Exceptions / serialization errors" sections to this writeup.
