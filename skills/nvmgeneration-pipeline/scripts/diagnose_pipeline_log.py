#!/usr/bin/env python3
"""
Forensics helper for NVMGeneration.groovy Jenkins console logs.

Automates the manual grep/view methodology used to diagnose "the pipeline
failed but a checker didn't go red" (or similar) questions: it pulls out the
stage timeline, the per-repo commit/push/PR activity, every GitHub
checker-status update attempt, and flags known bug signatures (see
../references/known-issues.md) - WITHOUT trying to render a final verdict.
This is deliberately a facts-extraction tool, not an auto-diagnosis: always
read the flagged evidence yourself (or have the agent reason over it) before
concluding why something did or didn't happen, exactly as this skill's
"Usage / Procedure" section in SKILL.md describes.

Usage:
    python3 diagnose_pipeline_log.py <log_file> [--repo <repo_name>] [--json]

Examples:
    python3 diagnose_pipeline_log.py "/home/sleonarx/inputs/log (4).txt"
    python3 diagnose_pipeline_log.py build.log --repo firmware.ethernet.mountmorgan800.snic-nvm-generator-config
    python3 diagnose_pipeline_log.py build.log --json > report.json
"""
import argparse
import json
import re
import sys
from collections import defaultdict

STAGE_RE = re.compile(r"^\[Pipeline\] \{ \((?P<name>.+)\)\s*$")
TIMESTAMP_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+Z)\]\s?(?P<rest>.*)$")

# "<label> changed status true/false" - emitted once per repo by
# repoCommitWrapper()'s caller in the "Commit and create PR" stage (e.g.
# "mmg config changed status false", "mmg800 config changed status true",
# "nvm gen changed status true", "cnic config changed status ").
CHANGED_STATUS_RE = re.compile(r"^(?P<label>.+ changed status) (?P<val>true|false)\s*$")

GIT_COMMIT_RE = re.compile(r"^\+ git commit -m")
GIT_COMMIT_SHA_RE = re.compile(r"^\[(?P<branch>\S+) (?P<sha>[0-9a-f]{7,40})\]")
GIT_PUSH_RE = re.compile(r"^\+ git push origin (?P<branch>\S+)\s*$")
GIT_PUSH_RESULT_RE = re.compile(r"^\s*(?P<old>[0-9a-f]{7,40})\.\.(?P<new>[0-9a-f]{7,40})\s+(?P<branch>\S+) -> ")
EVERYTHING_UP_TO_DATE_RE = re.compile(r"^Everything up-to-date\s*$")

# update_pr.py invocation: "... -r <repo> ... -s <STATUS> -p <prId> ..."
UPDATE_PR_CMD_RE = re.compile(
    r"update_pr\.py.*-r\s+(?P<repo>\S+).*-a\s+(?P<action>\S+).*-s\s+(?P<status>\S+).*-p\s+(?P<prid>\S+)"
)
UPDATE_PR_RESULT_RE = re.compile(
    r"^Update PR (?P<outcome>succeeded|failed) for (?P<target>\S+#\S+)"
)
BUNDLE_TARGETS_RE = re.compile(
    r"(?:Updating PR checker status for bundle targets|Setting '(?P<status>\w+)' checker status for bundle targets): (?P<targets>.+)$"
)
PR_EXISTENCE_RESPONSE_RE = re.compile(r'"message"\s*:\s*"Not Found"')
NO_EXISTING_PR_RE = re.compile(r"No existing PR found\. Creating a new one\.")
EXISTING_PR_FOUND_RE = re.compile(r"Existing PR found: (?P<url>\S+)")
FIND_OPEN_PR_RE = re.compile(r"findOpenPullRequestByBranch\(.*=>\s*(?P<result>.+)$")

AGENT_OFFLINE_RE = re.compile(r"seems to be removed or offline")
AGENT_BACK_RE = re.compile(r"is back online")
NOT_SERIALIZABLE_RE = re.compile(r"NotSerializableException:\s*(?P<cls>\S+)")
CAUSED_RE = re.compile(r"^Caused:\s*(?P<exc>.+)$")
FINISHED_RE = re.compile(r"^Finished:\s*(?P<result>\w+)\s*$")
CURRENT_BUILD_RESULT_RE = re.compile(r"Current Build Result:\s*(?P<result>\w+)")

FLAP_CORRELATION_WINDOW_LINES = 5
MAX_STAGE_NAME_LEN = 90  # parallel-branch keys can be entire shell commands (hundreds of chars)


