---
name: compare-drop-to-github-pipeline
description: 'Explain, debug, and extend the compare_drop_to_github.groovy Jenkins pipeline (ci-general repo, SDK/compare_drop_to_github.groovy) - the on-demand job that clones firmware.ethernet.mountmorgan.sdk-tools and runs scripts/compare_drops2github.py to diff a local IP register "drop" (osxml) folder against a GitHub branch and, optionally, an Artifactory release, writing output/output.csv plus per-IP diff_*.txt files. Use when asked to: diagnose a failed/red compare_drop_to_github Jenkins build, analyze its console log, explain the `to_compare` CSV schema or the RELEASE (-r) parameter, debug why an IP row is missing from output.csv, "ERROR" rows, clone failures (github or sdk-tools), the has_tag column, or propose a fix/feature to this pipeline or compare_drops2github.py (new CSV column, better error handling, new comparison mode).'
argument-hint: "<jenkins_console_log_path> [--json]  |  a question about compare_drop_to_github.groovy"
---

# compare_drop_to_github.groovy — Debug, Fix & Extend Guide

Everything needed to reason about the on-demand **compare drop to github** Jenkins pipeline
([compare_drop_to_github.groovy](/home/sleonarx/ci-general/SDK/compare_drop_to_github.groovy),
repo `firmware.ethernet.validation.tools.ci-general`): what the single stage does, the exact
behavior of the `compare_drops2github.py` script it runs (from the separate
`firmware.ethernet.mountmorgan.sdk-tools` repo), the `to_compare` CSV contract, the `RELEASE`
parameter, known bugs/quirks found by reading the source, and a log-forensics script.

This pipeline diffs a **drop** (a local register "osxml" folder delivered by an IP team) against
the matching GitHub `rtls.ethernet.mountmorgan.registerdescription.<ip>` repo branch, and
optionally against an Artifactory HAS release's `osxml/`, to check whether the drop has already
been synced into GitHub / matches a release.

## When to Use

- "the compare_drop_to_github Jenkins build failed/is red — why?"
- "analyze this Jenkins console log for the compare drop to github pipeline"
- "what format does the `to_compare` CSV need?" / "build me an example `to_compare` CSV"
- "why is IP X missing from `output.csv`?" / "why does row X say ' ERROR' instead of equal/not
  equal?"
- "what does the `RELEASE` / `-r` parameter do and what URL shape does it expect?"
- "what does `navigate_and_compare()` / `check_tag()` / `diff_file_name()` do?"
- "I want to add a column / new comparison mode / better error handling to
  `compare_drops2github.py` or the groovy wrapper"

## Usage / Procedure

### 1. Run the log forensics script first, whenever a console log is available

```bash
python3 <skill-dir>/scripts/diagnose_compare_drop_log.py <jenkins_console_log>

# Structured output for scripting
python3 <skill-dir>/scripts/diagnose_compare_drop_log.py <log> --json
```

