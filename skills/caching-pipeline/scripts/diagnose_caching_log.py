#!/usr/bin/env python3
"""
Forensics helper for CachingPipeline.groovy Jenkins console logs.

Extracts the facts needed to explain "why did the nightly caching build fail / not
open a PR / open a PR I shouldn't merge": stage timeline, per-repo step selection
(skip logic), per-index symlink state, pre-flight AL verification, every
generate / generate_anvm / generate_super_image failure (and the -caching_f retry),
git/PR outcomes, and known failure signatures (see ../references/known-issues.md).

It deliberately does NOT render a verdict - read the evidence, then reason (see
SKILL.md "Usage / Procedure").

Usage:
    python3 diagnose_caching_log.py <log_file> [--repo CNIC|MMG800] [--json]

Examples:
    python3 diagnose_caching_log.py "/home/sleonarx/inputs/caching_4521.log"
    python3 diagnose_caching_log.py build.log --repo MMG800
    python3 diagnose_caching_log.py build.log --json > report.json
"""
import argparse
import json
import re
import sys

TIMESTAMP_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+Z)\]\s?(?P<rest>.*)$")
STAGE_RE = re.compile(r"^\[Pipeline\] \{ \((?P<name>.+)\)\s*$")

PROCESSING_RE = re.compile(r"^=== Processing (?P<repo>CNIC|MMG800) Config ===")
FOLDER_STEPS_RE = re.compile(
    r"^(?P<repo>CNIC|MMG800) folder (?P<folder>\S+): all steps=\[(?P<all>.*)\], historicalSkips=\[(?P<skips>.*)\]"
)
FOLDER_SKIP_RE = re.compile(r"^Folder (?P<folder>\S+): historical targets to skip=\[(?P<skips>.*)\], current=(?P<cur>.*)$")
STEPS_AFTER_RE = re.compile(r"^(?P<repo>CNIC|MMG800) steps per folder \(after skip logic\): (?P<val>.*)$")
MAX_STEPS_RE = re.compile(r"^(?P<repo>CNIC|MMG800) max steps: (?P<n>\d+)")
SYMLINK_HDR_RE = re.compile(r"^\[Caching\] (?P<repo>CNIC|MMG800) index (?P<i>\d+) symlink state:")
SYMLINK_ROW_RE = re.compile(r"^\s+(?P<path>\S+) -> (?P<target>\S+)\s*$")

VERIFY_OK_RE = re.compile(r"^\[Verify:(?P<label>[^\]]+)\] All (?P<n>\d+) required AL files present under (?P<dir>\S+)")
VERIFY_BAD_RE = re.compile(
    r"^ERROR: \[Verify:(?P<label>[^\]]+)\] (?P<n>\d+) required AL file\(s\) missing under (?P<dir>\S+) \(symlink target: (?P<target>[^)]*)\)"
)
COPIED_RE = re.compile(r"^\[Copy:(?P<label>[^\]]+)\] Copied into (?P<dir>\S+) \((?P<n>\d+) entries\)")
HIFMC_WARN_RE = re.compile(r"^WARNING: hifmc (?P<topo>\S+) failed with status (?P<st>\d+)")

CMD_FAIL_RE = re.compile(r"^ERROR: (?P<cmd>generate_super_image|generate_anvm|generate) FAILED for (?P<what>.+)$")
ANVM_RETRY_RE = re.compile(r"^WARNING: generate_anvm FAILED for image group=(?P<grp>\S+) \(cached path\)")
ANVM_RETRY_FAIL_RE = re.compile(r"^ERROR: generate_anvm FAILED for image group=(?P<grp>\S+) \(also on -caching_f retry\)")
FULL_CMD_RE = re.compile(r"^ERROR: (?P<kind>Full command|Retry command): (?P<cmd>.+)$")
ITER_FAIL_RE = re.compile(r"^ERROR: (?P<repo>CNIC|MMG800) iteration index (?P<i>\d+) failed: (?P<msg>.*)$")

