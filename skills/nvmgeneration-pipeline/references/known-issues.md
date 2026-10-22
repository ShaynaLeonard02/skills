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

## 8. `createPR()`/`waitForPrHeadSha()` use a different, apparently-unauthorized GitHub credential than the rest of the file — spurious 404s, no CPS crash required

**Status: FIXED** (observed in build #4532, 2026-10-22, mmg PR #2265 + mmg800 PR #427 bundle).

Every other GitHub call in this file (`GithubGet()`/`GithubPost()` in `ci_groovy/github.groovy`,
used by `findOpenPullRequestByBranch()`, `notifyRepoPullRequest()`'s comment-posting, etc.)
authenticates with `-H "Authorization: token $GITHUB_TOKEN"` bound from
`CREDENTIALS_ID_GITHUB_TOKEN_MAIN` (= `CREDENTIALS_ID_GITHUB_SYS_SYSFW2`, the `sys_sysfw2_gh`
service-account credential). But `createPR()`'s two hand-rolled curls (the existing-PR lookup and
the PR-creation POST) and `waitForPrHeadSha()`'s head-sha poll all instead use `-u
"${user}:${token}"` HTTP Basic Auth bound from `SYS_SYSFW_CREDENTIALS_ID` — a constant that is
**not declared anywhere in `NVMGeneration.groovy` itself** (only in sibling pipeline files
`RegistersBATPipeline.groovy`/`create_pr_for_header_files.groovy`/`anvm_system_test.groovy`, where
it equals `c7087dce-6b6e-431a-802a-0afdd037e766` = `CREDENTIALS_ID_GITHUB_SYS_SYSFW`, the **older**
`sys_sysfw` account) — so it must resolve from a shared-library/global binding outside this repo.
`updateBundleCheckerStatus()` also hands this same `SYS_SYSFW_CREDENTIALS_ID`-bound token to
`update_pr.py -t $token`.

**Symptom signature observed:**
- `createPR()`'s existing-PR-lookup curl (`-u user:token`) returns a literal GitHub `{"message":
  "Not Found","status":"404"}` for a PR that demonstrably exists and is open — confirmed in the
  very same build, within the same minute, by `findOpenPullRequestByBranch()` (`-H "Authorization:
  token"` auth) returning `200` for the identical repo, AND by a *successful* (`201`) PR comment
  post moments earlier via the same `GithubPost()`/token-header path.
- `createPR()` then concludes "No existing PR found, creating a new one", re-pushes (no-op,
  already up to date), and POSTs to `/pulls` — which **also** 404s with the same Basic-Auth style.
  The unhandled 404 triggers the pipeline's own `error "Failed to create PR...."` step, which is
  what directly sets `currentBuild.currentResult = FAILURE` for the whole build in this incident —
  no CPS flapping, no `JsonSlurper`/`LazyMap` crash (issue §1) needed to explain the failure this
  time; 0 CPS offline events, 0 exceptions anywhere in the log.
- Because this `error` is thrown from inside the `repoCommitWrapper()`/`createPRWrapper()` call for
  one repo in the middle of the "Commit and create PR" stage's sequential `repoCommitWrapper()`
  calls (nvm_gen → mmg → mmg800 → mev → cnic), and nothing in that stage catches it, **every repo
  later in the sequence than the one that hit the 404 is silently never processed this run** — no
  "Running in .../<repo>_config/..." directory trace, no "`<repo> changed status`" log line for
  any of them at all. In this incident, mmg800/mev/cnic's `repoCommitWrapper()` never ran, so
  mmg800's real autoload changes (if any existed) for this run were never evaluated, committed, or
  pushed — mmg800's PR only got touched by `post{always{}}`'s final bundle-wide
  `updateBundleCheckerStatus()` call, using whatever its head already was before this build.
- `waitForPrHeadSha()` (same Basic-Auth credential) polls `GET /pulls/<prId>` for the repo that
  *did* commit (mmg here) and reports the head sha as **`null` on every one of its 6 attempts**
  over ~40s, then gives up with a warning and proceeds anyway. This is consistent with that GET
  also silently 404ing (`{"message":"Not Found",...}` has no `head` key, so `json?.head?.sha`
  safe-navigates to `null` — this is NOT a parsing bug in `waitForPrHeadSha()` itself, the
  `json?.head?.sha` access is correct; the response body is just the same auth-rejected 404).
- `update_pr.py` (handed the same `SYS_SYSFW_CREDENTIALS_ID`-bound token) then posts the final
  `FAILURE` status, but its printed API response resolves to a commit **from a day earlier**
  ("Update README.md", authored the day before this run) — and critically, **the exact same stale
  commit** was also what it resolved hours earlier for the initial `PENDING` status post (before
  this run had pushed anything). I.e. `update_pr.py`, using this credential, never once saw this
  PR's true current head in this build, before or after the push — consistent with this credential
  having degraded/stale/no real read access to this repo's live PR state, not just propagation lag
  (contrast with issue §2, which assumes eventual consistency).

**Net effect on the two bundle PRs' checkers in this incident:**
- **mmg800** (no commit this run, `repoHeadShas` has no entry for it, `waitForPrHeadSha()` is
  skipped) → `update_pr.py -s FAILURE` resolves its real, unchanged current head correctly → PR
  shows **red**, as expected.
- **mmg** (did commit `bc3f248` this run) → `waitForPrHeadSha()` can't confirm the new head (always
  null) → `update_pr.py` posts `FAILURE` against an unrelated day-old commit, never against
  `bc3f248` → GitHub has **no status at all** on mmg's actual current head this run → a required
  check with no status renders as pending/**yellow** ("Expected"), not red, even though the build
  genuinely failed and even though an early `PENDING` was posted (also against a stale, pre-this-
  run commit) long before the real push happened.

**Hypothesis, not yet confirmed**: the `sys_sysfw` account/token bound to `SYS_SYSFW_CREDENTIALS_ID`
either lacks (or has lost) read/write access to this repo, or Basic Auth itself is being rejected
by this GHE instance for API calls — GitHub (and GHE) return `404` rather than `401`/`403` for
unauthenticated/under-authorized access to a private repo's endpoints, specifically to avoid
leaking repo existence to callers without access. This would explain every symptom above
uniformly. **Not yet verified directly against the credential store** — treat as the leading
explanation, not a confirmed fact, until someone can check the `SYS_SYSFW_CREDENTIALS_ID` Jenkins
credential's current validity/scope against this repo.

**Fix applied**: switched `createPR()`'s two curls and `waitForPrHeadSha()` to the same
`CREDENTIALS_ID_GITHUB_TOKEN_MAIN`/`-H "Authorization: token ${token}"` pattern already used (and
known-working) everywhere else in this file, in place of the stale `SYS_SYSFW_CREDENTIALS_ID`/
HTTP-Basic-Auth path (`waitForPrHeadSha()`'s now-unused `ghUser` parameter was dropped).
`updateBundleCheckerStatus()`'s own `withCredentials` binding (which also supplies the `-t $token`
passed to `update_pr.py`) was switched the same way. Separately, each
`repoCommitWrapper()`/`createPRWrapper()` call in the "Commit and create PR" stage's per-repo
sequence is now wrapped in its own `try/catch` — failures are logged and collected, and a single
aggregated `error(...)` is raised only after every repo has had its chance to commit/create a PR,
so one repo's `createPR()` failure can no longer silently skip the repos after it in the list
(previously mmg800/mev/cnic never ran once mmg's `createPR()` threw). The remaining 6
`SYS_SYSFW_CREDENTIALS_ID` usages elsewhere in the file (`getUserDetails()`, `commitEmail()`,
`sendEmail()`, `pushChanges()`, the unused/dead `checkGithubRateLimit()`, and the non-fatal
`post{always{}}` rate-limit diagnostic) were deliberately left unchanged — they either don't
actually use the bound token/user in their body, call a different external script
(`find_pr_mail.py`) not implicated in this incident, or are non-fatal diagnostics; changing them
was out of scope for this fix.

**Not fully confirmed**: whether the root cause was specifically the *credential identity*
(`sys_sysfw` vs `sys_sysfw2`) or the *Basic-Auth mechanism* itself being rejected by this GHE
instance (or both) was not verified directly against the credential store — the fix addresses both
possibilities at once (switching both the credential and the auth header style to the known-good
combination), but this hasn't yet been confirmed against a live re-run of this exact bundle.

## 9. `findOpenPullRequestByBranch()` silently searched the wrong (unfiltered) page of PRs — missed genuinely-open, older PRs

**Status: FIXED** (in `findOpenPullRequestByBranch()` only — deliberately scoped to
`NVMGeneration.groovy`, not the shared `github.groovy` helper; see Root cause below).

**Symptom signature observed** (build triggered on a cnic PR bundled with mev/mmg/mmg800/the
NVM-generator tool repo itself): the build finished `SUCCESS` and every bundle PR's checker was
correctly updated to green **except** the one in `firmware.ethernet.mountevans.snic-nvm-generator`
(`NVM_GENERATOR_REPO`). The log showed, both in "Mark Bundle PRs Pending" and in the final
`post{}` bundle update: `No open PR found on branch <branch> in
firmware.ethernet.mountevans.snic-nvm-generator, skipping checker update for it` — even though the
target PR (e.g. #2234) was confirmed via `pull_request_read`/`list_pull_requests` to be genuinely
`open`, not merged, not closed, with `head.ref` exactly matching the bundle branch.

**Root cause**: `GithubGet(githubUrl, expCode, query)` in
[ci_groovy/github.groovy](/home/sleonarx/ci-general/ci_groovy/github.groovy) serializes its
`query` Map argument into a curl **`-d` JSON request body**, but the request itself is always
`-X GET`. GitHub's REST API only reads filters (`state`, `head`, `per_page`, ...) from the **URL
query string** for GET endpoints — a JSON body on a GET request is silently ignored, not an error.
`findOpenPullRequestByBranch()` is the *only* call site in all of `ci-general` that passes a
non-empty `query` map to `GithubGet()` (confirmed by grep across the repo), so this bug is isolated
to bundle-PR resolution, but it silently breaks it: every call actually hit GitHub's **unfiltered
default** result set for `GET /repos/{owner}/{repo}/pulls` — `state=open` (coincidentally matches),
`sort=created&direction=desc`, `per_page=30` — i.e. just "the 30 most-recently-*created*-and-still-
open PRs in the repo", nothing to do with the requested branch. The function's client-side
`data.findAll { it.head.ref == branch }` then filtered *that* irrelevant page, which obviously
misses any target PR that isn't among the 30 newest. Confirmed directly against the live GitHub
API: a `head`-filtered-by-bare-branch-name request (simulating the bug, no owner prefix, real query
string) still returned those same unrelated 30 PRs; a request using the proper
`owner:branch`/`org:branch` `head` format (e.g. `intel-innersource:dev/sleonarx/test`) as an actual
query-string parameter immediately returned the single correct match. PR #2234 (created many months
earlier than ~90 other currently-open PRs in that repo) was exactly the kind of case this silently
breaks — a real, open, correctly-named PR invisible to the lookup for no reason a user could see in
the GitHub UI.

**Why this went unnoticed for other bundle repos in the same run**: whether a repo's target PR
happens to be among its own 30-most-recently-*created* open PRs at call time is incidental — it
depends only on how many newer open PRs that repo happens to have accumulated, not on anything
about the PR itself. The config repos' PRs in this run happened to be recent/low-churn enough to
still land in that default page; `NVM_GENERATOR_REPO` evidently accumulates many more long-lived
open PRs, so it was the one to miss. Any bundle repo can silently hit this given enough open-PR
churn.

**Fix (scoped to `NVMGeneration.groovy` only, `github.groovy` intentionally left untouched since
other pipelines call `GithubGet()` too)**: `findOpenPullRequestByBranch()` now builds the filter
query string itself (URL-encoded via `java.net.URLEncoder.encode()`, already used elsewhere in this
file) and folds it directly into the `githubUrl` path passed to `GithubGet()`, calling it with no
`query` map at all so it never appends its broken `-d` body. `head` is now sent as
`"${GIT_ORG_INTEL}:${branch}"` (org-prefixed) instead of a bare branch name, matching what GitHub's
API actually requires to filter same-repo PRs by head branch. Verified: syntax-checked clean via
Groovy's `CompilationUnit` at `Phases.CONVERSION`; the corrected query format was confirmed
directly against the live GitHub API to return exactly the expected single PR.

**If `GithubGet()`'s `query` parameter is ever passed a non-empty map at a new call site in the
future**, it will hit this same silent-no-op bug — the real, root-cause fix (query string in the
URL, not a `-d` body, inside `GithubGet()` itself) is still outstanding in `github.groovy` and
should be applied there if a second call site ever needs it.