It extracts (facts only, never a verdict): the stage timeline; the `sdk-tools` `clone_repo`
call and its result; the exact `compare_drops2github.py` command line (csv path + optional `-r`
release); then, per IP row (in CSV order, grouped between consecutive `...Going over X...`
markers): the resolved drop→osxml path (or the "directory does not exist" / "neither 'reg' nor
'register' found" failure), the github branch clone result, the `comparing <a> and <b>` diff
invocations and their equal/not-equal/ERROR outcome for both the github and release comparisons,
and — importantly — flags any IP whose only output was a **bare** `ERROR: ...` line not tied to
"when comparing drop to git/release" (see
[known-issues.md #2](./references/known-issues.md#2-navigate_and_compare-returning-none-silently-drops-the-row)
and [#1](./references/known-issues.md#1-has_tag-leaksundefined-when-the-ip-repo-column-is-empty)) —
those IPs are **silently missing from `output.csv`** even though the console log mentions them.
Finally: the groovy-level `scripts/compare_drop_2_github.py failed!` error (if the python process
itself exited non-zero), whether `Archiving artifacts` ran, and the `Finished:` result.

### 2. Understand the CSV contract before debugging "wrong"/"missing" rows

Read **[references/architecture.md](./references/architecture.md) §3** for the exact
`to_compare` CSV schema (4 columns, **no header validation** — a missing header silently eats
the first data row) and the `RELEASE` URL shape. A live example lives at
[ci-general/SDK/example_compare_drop_to_github.csv](/home/sleonarx/ci-general/SDK/example_compare_drop_to_github.csv).

### 3. Cross-reference against known, code-read bugs/quirks

Read **[references/known-issues.md](./references/known-issues.md)** before treating a symptom as
new — it documents quirks found by reading `compare_drops2github.py` and the groovy wrapper
directly (a `has_tag` variable leak/`UnboundLocalError` when the IP-repo column is empty, rows
silently dropped from `output.csv` on certain exceptions, a `clean(repo)` crash when no row ever
populated `repo`, `archiveArtifacts` never running after a script-level crash because `error()`
is called first, unvalidated CSV header, and brittle positional `RELEASE` URL parsing). None of
these are marked "log-confirmed" unless you've actually seen the matching line in a real log —
say explicitly which is which.

### 4. For architecture/code questions (not log analysis)

Read **[references/architecture.md](./references/architecture.md)** — full walkthrough of the
single `compare_drop_2_github` stage (parameters, `CopyConfigurations`, cloning `sdk-tools`,
building the python command), then a function-by-function deep dive of
`compare_drops2github.py` (`parse_args`, `clone_repo`, `check_branch_exist`,
`navigate_and_compare`, `diff_file_name`, `create_dir`, `delete_empty_files`, `check_tag`,
`main()`'s per-row flow, and the `download_directory_to_ip`/Artifactory path math used for the
`RELEASE` comparison). The script itself lives in a **different repo**
(`firmware.ethernet.mountmorgan.sdk-tools`, cloned at runtime, not checked out in this
workspace) — use the `github_repo` / `github_text_search` tools against
`intel-restricted/firmware.ethernet.mountmorgan.sdk-tools` to re-read current source rather than
assuming this doc hasn't drifted (`fetch_webpage` 404s on intel-restricted GitHub URLs — don't
retry it for this repo).

### 5. Before proposing a fix

- Check [known-issues.md](./references/known-issues.md) first; confirm against current source
  rather than assuming it's still accurate.
- Any fix to `compare_drops2github.py` must be made (or at least mirrored) in the **sdk-tools**
  repo, not in `ci-general` — the groovy file only clones and invokes it.
- Any fix to the groovy stage itself should preserve the existing `error()`-then-`archiveArtifacts`
  ordering concern (see known-issues.md #4) if you touch failure handling — consider archiving
  `output/**` in a `post { always {} }` (or before the status check) so partial results survive a
  script-level crash.

### 6. Honesty rule

The quirks in [architecture.md](./references/architecture.md) and
[known-issues.md](./references/known-issues.md) are **code-read** findings from the live
`compare_drops2github.py` (fetched via `github_repo`) and
[compare_drop_to_github.groovy](/home/sleonarx/ci-general/SDK/compare_drop_to_github.groovy)
unless marked as log-confirmed. State plainly what a log actually shows vs. what is inferred from
source, and re-read the source before asserting a fix is correct.

## Reference Files

- [references/architecture.md](./references/architecture.md) — stage walkthrough, `to_compare`
  CSV schema, `RELEASE` parameter, and a function-by-function tour of `compare_drops2github.py`.
- [references/known-issues.md](./references/known-issues.md) — numbered, code-read quirks/bugs
  in this pipeline and its python script.
- [scripts/diagnose_compare_drop_log.py](./scripts/diagnose_compare_drop_log.py) — the
  log-forensics tool described above.