def parse_log(path):
    stages = []
    changed_status = []
    git_events = []
    update_pr_calls = []
    update_pr_results = []
    bundle_target_lines = []
    pr_lookup_events = []
    agent_flaps = []
    exceptions = []
    finished_line = None
    current_build_result_lines = []

    last_timestamp = None
    pending_push_line = None  # line number of the most recent "git push" seen

    # newline="\n" (NOT the default universal-newlines mode) is critical here: some logs embed
    # stray bare \r bytes (e.g. from progress output captured mid-stream) that Python's default
    # universal-newline mode would otherwise treat as extra line breaks, shifting every line
    # number reported below out of sync with what `grep -n` / `wc -l` / the `view` tool report on
    # the same file. Splitting only on \n keeps line numbers directly cross-referenceable.
    with open(path, "r", encoding="utf-8", errors="replace", newline="\n") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.rstrip("\n")
            m = TIMESTAMP_RE.match(line)
            if m:
                last_timestamp = m.group("ts")
                rest = m.group("rest")
            else:
                rest = line

            m = STAGE_RE.match(line)
            if m:
                stages.append({"line": lineno, "ts": last_timestamp, "name": m.group("name")})
                continue

            m = CHANGED_STATUS_RE.match(rest)
            if m:
                changed_status.append({
                    "line": lineno, "ts": last_timestamp,
                    "label": m.group("label"), "value": m.group("val"),
                })
                continue

            if GIT_COMMIT_RE.match(rest):
                git_events.append({"line": lineno, "ts": last_timestamp, "event": "commit", "detail": rest})
                continue
            m = GIT_COMMIT_SHA_RE.match(rest)
            if m:
                git_events.append({
                    "line": lineno, "ts": last_timestamp, "event": "committed_sha",
                    "branch": m.group("branch"), "sha": m.group("sha"),
                })
                continue
            m = GIT_PUSH_RE.match(rest)
            if m:
                pending_push_line = lineno
                git_events.append({"line": lineno, "ts": last_timestamp, "event": "push",
                                    "branch": m.group("branch")})
                continue
            m = GIT_PUSH_RESULT_RE.match(rest)
            if m:
                git_events.append({
                    "line": lineno, "ts": last_timestamp, "event": "push_result",
                    "old_sha": m.group("old"), "new_sha": m.group("new"), "branch": m.group("branch"),
                })
                continue
            if EVERYTHING_UP_TO_DATE_RE.match(rest):
                git_events.append({"line": lineno, "ts": last_timestamp, "event": "push_noop"})
                continue

            m = UPDATE_PR_CMD_RE.search(rest)
            if m:
                update_pr_calls.append({
                    "line": lineno, "ts": last_timestamp,
                    "repo": m.group("repo"), "action": m.group("action"),
                    "status": m.group("status"), "pr_id": m.group("prid"),
                })
                continue
            m = UPDATE_PR_RESULT_RE.match(rest)
            if m:
                update_pr_results.append({
                    "line": lineno, "ts": last_timestamp,
                    "outcome": m.group("outcome"), "target": m.group("target"),
                })
                continue

            m = BUNDLE_TARGETS_RE.search(rest)
            if m:
                bundle_target_lines.append({
                    "line": lineno, "ts": last_timestamp,
                    "status": m.group("status"), "targets": m.group("targets"),
                })
                continue

            if PR_EXISTENCE_RESPONSE_RE.search(rest):
                pr_lookup_events.append({"line": lineno, "ts": last_timestamp, "event": "api_404"})
                continue
            if NO_EXISTING_PR_RE.search(rest):
                pr_lookup_events.append({"line": lineno, "ts": last_timestamp, "event": "no_existing_pr_creating_new"})
                continue
            m = EXISTING_PR_FOUND_RE.search(rest)
            if m:
                pr_lookup_events.append({"line": lineno, "ts": last_timestamp, "event": "existing_pr_found",
                                          "url": m.group("url")})
                continue
            m = FIND_OPEN_PR_RE.search(rest)
            if m:
                pr_lookup_events.append({"line": lineno, "ts": last_timestamp, "event": "find_open_pr_result",
                                          "result": m.group("result")})
                continue

            if AGENT_OFFLINE_RE.search(rest):
                correlated_push = (pending_push_line is not None and
                                    lineno - pending_push_line <= FLAP_CORRELATION_WINDOW_LINES)
                agent_flaps.append({"line": lineno, "ts": last_timestamp, "event": "offline",
                                     "near_push_line": pending_push_line if correlated_push else None})
                continue
            if AGENT_BACK_RE.search(rest):
                agent_flaps.append({"line": lineno, "ts": last_timestamp, "event": "back_online"})
                continue

            m = NOT_SERIALIZABLE_RE.search(rest)
            if m:
                exceptions.append({"line": lineno, "ts": last_timestamp, "type": "NotSerializableException",
                                    "class": m.group("cls")})
                continue
            m = CAUSED_RE.match(rest)
            if m:
                exceptions.append({"line": lineno, "ts": last_timestamp, "type": "Caused", "detail": m.group("exc")})
                continue

            m = FINISHED_RE.match(rest)
            if m:
                finished_line = {"line": lineno, "ts": last_timestamp, "result": m.group("result")}
                continue
            m = CURRENT_BUILD_RESULT_RE.search(rest)
            if m:
                current_build_result_lines.append({"line": lineno, "ts": last_timestamp,
                                                     "result": m.group("result")})
                continue

    return {
        "stages": stages,
        "changed_status": changed_status,
        "git_events": git_events,
        "update_pr_calls": update_pr_calls,
        "update_pr_results": update_pr_results,
        "bundle_target_lines": bundle_target_lines,
        "pr_lookup_events": pr_lookup_events,
        "agent_flaps": agent_flaps,
        "exceptions": exceptions,
        "finished_line": finished_line,
        "current_build_result_lines": current_build_result_lines,
    }


