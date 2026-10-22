---
name: nvmgeneration-pipeline
description: 'Explain, debug, and extend the NVMGeneration.groovy Jenkins pipeline (ci-general repo, SDK/NVMGeneration.groovy) - the CI that regenerates MEV/MMG/MMG800/CNIC NVM autoload images and posts a GitHub checker status (context "NVMGeneration_pipeline") back onto every PR in a branch-matched bundle across the mev/mmg/mmg800/cnic/nvm-generator config repos. Use when asked to: diagnose why a NVMGeneration pipeline build did or did not turn a PR checker red/green, analyze a NVMGeneration Jenkins console log, explain what a stage or function in NVMGeneration.groovy does, trace a commit/push/createPR/update_pr.py/checker-status flow, investigate a PR bundle not all going red together, debug stale commit status / wrong PR commented / dropped bundle repo / NotSerializableException / CPS agent offline flapping, or propose a fix to checker-status/PR-bundle logic in this pipeline.'
argument-hint: "<jenkins_console_log_path> [--repo <repo_name>]  |  a question about NVMGeneration.groovy"
---

# NVMGeneration.groovy Pipeline Knowledge & Log Forensics

Everything needed to reason about the **NVMGeneration** Jenkins pipeline
([NVMGeneration.groovy](/home/sleonarx/ci-general/SDK/NVMGeneration.groovy), in
`firmware.ethernet.validation.tools.ci-general`): what it does, how its PR-bundle /
checker-status logic works, which bugs in that logic have already been found and fixed, and a
log-forensics script that automates the console-log investigation methodology used to find them.

## When to Use

- "why didn't the checker go red/green on PR #... after this NVMGeneration build?"
- "analyze this Jenkins console log for the NVMGeneration pipeline"
- "a PR bundle (mmg/mmg800/mev/cnic) didn't all get the same checker status - why?"
- "what does `createPR()` / `repoCommitWrapper()` / `getBundlePullRequests()` / stage X do in
  NVMGeneration.groovy?"
- "I want to change how checker statuses are posted / how the PR bundle is resolved in this
  pipeline"
- "the commit/comment landed on the wrong PR" / "the status update seems stale" / seeing
  `NotSerializableException`, `cannot find current thread in CpsStepContext`, or `seems to be
  removed or offline` in a NVMGeneration build log

## Usage / Procedure

### 1. Run the log forensics script first, whenever a console log is available

```bash
python3 <skill-dir>/scripts/diagnose_pipeline_log.py <jenkins_console_log>

# Narrow to one repo's activity (commits, update_pr.py calls/results)
python3 <skill-dir>/scripts/diagnose_pipeline_log.py <log> --repo firmware.ethernet.mountmorgan800.snic-nvm-generator-config

# Show parallel-branch sub-stages too (hidden by default - they can be entire shell commands)
python3 <skill-dir>/scripts/diagnose_pipeline_log.py <log> --show-branches

# Full structured data (for scripting/further filtering)
python3 <skill-dir>/scripts/diagnose_pipeline_log.py <log> --json
```