PR_EVENT_PATTERNS = [
    (re.compile(r"^PR created: \S+"), "created"),
    (re.compile(r"^(PR already exists for \S+ in \S+|Found existing PR: \S+)"), "existing"),
    (re.compile(r"Failed to create PR for \S+"), "failed"),
    (re.compile(r"Content identical to \S+ \(deterministic generation\)\. Skipping PR\."), "identical_skip"),
    (re.compile(r"^\S+: no changes to commit"), "no_changes"),
    (re.compile(r"Clone of \S+ failed!"), "clone_failed"),
]
RESULT_RE = re.compile(r"^(?P<which>CNIC|MMG800|MEV|MMG) Result: (?P<val>.*)$")
LARGE_FILE_RE = re.compile(r"^WARNING: Unstaging large file .*: (?P<f>.+)$")
FINISHED_RE = re.compile(r"^Finished:\s*(?P<result>\w+)\s*$")
BUILD_RESULT_RE = re.compile(r"Current Build Result:\s*(?P<result>\w+)")

# (id, short name, regex, known-issues.md anchor)
SIGNATURES = [
    ("S1", "HIF AL missing (File not exists .../ALs/HIF/hif_*.nvm.csv)",
     re.compile(r"File not exists.*ALs/HIF/hif_\S*\.nvm\.csv"), "#1-hifmc-als-copied-into-the-wrong-step-directory"),
    ("S2", "Stale/incomplete caching archive (caching_files/<N>.csv missing)",
     re.compile(r"(FileNotFoundError|No such file or directory).*caching_files/\d+\.csv"), "#2-stale-or-incomplete-caching-archive"),
    ("S3", "Generator 'File not exists' (non-HIF)",
     re.compile(r"File not exists(?!.*ALs/HIF/hif_)"), "#1-hifmc-als-copied-into-the-wrong-step-directory"),
    ("S4", "Python traceback from NVM Generator",
     re.compile(r"^Traceback \(most recent call last\)"), "#2-stale-or-incomplete-caching-archive"),
    ("S5", "CRIF validation failure (-validate_crif True)",
     re.compile(r"crif.*(mismatch|not found|invalid|fail|error)|(register|field).*not found in crif", re.I),
     "#8-generate_anvm-validate_crif-failures"),
    ("S6", "Cherry-pick conflict while squashing",
     re.compile(r"(CONFLICT|could not apply|The previous cherry-pick is now empty|cherry-pick --skip)"),
     "#5-squash-cherry-pick-problems"),
    ("S7", "Push rejected (--force-with-lease)",
     re.compile(r"(\[rejected\]|stale info|failed to push some refs)"), "#6-github--auth--proxy-failures"),
    ("S8", "GitHub API auth / proxy problem",
     re.compile(r"(Bad credentials|\"message\"\s*:\s*\"(Not Found|Bad credentials|Validation Failed)\"|Could not resolve proxy|Proxy Authentication)"),
     "#6-github--auth--proxy-failures"),
    ("S9", "Disk full", re.compile(r"No space left on device"), "#7-workspace--disk--timeout"),
    ("S10", "File >100MB unstaged", LARGE_FILE_RE, "#7-workspace--disk--timeout"),
    ("S11", "Build aborted / timed out",
     re.compile(r"(Aborted by|Timeout has been exceeded|FlowInterruptedException)"),
     "#4-aborts-are-swallowed-by-the-per-index-catch"),
    ("S12", "CPS NotSerializableException", re.compile(r"NotSerializableException:\s*\S+"), "#9-cps-serialization--agent-flapping"),
    ("S13", "Agent offline (CPS flap)", re.compile(r"seems to be removed or offline"), "#9-cps-serialization--agent-flapping"),
]
MAX_HITS = 8


def split_list(s):
    return [x.strip().strip("'\"") for x in s.split(",") if x.strip()]


