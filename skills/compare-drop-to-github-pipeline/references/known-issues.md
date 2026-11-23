# compare_drop_to_github.groovy / compare_drops2github.py — Known Issues & Quirks

All entries below are **code-read** findings from
[compare_drop_to_github.groovy](/home/sleonarx/ci-general/SDK/compare_drop_to_github.groovy) and
the live `compare_drops2github.py` in `intel-restricted/firmware.ethernet.mountmorgan.sdk-tools`
(fetched via the `github_repo` tool — this repo is not checked out locally). None are marked
"log-confirmed" unless a matching console-log line has actually been observed — check that
before presenting one of these as the root cause of a specific failed build.

## 1. `has_tag` leaks/undefined when the IP-repo column is empty

In `main()`, `has_tag` is only ever assigned **inside** the `if row[2] != "" and row[2] is not
None:` branch (via `check_tag()`). The line that appends it to the output row —
`row = row + [f" {has_tag}"]` — runs **unconditionally**, for every row, regardless of that
branch.

Because `main()`'s `for row in reader:` loop is a single Python function scope (no per-iteration
variable reset), this means:
- If the **very first** CSV row has an empty `row[2]` (no github repo configured for that IP),
  `has_tag` has never been assigned at all → `UnboundLocalError` is raised, caught by the row's
  own `except Exception as e:`, and **the row is silently dropped from `output.csv`** (only an
  `ERROR: ...` line is printed to the console; see #2 for how to recognize this pattern in a
  log).
- If a **later** row has an empty `row[2]`, `has_tag` still holds the value computed for the
  **previous** row that had a non-empty `row[2]` — i.e., the "has tag" column for an IP with no
  GitHub comparison can show a stale/wrong value carried over from a different IP, with no error
  at all.

Fix direction: initialize `has_tag = "N/A"` (or similar) before the `if row[2] != ""` check, each
iteration.

## 2. `navigate_and_compare()` returning `None` silently drops the row

If `navigate_and_compare(row[1])` can't find a `"reg"`-named directory under `<drop>/src` (or
`<drop>` isn't a directory at all), it prints a message and returns `None`. The very next line,
`drop_to_compare = navigate_and_compare(row[1]).strip()`, then raises
`AttributeError: 'NoneType' object has no attribute 'strip'`, caught by the per-row
`except Exception as e: print(RED + f'ERROR: {e}' + RESET)` — which has **no `in <ip> when
comparing ...` suffix** (unlike the diff-comparison error path, which does print that context).

**Recognizing this in a log:** a bare `ERROR: 'NoneType' object has no attribute 'strip'` (or an
`UnboundLocalError`/`NameError: name 'has_tag' is not defined` from #1) with no following "when
comparing drop to git/release" text means that IP's row **never made it into `output.csv` at
all** — not even as an `' ERROR'`-suffixed row. If the user says "IP X is just missing from the
output, no error status, nothing" — this is almost certainly why; check the drop path (`row[1]`)
actually contains a `.../src/<something with "reg" in the name>/src/osxml` structure.

## 3. `clean(repo)` can crash after the loop if `repo` was never set

`repo` is initialized to `None` before the CSV loop and only reassigned inside
`check_branch_exist()`'s success path (i.e., only when some row had a non-empty `row[2]` *and*
the clone succeeded). After the loop, `clean(repo)` is called unconditionally — `clean()` does
`os.path.exists(this_dir)`, which raises `TypeError` on `None` (not `str`). This happens
**outside** the per-row `try/except`, so if every row either had an empty `row[2]` or failed to
clone, this raises and kills the whole python process with a non-zero exit — which **does**
propagate to the groovy `sh` step's `returnStatus`, triggering `error("scripts/compare_drop_2_github.py failed!")`.

Net effect: a CSV where no row successfully resolves a GitHub branch (e.g., all rows have a typo'd
branch name, or the repo column is left blank everywhere) can fail the **entire build** even
though every individual row's drop/release diffs may have completed fine and `output/output.csv`
has rows written for them.