It extracts (never auto-concludes — see step 3): the stage timeline; every repo's `<repo> changed
status true/false` line from the "Commit and create PR" stage; every `git commit`/`git push`
event with the resulting SHA; PR-existence lookup results (`404`s, "no existing PR, creating new
one", `findOpenPullRequestByBranch()` results); every bundle-targets resolution line; every
`update_pr.py` invocation (repo/status/PR) and its success/failure; Jenkins CPS "agent seems
offline"/"back online" flap counts, flagging any that occur within a few lines of a `git push`
(the known `JsonSlurper()`/`LazyMap` signature — see
[known-issues.md #1](./references/known-issues.md#1-jsonslurper-vs-jsonslurperclassic)); any
`NotSerializableException`/`Caused:` stack-trace fragments; and the final `Finished:`/`Current
Build Result:` lines. Line numbers in its output are directly `grep -n`/`view`-cross-referenceable
against the same log file.

### 2. Cross-reference against known, already-fixed bugs

Read **[references/known-issues.md](./references/known-issues.md)** before investigating further
— it documents 7 specific bugs already found and fixed in this pipeline (stale JsonSlurper/LazyMap
serialization crash, stale-commit checker races, silent bundle-repo exclusion, wrong-PR comments,
a `post{}` single point of failure, unnormalized build results, and the one genuinely
unfixable-from-pipeline-code case: a catastrophic agent/controller death before `post{}` runs).
**Check whether the log's symptoms match one of these before treating it as a new issue** — and if
it's genuinely new, add a new numbered entry to that file once understood, so it isn't
rediscovered from scratch next time.

### 3. Reason over the evidence, don't just report the extracted facts

The script surfaces *evidence*; forming the actual "why" conclusion requires correlating multiple
sections by hand (or by the agent) — e.g.: did the repo that mattered actually get a `changed
status true`? Did its `update_pr.py` call report `succeeded`? Was there a CPS flap near its
`git push`? Did a `NotSerializableException` follow? Was the PR bundle-resolution line's target
list missing a repo that should have been included (cross-check against
[architecture.md §4](./references/architecture.md#4-the-bundle-concept))? Only after walking
through these should you state a root cause — and say explicitly if you weren't able to fully
confirm one (e.g. "the status call reported success, but I can't confirm from this log alone
whether it landed on the right commit — see known-issues.md #2").

### 4. For architecture/code questions (not log analysis)

Read **[references/architecture.md](./references/architecture.md)** — full stage-by-stage flow,
every repo cloned and how its branch is resolved, the Bundle concept in depth, the
commit→PR→checker-status call chain (`repoCommitWrapper` → `createPRWrapper` → `createPR` /
`notifyRepoPullRequest` → `post{always{}}` → `updateBundleCheckerStatus` → `waitForPrHeadSha` →
`update_pr.py`), and a couple of flagged-but-not-yet-fixed oddities (e.g. a dead
`params.UTILS_REPO_BRANCH` reference). Prefer reading the live
[NVMGeneration.groovy](/home/sleonarx/ci-general/SDK/NVMGeneration.groovy) itself for exact current
code — this reference describes behavior and intent, and can drift from the file over time.

### 5. Before proposing a fix

- Check [known-issues.md](./references/known-issues.md) first — it may already be fixed; read the
  relevant function's current implementation to confirm rather than assuming the chat/doc history
  is still accurate.
- `scripts/update_pr.py` lives in a **different repo** (`firmware.ethernet.mountmorgan.sdk-tools`,
  cloned at runtime as `SDK_TOOLS_REPO`) — it is NOT in `ci-general` and most likely not available
  in your current workspace. Don't assume you can fix its internals; mitigations from
  `NVMGeneration.groovy`'s side (e.g. `waitForPrHeadSha()`) are the available lever unless that
  repo is explicitly provided.
- Any fix touching Groovy syntax should be validated — Jenkins Pipeline Groovy runs under a CPS
  sandbox with real gotchas (e.g. `JsonSlurper()`'s `LazyMap` not being serializable,
  `.unique(Closure)`/`.sort(Closure)` resolving to the wrong overload under CPS - see the comment
  in `getBundlePullRequests()`). A plain syntax check (e.g. Groovy's `CompilationUnit` at
  `Phases.CONVERSION`) catches basic errors but not CPS-specific ones — reason carefully about
  serializability and sandbox-safe APIs for anything living on the live pipeline-script call stack
  (not inside a `@NonCPS` method).

## Reference Files

- [references/architecture.md](./references/architecture.md) — full pipeline deep-dive: purpose,
  trigger params, every cloned repo + branch resolution, the Bundle concept, stage-by-stage flow,
  the commit→PR→checker-status call chain, where `update_pr.py` actually lives.
- [references/known-issues.md](./references/known-issues.md) — numbered history of bugs already
  found + fixed (or deliberately mitigated / flagged as out-of-scope) in this pipeline's
  checker-status logic, plus the real incident (build #4523) this was all diagnosed from.
- [scripts/diagnose_pipeline_log.py](./scripts/diagnose_pipeline_log.py) — the log-forensics tool
  described above.