def record_failure(r, n, ts, repo, index, cmd, what, last_cmd):
    """The pipeline both echoes 'ERROR: ... FAILED' and calls error() with the same text, so the
    same failure appears twice a few lines apart - collapse the duplicate."""
    if last_cmd and last_cmd["cmd"] == cmd and last_cmd["what"] == what and n - last_cmd["line"] <= 6 \
            and last_cmd["repo"] == repo and last_cmd["index"] == index:
        return last_cmd
    ev = {"line": n, "ts": ts, "repo": repo, "index": index, "cmd": cmd, "what": what}
    r["cmd_failures"].append(ev)
    key = f"{repo}:{index}"
    if key in r["indices"]:
        r["indices"][key]["failures"].append(f"{cmd} {what}")
    return ev


def parse_log(path, repo_filter=None):
    r = {
        "stages": [], "processing": [], "folders": [], "skip_lines": [], "steps_after": [], "max_steps": [],
        "indices": {}, "verify": [], "copies": [], "hifmc_warnings": [], "cmd_failures": [],
        "anvm_retries": [], "iter_failures": [], "pr_events": [], "results": [], "signatures": {},
        "build_result_lines": [], "finished": None, "large_files": [],
    }
    cur_repo = None
    cur_index = None
    symlink_block = None
    last_cmd = None
    last_ts = None
    sig_hits = {s[0]: [] for s in SIGNATURES}

    # newline="\n": stray bare \r bytes must not shift line numbers vs grep -n / the view tool.
    with open(path, "r", encoding="utf-8", errors="replace", newline="\n") as f:
        for n, raw in enumerate(f, start=1):
            line = raw.rstrip("\n").rstrip("\r")
            m = TIMESTAMP_RE.match(line)
            rest = m.group("rest") if m else line
            if m:
                last_ts = m.group("ts")

            for sid, _name, rx, _anchor in SIGNATURES:
                if len(sig_hits[sid]) < MAX_HITS and rx.search(rest):
                    sig_hits[sid].append({"line": n, "text": rest.strip()[:220]})

            m = STAGE_RE.match(rest) or STAGE_RE.match(line)
            if m:
                r["stages"].append({"line": n, "ts": last_ts, "name": m.group("name")[:90]})
                symlink_block = None
                continue

            if symlink_block:
                m = SYMLINK_ROW_RE.match(rest)
                if m:
                    r["indices"][symlink_block]["symlinks"][m.group("path")] = m.group("target")
                    continue
                symlink_block = None

            m = PROCESSING_RE.match(rest)
            if m:
                cur_repo = m.group("repo")
                r["processing"].append({"line": n, "ts": last_ts, "repo": cur_repo})
                continue
            m = FOLDER_STEPS_RE.match(rest)
            if m:
                r["folders"].append({"line": n, "repo": m.group("repo"), "folder": m.group("folder"),
                                     "all": split_list(m.group("all")), "historical_skips": split_list(m.group("skips"))})
                continue
            m = FOLDER_SKIP_RE.match(rest)
            if m:
                r["skip_lines"].append({"line": n, "folder": m.group("folder"),
                                        "skips": split_list(m.group("skips")), "current": m.group("cur").strip()})
                continue
            m = STEPS_AFTER_RE.match(rest)
            if m:
                r["steps_after"].append({"line": n, "repo": m.group("repo"), "value": m.group("val")})
                continue
            m = MAX_STEPS_RE.match(rest)
            if m:
                r["max_steps"].append({"line": n, "repo": m.group("repo"), "n": int(m.group("n"))})
                continue
            m = SYMLINK_HDR_RE.match(rest)
            if m:
                cur_repo, cur_index = m.group("repo"), int(m.group("i"))
                key = f"{cur_repo}:{cur_index}"
                r["indices"][key] = {"repo": cur_repo, "index": cur_index, "line": n, "ts": last_ts,
                                     "symlinks": {}, "failures": []}
                symlink_block = key
                continue

            m = VERIFY_OK_RE.match(rest)
            if m:
                r["verify"].append({"line": n, "status": "ok", **m.groupdict()})
                continue
            m = VERIFY_BAD_RE.match(rest)
            if m:
                r["verify"].append({"line": n, "status": "missing", **m.groupdict()})
                continue
            m = COPIED_RE.match(rest)
            if m:
                r["copies"].append({"line": n, **m.groupdict()})
                continue
            m = HIFMC_WARN_RE.match(rest)
            if m:
                r["hifmc_warnings"].append({"line": n, **m.groupdict()})
                continue

            m = ANVM_RETRY_FAIL_RE.match(rest)
            if m:
                for ev in reversed(r["anvm_retries"]):
                    if ev["group"] == m.group("grp") and not ev["retry_failed"]:
                        ev["retry_failed"] = True
                        break
                last_cmd = record_failure(r, n, last_ts, cur_repo, cur_index, "generate_anvm",
                                          f"image group={m.group('grp')}", last_cmd)
                continue
            m = CMD_FAIL_RE.match(rest)
            if m:
                last_cmd = record_failure(r, n, last_ts, cur_repo, cur_index, m.group("cmd"),
                                          m.group("what"), last_cmd)
                continue
            m = ANVM_RETRY_RE.match(rest)
            if m:
                r["anvm_retries"].append({"line": n, "group": m.group("grp"), "retry_failed": False})
                continue
            m = FULL_CMD_RE.match(rest)
            if m:
                if last_cmd is not None:
                    last_cmd.setdefault("commands", []).append(f"{m.group('kind')}: {m.group('cmd')}")
                continue
            m = ITER_FAIL_RE.match(rest)
            if m:
                r["iter_failures"].append({"line": n, **m.groupdict()})
                continue

            for rx, kind in PR_EVENT_PATTERNS:
                if rx.search(rest):
                    r["pr_events"].append({"line": n, "kind": kind, "text": rest.strip()[:240]})
                    break
            m = RESULT_RE.match(rest)
            if m:
                r["results"].append({"line": n, **m.groupdict()})
                continue
            m = LARGE_FILE_RE.match(rest)
            if m:
                r["large_files"].append({"line": n, "file": m.group("f")})
                continue
            m = BUILD_RESULT_RE.search(rest)
            if m:
                r["build_result_lines"].append({"line": n, "result": m.group("result")})
            m = FINISHED_RE.match(rest)
            if m:
                r["finished"] = {"line": n, "result": m.group("result")}

    r["signatures"] = {sid: hits for sid, hits in sig_hits.items() if hits}

    if repo_filter:
        rf = repo_filter.upper()
        r["indices"] = {k: v for k, v in r["indices"].items() if v["repo"] == rf}
        for key in ("folders", "cmd_failures", "iter_failures", "steps_after", "max_steps"):
            r[key] = [x for x in r[key] if x.get("repo") == rf]
    return r