## 4. `error()` runs before `archiveArtifacts`, so a script-level crash loses `output/` entirely

The groovy stage is:
```groovy
int status = sh(... returnStatus: true, script: script_command)
if (status != 0) {
    error("scripts/compare_drop_2_github.py failed!")
}
archiveArtifacts artifacts: 'output/**', fingerprint: true
```
`error()` throws immediately, aborting the `steps { script { ... } }` block — the
`archiveArtifacts` line is **never reached**. Combined with #3 (a crash *after* the per-row loop
has already written a mostly-complete `output.csv`), this means a build can go fully red with
**zero archived artifacts**, even though the comparison data existed on disk in the workspace
right up until `cleanWs` wiped it in `post { cleanup {} }`.

If asked to improve failure handling here: move `archiveArtifacts` into a `post { always {} }`
block (or wrap it with `catchError` before the status check), so partial `output.csv`/diff files
survive a downstream crash like #3.

## 5. CSV header row is consumed without any validation

`header = next(reader)` unconditionally treats the CSV's first line as a header and discards it
from comparison. There is no check that it actually looks like a header (e.g., matches the
expected 4 column names). If a user builds/edits a `to_compare` CSV and forgets the header line,
their first real IP row is silently treated as the header and **never compared or appears in
`output.csv`**, with no error printed at all (unlike #1/#2, which at least print something).

## 6. `RELEASE` URL parsing is brittle positional string-splitting

`path_for_download = '/'.join(my_release.split('/')[4:])` and
`path_for_osxml = '/'.join(my_release.split('/')[5:])` assume an exact
`https://<host>/artifactory/<repo-key>/<rest...>/osxml/` shape (5 fixed path segments before the
"rest"). There is no check that the URL actually contains `/artifactory/` or ends in `osxml/` (the
groovy parameter description says it should, but nothing enforces it). A URL with a different
shape (extra port, missing `/artifactory/` segment, trailing content after `osxml/` other than a
single empty split) produces a **silently wrong** Artifactory path — `jf rt dl` then either
downloads nothing (empty/missing folder, usually surfaced as a later "not equal" or "ERROR" from
the `diff` finding `No such file or directory`) or downloads from the wrong location, with no
explicit validation error pointing back at the malformed `RELEASE` value.

## 7. `diff -rw` exit-code bucketing conflates "legitimately different" with "comparison broken"

Exit code `0` → `' equal'`; exit code `1` **or** `"No such file or directory"` appearing anywhere
in stderr → `' not equal'`; anything else → `' ERROR'` (full stderr printed in red). This is
intentional (diff exit 1 = differences found; "No such file or directory" on one side, e.g. the
release wasn't downloaded or osxml dir doesn't exist on one side, is deliberately bucketed as
"not equal" rather than a hard error) — not a bug, but worth knowing when interpreting a
`' not equal'` result: it can mean either "real content differences" **or** "one side of the
comparison didn't actually exist", and `output.csv` alone doesn't distinguish the two — you have
to open the corresponding `diff_<github|release>_<ip>_<n>.txt` to tell which.

## 8. `script_command` (groovy) and `params.SCM_BRANCH` are stylistic landmines, not bugs

- `script_command = "..."` is assigned without `def`/a type — becomes an implicit Groovy
  script-binding variable. Works fine as used (single assignment, used once immediately after),
  but would silently leak/collide if this stage were ever duplicated into a `parallel {}` block
  or another stage reused the same name.
- `params.SCM_BRANCH` (default `SyDK_tools`) is **not referenced anywhere in the script body** —
  it's consumed by Jenkins' own "Pipeline script from SCM" job configuration (which branch of
  `ci-general` to load this Jenkinsfile from), the same pattern seen in other ci-general
  pipelines. Don't go looking for in-script usage of it.
- The groovy `error()` message text says `"scripts/compare_drop_2_github.py failed!"` (legacy
  underscored name) while the real script is `compare_drops2github.py` — purely cosmetic, don't
  chase a "wrong filename" theory from this message.