def summarize_flaps(agent_flaps):
    offline_events = [e for e in agent_flaps if e["event"] == "offline"]
    correlated = [e for e in offline_events if e["near_push_line"] is not None]
    return {
        "total_offline_events": len(offline_events),
        "total_back_online_events": len([e for e in agent_flaps if e["event"] == "back_online"]),
        "first_offline": offline_events[0] if offline_events else None,
        "last_offline": offline_events[-1] if offline_events else None,
        "offline_events_near_a_git_push": correlated,
    }


def filter_by_repo(report, repo):
    if not repo:
        return report
    out = dict(report)
    out["changed_status"] = [e for e in report["changed_status"] if repo in e["label"]]
    out["update_pr_calls"] = [e for e in report["update_pr_calls"] if repo in e["repo"]]
    out["update_pr_results"] = [e for e in report["update_pr_results"] if repo in e["target"]]
    return out


def print_human_report(report, repo_filter, show_branches):
    print("=" * 78)
    print("NVMGeneration.groovy pipeline log forensics report")
    print("=" * 78)

    if repo_filter:
        print(f"(filtered to repo containing: {repo_filter!r})\n")

    top_level_stages = [s for s in report["stages"] if not s["name"].startswith("Branch: ")]
    branch_stages = [s for s in report["stages"] if s["name"].startswith("Branch: ")]
    shown = report["stages"] if show_branches else top_level_stages

    print(f"\n--- Stage timeline ({len(top_level_stages)} top-level stages"
          + (f", {len(branch_stages)} parallel branches hidden (use --show-branches to show)"
             if not show_branches and branch_stages else
             f", {len(branch_stages)} parallel branches" if branch_stages else "")
          + ") ---")
    for s in shown:
        name = s["name"]
        if len(name) > MAX_STAGE_NAME_LEN:
            name = name[:MAX_STAGE_NAME_LEN] + "... (truncated, see --json for full text)"
        print(f"  L{s['line']:>7}  {s['ts'] or '':<24} {name}")

    print(f"\n--- Per-repo 'changed status' (from 'Commit and create PR' stage) ---")
    if not report["changed_status"]:
        print("  (none found)")
    for e in report["changed_status"]:
        print(f"  L{e['line']:>7}  {e['ts'] or '':<24} {e['label']} {e['value']}")

    print(f"\n--- Git commit/push events ---")
    if not report["git_events"]:
        print("  (none found)")
    for e in report["git_events"]:
        if e["event"] == "commit":
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} git commit invoked")
        elif e["event"] == "committed_sha":
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} committed {e['sha']} on {e['branch']}")
        elif e["event"] == "push":
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} git push origin {e['branch']}")
        elif e["event"] == "push_result":
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} pushed {e['old_sha']}..{e['new_sha']} -> {e['branch']}")
        elif e["event"] == "push_noop":
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} push was a no-op (Everything up-to-date)")

    print(f"\n--- PR existence lookups (createPR()/findOpenPullRequestByBranch()) ---")
    if not report["pr_lookup_events"]:
        print("  (none found)")
    for e in report["pr_lookup_events"]:
        if e["event"] == "api_404":
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} *** GitHub API returned 404 on a PR-list lookup ***")
        elif e["event"] == "no_existing_pr_creating_new":
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} -> createPR() concluded 'no existing PR', will CREATE A NEW ONE")
        elif e["event"] == "existing_pr_found":
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} -> createPR() found existing PR: {e['url']}")
        elif e["event"] == "find_open_pr_result":
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} findOpenPullRequestByBranch() => {e['result']}")

    print(f"\n--- Bundle checker-status update attempts ---")
    if not report["bundle_target_lines"]:
        print("  (none found - bundle resolution never ran or logging differs)")
    for e in report["bundle_target_lines"]:
        status_str = f" status={e['status']}" if e.get("status") else ""
        print(f"  L{e['line']:>7}  {e['ts'] or '':<24}{status_str} targets: {e['targets']}")

    print(f"\n--- update_pr.py invocations ---")
    if not report["update_pr_calls"]:
        print("  (none found)")
    for e in report["update_pr_calls"]:
        print(f"  L{e['line']:>7}  {e['ts'] or '':<24} repo={e['repo']} action={e['action']} status={e['status']} pr={e['pr_id']}")
    print(f"\n--- update_pr.py results ---")
    if not report["update_pr_results"]:
        print("  (none found)")
    for e in report["update_pr_results"]:
        flag = "" if e["outcome"] == "succeeded" else "  *** FAILED ***"
        print(f"  L{e['line']:>7}  {e['ts'] or '':<24} {e['outcome']:>9} for {e['target']}{flag}")

    flap_summary = summarize_flaps(report["agent_flaps"])
    print(f"\n--- Jenkins CPS agent stability ---")
    print(f"  offline events: {flap_summary['total_offline_events']}  "
          f"back-online events: {flap_summary['total_back_online_events']}")
    if flap_summary["first_offline"]:
        print(f"  first offline: L{flap_summary['first_offline']['line']} @ {flap_summary['first_offline']['ts']}")
        print(f"  last  offline: L{flap_summary['last_offline']['line']} @ {flap_summary['last_offline']['ts']}")
    if flap_summary["offline_events_near_a_git_push"]:
        print(f"  *** {len(flap_summary['offline_events_near_a_git_push'])} offline event(s) occurred within "
              f"{FLAP_CORRELATION_WINDOW_LINES} lines of a 'git push' (createPR() PR-creation path). ***")
        print(f"  This is the known JsonSlurper()/LazyMap NotSerializableException signature - see "
              f"../references/known-issues.md#1-jsonslurper-vs-jsonslurperclassic. Confirm by checking "
              f"the 'Exceptions / serialization errors' section below.")

    print(f"\n--- Exceptions / serialization errors ---")
    if not report["exceptions"]:
        print("  (none found)")
    for e in report["exceptions"]:
        if e["type"] == "NotSerializableException":
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} NotSerializableException: {e['class']}"
                  + ("  *** matches known JsonSlurper()/LazyMap bug - see references/known-issues.md ***"
                     if "LazyMap" in e["class"] else ""))
        else:
            print(f"  L{e['line']:>7}  {e['ts'] or '':<24} Caused: {e['detail']}")

    print(f"\n--- Final build result ---")
    for e in report["current_build_result_lines"]:
        print(f"  L{e['line']:>7}  {e['ts'] or '':<24} Current Build Result (mid-pipeline): {e['result']}")
    if report["finished_line"]:
        fl = report["finished_line"]
        print(f"  L{fl['line']:>7}  {fl['ts'] or '':<24} Finished: {fl['result']}")
    else:
        print("  (no 'Finished:' line found - log may be truncated, or build is still running)")

    print("\n" + "=" * 78)
    print("Reminder: this report only extracts evidence. Reason over it (or hand it to the")
    print("agent) using the 'Diagnostic Procedure' in SKILL.md before concluding WHY a")
    print("checker did or didn't turn red - do not treat any single flag above as proof")
    print("on its own.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log_file", help="Path to the Jenkins console log (plain text)")
    ap.add_argument("--repo", help="Filter per-repo sections to repo names containing this substring")
    ap.add_argument("--show-branches", action="store_true",
                     help="Include parallel-branch sub-stages (e.g. 'Branch: Run <image>') in the "
                          "stage timeline. These can be very numerous/long (parallel job keys are "
                          "sometimes entire shell commands); hidden by default for readability.")
    ap.add_argument("--json", action="store_true", help="Emit the full structured report as JSON instead of text")
    args = ap.parse_args()

    report = parse_log(args.log_file)
    report = filter_by_repo(report, args.repo)

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
    else:
        print_human_report(report, args.repo, args.show_branches)


if __name__ == "__main__":
    main()