def render(r):
    out = []
    p = out.append

    p("=" * 78)
    p("CACHING PIPELINE LOG FORENSICS (facts only - reason over them, see SKILL.md)")
    p("=" * 78)

    p("\n## Stage timeline")
    for s in r["stages"]:
        p(f"  L{s['line']:>7}  {s['ts'] or '-':<26} {s['name']}")

    p("\n## Step selection (skip logic)")
    if not (r["folders"] or r["skip_lines"] or r["steps_after"]):
        p("  (no skip-logic lines found - did the Process stage run? RUN_CNIC / RUN_MMG800 params?)")
    for f in r["folders"]:
        p(f"  L{f['line']:>7} {f['repo']} {f['folder']}: all={f['all']}")
        p(f"             historical skips={f['historical_skips']}")
    for s in r["skip_lines"]:
        p(f"  L{s['line']:>7} {s['folder']}: current symlink target={s['current']!r}")
    for s in r["steps_after"]:
        p(f"  L{s['line']:>7} {s['repo']} after skip logic: {s['value']}")
    for s in r["max_steps"]:
        p(f"  L{s['line']:>7} {s['repo']} max steps (= number of per-index branches): {s['n']}")

    p("\n## Per-index symlink state & failures")
    if not r["indices"]:
        p("  (none - 0 indices ran: every step skipped, or the stage never started)")
    for k, v in r["indices"].items():
        status = "FAILED: " + "; ".join(v["failures"]) if v["failures"] else "no generate failures logged"
        p(f"  L{v['line']:>7} {k}  [{status}]")
        for path, tgt in v["symlinks"].items():
            p(f"             {path} -> {tgt}")

    p("\n## hifmc / AL placement")
    for w in r["hifmc_warnings"]:
        p(f"  L{w['line']:>7} hifmc topology {w['topo']} failed (status {w['st']})")
    if not r["hifmc_warnings"]:
        p("  (no 'WARNING: hifmc ... failed' lines - NOTE the hifmc commands end with '|| true', so "
          "hifmc failures are normally invisible; see known-issues.md #3)")
    for c in r["copies"]:
        p(f"  L{c['line']:>7} copied into {c['dir']} ({c['n']} entries)  [{c['label']}]")
    for v in r["verify"]:
        if v["status"] == "ok":
            p(f"  L{v['line']:>7} VERIFY OK   [{v['label']}] {v['n']} AL files under {v['dir']}")
        else:
            p(f"  L{v['line']:>7} VERIFY FAIL [{v['label']}] {v['n']} missing under {v['dir']} (latest -> {v['target']})")

    p("\n## Generator command failures")
    if not r["cmd_failures"]:
        p("  (none logged)")
    for f in r["cmd_failures"]:
        p(f"  L{f['line']:>7} {f['repo']} index {f['index']}: {f['cmd']} FAILED for {f['what']}")
        for c in f.get("commands", []):
            p(f"             {c[:300]}")
    if r["anvm_retries"]:
        p("  generate_anvm -caching_f retries (cached path failed first):")
        for a in r["anvm_retries"]:
            outcome = "retry ALSO FAILED" if a["retry_failed"] else "retry succeeded (cache rebuilt)"
            p(f"    L{a['line']:>7} {a['group']}: {outcome}")
    for it in r["iter_failures"]:
        p(f"  L{it['line']:>7} {it['repo']} iteration {it['i']} aborted: {it['msg'][:250]}")

    p("\n## Git / PR outcomes")
    if not (r["pr_events"] or r["results"]):
        p("  (none)")
    for e in r["pr_events"]:
        p(f"  L{e['line']:>7} [{e['kind']}] {e['text']}")
    for lf in r["large_files"]:
        p(f"  L{lf['line']:>7} [large file unstaged] {lf['file']}")
    for e in r["results"]:
        p(f"  L{e['line']:>7} {e['which']} Result: {e['val']}")

    p("\n## Known failure signatures hit (see references/known-issues.md)")
    if not r["signatures"]:
        p("  (none)")
    names = {s[0]: (s[1], s[3]) for s in SIGNATURES}
    for sid, hits in r["signatures"].items():
        name, anchor = names[sid]
        more = "+" if len(hits) >= MAX_HITS else ""
        p(f"  {sid} {name}  ->  known-issues.md{anchor}   ({len(hits)}{more} hit(s))")
        for h in hits[:3]:
            p(f"      L{h['line']:>7} {h['text']}")

    p("\n## Final status")
    p(f"  Current Build Result lines: {[b['result'] for b in r['build_result_lines']] or 'none'}")
    if r["finished"]:
        p(f"  Finished: {r['finished']['result']} (L{r['finished']['line']})")
    else:
        p("  Finished: NOT FOUND (log truncated / controller died?)")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--repo", choices=["CNIC", "MMG800", "cnic", "mmg800"], help="restrict per-index sections to one repo")
    ap.add_argument("--json", action="store_true", help="emit full structured data")
    args = ap.parse_args()
    data = parse_log(args.log, args.repo)
    if args.json:
        json.dump(data, sys.stdout, indent=2)
        print()
    else:
        print(render(data))


if __name__ == "__main__":
    main()
