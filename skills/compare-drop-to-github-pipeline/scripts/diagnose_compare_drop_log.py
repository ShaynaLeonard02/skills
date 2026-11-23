#!/usr/bin/env python3
"""
Forensics helper for compare_drop_to_github.groovy Jenkins console logs.

Extracts the facts needed to explain "why did the compare-drop-to-github build fail / why is
IP X missing from output.csv": the stage/clone timeline for the sdk-tools repo, the exact
compare_drops2github.py invocation, and - per IP row (grouped between consecutive
"...Going over X..." markers) - the resolved drop->osxml path, the github branch clone result,
every `comparing <a> and <b>` diff and its equal/not-equal/ERROR outcome, and whether that IP's
row was ever actually written to output.csv or silently dropped by a bare (un-contextualized)
ERROR line (see references/known-issues.md #1 and #2).

It deliberately does NOT render a verdict - read the evidence, then reason (see SKILL.md
"Usage / Procedure").

Usage:
    python3 diagnose_compare_drop_log.py <log_file> [--json]
"""
import argparse
import json
import re
import sys

TIMESTAMP_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+Z)\]\s?(?P<rest>.*)$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
STAGE_RE = re.compile(r"^\[Pipeline\] \{ \((?P<name>.+)\)\s*$")

CLONE_START_RE = re.compile(
    r"^> clone_repo - Clone dir: (?P<dir>\S+) repo_name: (?P<repo>\S+) stage_name: (?P<stage>\S+) branch: (?P<branch>\S*)"
)
CLONE_OK_RE = re.compile(r"^Stage: (?P<stage>\S+) - (?P<repo>\S+) cloned successfully")
CLONE_FAIL_RE = re.compile(r"Stage: (?P<stage>\S+) - (?P<repo>\S+) clone failed!")

PY_LAUNCH_RE = re.compile(r"Run compare drop to github script with the csv file\s*:\s*(?P<csv>.+)$")

GOING_OVER_RE = re.compile(r"^\.\.\.Going over (?P<ip>.+?)\.\.\.$")
DROP_OSXML_RE = re.compile(r"^drop path connected to osxml is (?P<path>.+)$")
DROP_NOT_FOUND_RE = re.compile(r"^Neither 'reg' nor 'register' found in the drop path\.$")
DROP_DIR_MISSING_RE = re.compile(r"^The directory '(?P<dir>.*)' does not exist\.$")

SEARCH_BRANCH_RE = re.compile(r"^Searching for branch '(?P<branch>.+)'$")
BRANCH_EXIST_RE = re.compile(r"^Branch '(?P<branch>.+)' exist$")
CLONE_FAIL2_RE = re.compile(r"^Failed to clone repo$")

COMPARING_RE = re.compile(r"^comparing (?P<a>.+) and (?P<b>.+)$")
FINISHED_RE = re.compile(
    r"^FINISHED: in (?P<ip>\S+) results were (?P<eq>equal|not equal) when comparing drop to (?P<target>git|release), updated row"
)
ROW_ERROR_RE = re.compile(
    r"^ERROR: (?P<text>.+), in (?P<ip>\S+) when comparing drop to (?P<target>git|release)\."
)
BARE_ERROR_RE = re.compile(r"^ERROR: (?P<text>.+)$")

DELETE_EMPTY_RE = re.compile(r"^Deleting empty file: (?P<path>.+)$")
SUCCESS_DELETE_RE = re.compile(r"^Successfully deleted (?P<dir>.+)$")

GROOVY_FAIL_RE = re.compile(r"scripts/compare_drop_2_github\.py failed!")
ARCHIVING_RE = re.compile(r"^Archiving artifacts")
FINISHED_BUILD_RE = re.compile(r"^Finished:\s*(?P<result>\w+)\s*$")


def strip_line(raw):
    line = raw.rstrip("\n")
    m = TIMESTAMP_RE.match(line)
    ts, line = (m.group("ts"), m.group("rest")) if m else (None, line)
    return ts, ANSI_RE.sub("", line)


