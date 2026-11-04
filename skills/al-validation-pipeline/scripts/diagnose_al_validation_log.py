#!/usr/bin/env python3
"""
Forensics helper for al_validation.groovy Jenkins console logs.

Extracts the facts needed to explain "why did the AL validation build fail / why did
I get the 'autoloads are invalid' email": stage timeline, clone_repo results, the
resolved AUTO_LOAD/OSAL GitHub refs, mini_crif removals, AL staging errors, the full
validate_auto_load.py command, the validation verdict, validator ERROR/Traceback
fragments, artifact/email/output events, and the final build result.

It deliberately does NOT render a verdict - read the evidence, then reason (see
SKILL.md "Usage / Procedure").

Usage:
    python3 diagnose_al_validation_log.py <log_file> [--json]
"""
import argparse
import json
import re
import sys

TIMESTAMP_RE = re.compile(r"^\[(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+Z)\]\s?(?P<rest>.*)$")
STAGE_RE = re.compile(r"^\[Pipeline\] \{ \((?P<name>.+)\)\s*$")

CLONE_START_RE = re.compile(
    r"^> clone_repo - Clone dir: (?P<dir>\S+) repo_name: (?P<repo>\S+) stage_name: (?P<stage>\S+) branch: (?P<branch>\S*)"
)
CLONE_OK_RE = re.compile(r"^Stage: (?P<stage>\S+) - (?P<repo>\S+) cloned successfully")
CLONE_FAIL_RE = re.compile(r"Stage: (?P<stage>\S+) - (?P<repo>\S+) clone failed!")
BAD_URL_RE = re.compile(r"Unsupported GitHub URL format: '(?P<url>[^']*)'")
NO_BRANCH_RE = re.compile(r"Could not resolve a branch for GitHub path '(?P<url>[^']*)'")
REF_RE = re.compile(r"^(?P<which>OSAL )?(?:Repository URL|Branch|Folder): (?P<val>.*)$")

UTILS_COPY_OK_RE = re.compile(r"Moving utils files into NVMGenerator was SUCCESSFUL!")
UTILS_COPY_FAIL_RE = re.compile(r"Moving utils files into NVMGenerator failed!")

MINI_CRIF_RM_RE = re.compile(r"^Folder 'mini_crif' has been removed from (?P<path>.*)$")
MINI_CRIF_NONE_RE = re.compile(r"^No folder named 'mini_crif' found in (?P<path>.*)$")

AL_STAGE_ERR_RES = [
    re.compile(r"Autoload file '(?P<path>[^']*)' is not a CSV file"),
    re.compile(r"No CSV autoload files found in '(?P<path>[^']*)'"),
    re.compile(r"Autoload file or folder '(?P<path>[^']*)' does not exist"),
]

VALIDATE_CMD_RE = re.compile(r"validate_auto_load\.py")
VERDICT_OK_RE = re.compile(r"^validate_auto_load\.py succeeded")
VERDICT_FAIL_RE = re.compile(r"validate_auto_load\.py failed!")
VALIDATOR_ERR_RE = re.compile(
    r"(^Traceback \(most recent call last\)|^\s*ERROR\b|\bFAILED\b|mismatch|invalid)", re.I
)

ARCHIVE_NAME_RE = re.compile(r"^New name: (?P<name>\S+)$")
ARCHIVING_RE = re.compile(r"^Archiving artifacts")
LOG_SAVED_RE = re.compile(r"^Log file saved to: (?P<path>.*)$")
LOG_SAVE_FAIL_RE = re.compile(r"Moving log file into (?P<path>.*) failed!")
EMAIL_RE = re.compile(r"^Email was sent to (?P<addr>.*)$")
FINISHED_RE = re.compile(r"^Finished:\s*(?P<result>\w+)\s*$")

STAGE_ORDER = [
    "cloning_repos", "copying_utils_to_nvmgenerator", "remove_mini_crif",
    "prepare_al_folders", "al_validation", "output",
]


def strip_ts(line):
    m = TIMESTAMP_RE.match(line)
    return (m.group("ts"), m.group("rest")) if m else (None, line)