def new_ip_entry(name, line):
    return {
        "ip": name,
        "line": line,
        "drop_osxml": None,
        "drop_error": None,
        "github": {"branch": None, "branch_result": None, "diffs": []},
        "release": {"diffs": []},
        "row_written": False,
        "dropped_reason": None,  # set if a bare ERROR occurred for this ip -> row missing from output.csv
    }


def parse(path):
    report = {
        "sdk_tools_clone": None,   # {dir, branch, result, line}
        "py_command": None,       # {csv, line}
        "ips": [],                # ordered list of ip entries
        "cleanup": [],            # [{line, text}]
        "groovy_failed": None,    # {line}
        "archiving": None,        # {line}
        "finished": None,         # {result, line}
    }
    current = None

    with open(path, errors="replace") as f:
        for n, raw in enumerate(f, 1):
            ts, line = strip_line(raw)

            m = CLONE_START_RE.match(line)
            if m:
                report["sdk_tools_clone"] = {
                    "dir": m.group("dir"), "branch": m.group("branch") or "(default)",
                    "result": None, "line": n,
                }
                continue
            m = CLONE_OK_RE.match(line) or CLONE_FAIL_RE.search(line)
            if m and report["sdk_tools_clone"] and report["sdk_tools_clone"]["result"] is None:
                report["sdk_tools_clone"]["result"] = "ok" if CLONE_OK_RE.match(line) else "FAILED"
                report["sdk_tools_clone"]["result_line"] = n
                continue

            m = PY_LAUNCH_RE.match(line)
            if m:
                report["py_command"] = {"csv": m.group("csv").strip(), "line": n}
                continue

            m = GOING_OVER_RE.match(line)
            if m:
                current = new_ip_entry(m.group("ip"), n)
                report["ips"].append(current)
                continue

            if current is not None:
                m = DROP_OSXML_RE.match(line)
                if m:
                    current["drop_osxml"] = m.group("path")
                    continue
                if DROP_NOT_FOUND_RE.match(line):
                    current["drop_error"] = "neither 'reg' nor 'register' found under <drop>/src"
                    continue
                m = DROP_DIR_MISSING_RE.match(line)
                if m:
                    current["drop_error"] = f"directory does not exist: {m.group('dir')}"
                    continue

                m = SEARCH_BRANCH_RE.match(line)
                if m:
                    current["github"]["branch"] = m.group("branch")
                    continue
                if BRANCH_EXIST_RE.match(line):
                    current["github"]["branch_result"] = "ok"
                    continue
                if CLONE_FAIL2_RE.match(line):
                    current["github"]["branch_result"] = "FAILED"
                    continue

                m = COMPARING_RE.match(line)
                if m:
                    current.setdefault("_last_comparing", []).append({"a": m.group("a"), "b": m.group("b"), "line": n})
                    continue

                m = FINISHED_RE.match(line)
                if m:
                    target = "github" if m.group("target") == "git" else "release"
                    current[target]["diffs"].append({"result": m.group("eq"), "line": n})
                    current["row_written"] = True
                    continue

                m = ROW_ERROR_RE.match(line)
                if m:
                    target = "github" if m.group("target") == "git" else "release"
                    current[target]["diffs"].append({"result": "ERROR", "text": m.group("text"), "line": n})
                    current["row_written"] = True
                    continue

                m = BARE_ERROR_RE.match(line)
                if m:
                    # A bare ERROR (no "when comparing drop to git/release" suffix) means the
                    # row-level try/except swallowed an exception OUTSIDE the diff-comparison
                    # logic (navigate_and_compare() -> None, has_tag UnboundLocalError, a clone
                    # exception not caught by check_branch_exist, ...) - the row never reaches
                    # writer.writerow() and is silently missing from output.csv.
                    current["dropped_reason"] = m.group("text")
                    continue

            m = DELETE_EMPTY_RE.match(line)
            if m:
                report["cleanup"].append({"line": n, "text": f"deleted empty diff file: {m.group('path')}"})
                continue
            m = SUCCESS_DELETE_RE.match(line)
            if m:
                report["cleanup"].append({"line": n, "text": f"cleaned dir: {m.group('dir')}"})
                continue

            if GROOVY_FAIL_RE.search(line):
                report["groovy_failed"] = {"line": n}
                continue
            if ARCHIVING_RE.match(line):
                report["archiving"] = {"line": n}
                continue
            m = FINISHED_BUILD_RE.match(line)
            if m:
                report["finished"] = {"result": m.group("result"), "line": n}
                continue

    for ip in report["ips"]:
        ip.pop("_last_comparing", None)
    return report


def render(r):
    out = []
    p = out.append

    p("== sdk-tools clone ==")
    c = r["sdk_tools_clone"]
    if c:
        p(f"  L{c['line']:>7}  dir={c['dir']} branch={c['branch']} -> {c['result'] or 'no result line'}")
    else:
        p("  (clone_repo call not found - build may have failed before/during CopyConfigurations)")

    p("\n== compare_drops2github.py command ==")
    if r["py_command"]:
        p(f"  L{r['py_command']['line']:>7}  csv={r['py_command']['csv']}")
    else:
        p("  (launch line not found - script may never have run)")

    p(f"\n== Per-IP results ({len(r['ips'])} IP(s) seen) ==")
    if not r["ips"]:
        p("  (no '...Going over X...' markers found)")
    for ip in r["ips"]:
        p(f"  --- {ip['ip']} (L{ip['line']}) ---")
        if ip["drop_error"]:
            p(f"      !! drop path error: {ip['drop_error']}")
        elif ip["drop_osxml"]:
            p(f"      drop osxml: {ip['drop_osxml']}")
        gh = ip["github"]
        if gh["branch"]:
            p(f"      github branch: {gh['branch']} -> {gh['branch_result'] or 'no result line'}")
        for d in gh["diffs"]:
            extra = f" ({d['text']})" if "text" in d else ""
            p(f"      github diff (L{d['line']}): {d['result']}{extra}")
        for d in ip["release"]["diffs"]:
            extra = f" ({d['text']})" if "text" in d else ""
            p(f"      release diff (L{d['line']}): {d['result']}{extra}")
        if ip["dropped_reason"]:
            p(f"      !! ROW LIKELY MISSING FROM output.csv - bare error (no 'when comparing' "
              f"context): {ip['dropped_reason']}")
            p("         see known-issues.md #1/#2 for the two known causes of this pattern")
        elif not ip["row_written"] and not ip["dropped_reason"]:
            p("      !! no FINISHED/ERROR-row line seen for this IP - log may be truncated here")

    if r["cleanup"]:
        p("\n== Cleanup ==")
        for e in r["cleanup"]:
            p(f"  L{e['line']:>7}  {e['text']}")

    p("\n== Groovy stage result ==")
    if r["groovy_failed"]:
        p(f"  L{r['groovy_failed']['line']:>7}  error(\"scripts/compare_drop_2_github.py failed!\") "
          f"-> python process exited non-zero (see known-issues.md #3 for a cause unrelated to "
          f"per-row diff failures)")
    if r["archiving"]:
        p(f"  L{r['archiving']['line']:>7}  Archiving artifacts (output/**) ran")
    else:
        p("  Archiving artifacts: NOT seen" +
          (" - expected, since error() was called first (known-issues.md #4)" if r["groovy_failed"] else ""))

    p(f"\n== Finished: {r['finished']['result'] if r['finished'] else 'line not found'} ==")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log_file")
    ap.add_argument("--json", action="store_true", help="emit structured JSON instead of text")
    args = ap.parse_args()
    try:
        report = parse(args.log_file)
    except OSError as e:
        sys.exit(f"Cannot read {args.log_file}: {e}")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))


if __name__ == "__main__":
    main()