def parse(path):
    report = {
        "stages": [],            # [{name, line, ts}]
        "clones": [],            # [{repo, branch, dir, line, result}]
        "url_errors": [],        # [{line, text}]
        "refs": {"AUTO_LOAD": {}, "OSAL": {}},
        "utils_copy": None,      # {result, line}
        "mini_crif": [],         # [{path, removed(bool-ish echo), line}]
        "al_staging_errors": [], # [{line, text}]
        "validate_cmd": None,    # {line, text}
        "verdict": None,         # {result, line}
        "validator_errors": [],  # [{line, text}] (between cmd start and verdict)
        "artifacts": [],         # [{line, text}]
        "log_saved": None,
        "email": None,
        "finished": None,
    }
    in_validation = False
    with open(path, errors="replace") as f:
        for n, raw in enumerate(f, 1):
            ts, line = strip_ts(raw.rstrip("\n"))

            m = STAGE_RE.match(line)
            if m:
                report["stages"].append({"name": m.group("name"), "line": n, "ts": ts})
                in_validation = m.group("name") == "al_validation"
                continue

            m = CLONE_START_RE.match(line)
            if m:
                report["clones"].append({
                    "repo": m.group("repo"), "branch": m.group("branch") or "(default)",
                    "dir": m.group("dir"), "line": n, "result": None,
                })
                continue
            m = CLONE_OK_RE.match(line) or CLONE_FAIL_RE.search(line)
            if m:
                ok = bool(CLONE_OK_RE.match(line))
                for c in reversed(report["clones"]):
                    if c["repo"] == m.group("repo") and c["result"] is None:
                        c["result"] = "ok" if ok else "FAILED"
                        c["result_line"] = n
                        break
                continue

            if BAD_URL_RE.search(line) or NO_BRANCH_RE.search(line):
                report["url_errors"].append({"line": n, "text": line.strip()})
                continue
            m = REF_RE.match(line)
            if m:
                which = "OSAL" if m.group("which") else "AUTO_LOAD"
                key = line.split(":", 1)[0].replace("OSAL ", "").strip().lower().replace(" ", "_")
                report["refs"][which][key] = m.group("val").strip()
                report["refs"][which].setdefault("line", n)
                continue

            if UTILS_COPY_OK_RE.search(line):
                report["utils_copy"] = {"result": "ok", "line": n}
                continue
            if UTILS_COPY_FAIL_RE.search(line):
                report["utils_copy"] = {"result": "FAILED", "line": n}
                continue

            m = MINI_CRIF_RM_RE.match(line)
            if m:
                report["mini_crif"].append({"path": m.group("path"), "echo": "removed (echo unreliable, see architecture.md #2)", "line": n})
                continue
            m = MINI_CRIF_NONE_RE.match(line)
            if m:
                report["mini_crif"].append({"path": m.group("path"), "echo": "not found", "line": n})
                continue

            for rx in AL_STAGE_ERR_RES:
                if rx.search(line):
                    report["al_staging_errors"].append({"line": n, "text": line.strip()})
                    break

            if report["validate_cmd"] is None and VALIDATE_CMD_RE.search(line) and (
                "-c" in line or line.lstrip().startswith("+")
            ):
                report["validate_cmd"] = {"line": n, "text": line.strip()}
                continue

            if VERDICT_OK_RE.match(line):
                report["verdict"] = {"result": "succeeded", "line": n}
                in_validation = False
                continue
            if VERDICT_FAIL_RE.search(line):
                report["verdict"] = {"result": "FAILED", "line": n}
                in_validation = False
                continue
            if in_validation and VALIDATOR_ERR_RE.search(line) and "[Pipeline]" not in line:
                if len(report["validator_errors"]) < 40:
                    report["validator_errors"].append({"line": n, "text": line.strip()[:300]})
                continue

            m = ARCHIVE_NAME_RE.match(line)
            if m:
                report["artifacts"].append({"line": n, "text": f"compiled OSAL artifact: {m.group('name')}"})
                continue
            if ARCHIVING_RE.match(line):
                report["artifacts"].append({"line": n, "text": "Archiving artifacts"})
                continue
            m = LOG_SAVED_RE.match(line)
            if m:
                report["log_saved"] = {"result": "ok", "path": m.group("path"), "line": n}
                continue
            m = LOG_SAVE_FAIL_RE.search(line)
            if m:
                report["log_saved"] = {"result": "FAILED", "path": m.group("path"), "line": n}
                continue
            m = EMAIL_RE.match(line)
            if m:
                report["email"] = {"to": m.group("addr"), "line": n}
                continue
            m = FINISHED_RE.match(line)
            if m:
                report["finished"] = {"result": m.group("result"), "line": n}
    return report


def render(r):
    out = []
    p = out.append
    p("== Stage timeline ==")
    seen = [s["name"] for s in r["stages"] if s["name"] in STAGE_ORDER]
    for s in r["stages"]:
        p(f"  L{s['line']:>7}  {s['ts'] or '':<24} {s['name']}")
    missing = [s for s in STAGE_ORDER if s not in seen]
    if missing:
        p(f"  !! stages never reached: {', '.join(missing)}")

    p("\n== clone_repo calls ==")
    if not r["clones"]:
        p("  (none found)")
    for c in r["clones"]:
        p(f"  L{c['line']:>7}  {c['repo']}  branch={c['branch']}  -> {c['result'] or 'no result line'}")
    for e in r["url_errors"]:
        p(f"  !! L{e['line']}: {e['text']}")

    p("\n== Resolved GitHub refs (empty = param was a disk path or stage not reached) ==")
    for which in ("AUTO_LOAD", "OSAL"):
        ref = {k: v for k, v in r["refs"][which].items() if k != "line"}
        p(f"  {which}: {ref if ref else '(no ref echoes)'}")

    p("\n== Utils -> NVMGenerator copy ==")
    p(f"  {r['utils_copy'] or '(no result line)'}")

    p("\n== mini_crif removals ==")
    if not r["mini_crif"]:
        p("  (no echoes found)")
    for mc in r["mini_crif"]:
        p(f"  L{mc['line']:>7}  {mc['path']}  [{mc['echo']}]")

    if r["al_staging_errors"]:
        p("\n== AL staging errors (prepare_al_folders) ==")
        for e in r["al_staging_errors"]:
            p(f"  !! L{e['line']}: {e['text']}")

    p("\n== validate_auto_load.py ==")
    if r["validate_cmd"]:
        p(f"  command (L{r['validate_cmd']['line']}): {r['validate_cmd']['text']}")
    else:
        p("  (command line not found - validation stage may not have been reached)")
    if r["verdict"]:
        p(f"  verdict (L{r['verdict']['line']}): validate_auto_load.py {r['verdict']['result']}")
    else:
        p("  verdict: NOT FOUND (build may have died before/inside al_validation)")
    if r["validator_errors"]:
        p("  validator ERROR/Traceback fragments (first 40, view +/-80 lines around each):")
        for e in r["validator_errors"]:
            p(f"    L{e['line']:>7}  {e['text']}")

    p("\n== Output stage ==")
    for a in r["artifacts"]:
        p(f"  L{a['line']:>7}  {a['text']}")
    if r["log_saved"]:
        p(f"  log.txt -> OUTPUT_PATH: {r['log_saved']}")
    p(f"  email: {r['email'] or '(no \"Email was sent\" line - output stage incomplete?)'}")

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
