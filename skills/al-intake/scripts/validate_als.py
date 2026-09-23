#!/usr/bin/env python3
"""
al-intake step 1: validate Autoload (AL) CSV changes against their CRIF.

For each AL file:
  1. Resolve its environment root (.../<env>/ALs/... -> <env>) and CRIF
     (.../<env>/crifs/*.bz2), unless --crif is given explicitly.
  2. Erase the corresponding crifs/mini_crif/<al>_mini_crif.bz2 cache file
     (validate_auto_load.py regenerates it fresh on the next run).
  3. Run validate_auto_load.py -c <crif> -al <al_file> and classify the
     result as PASS/FAIL from its own "Validation of '...' succeeded!!!"
     marker (never just the process exit code, which is 0 even when the
     script reports "succeeded" after only printing warnings).

Usage:
    python3 validate_als.py <al_file> [<al_file> ...] [options]
    python3 validate_als.py --git-changed <repo_root> [--since <git-rev>] [options]

Examples:
    # Explicit list of ALs (mirrors the VS Code "AL validation" launch config)
    python3 validate_als.py \\
        /home/sleonarx/cnic/NSC/MGVL_800_EMULATION_A0/R32/FLASH_s/ALs/ECQ/ecq_init.nvm.csv

    # Every AL/mini_crif touched by uncommitted changes in a repo
    python3 validate_als.py --git-changed /home/sleonarx/cnic

    # Every AL touched by the last 2 commits
    python3 validate_als.py --git-changed /home/sleonarx/cnic --since HEAD~2

    # Force a specific CRIF instead of auto-resolving (only valid with one AL file)
    python3 validate_als.py <al_file> --crif <path/to/crif.csv.bz2>
"""
import argparse
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from al_intake_common import find_env_root, git_changed_files, mini_crif_path_for, resolve_crif

# Candidate locations for validate_auto_load.py, checked in order unless
# --validator or $NVM_VALIDATOR overrides it.
DEFAULT_VALIDATOR_CANDIDATES = [
    "/home/sleonarx/nvm-gen/NVMGenerator/validate_auto_load.py",
    "/home/sleonarx/projects/ggl_repo_name_repo/sources/imc/nvm-generator/NVMGenerator/validate_auto_load.py",
    "/home/sleonarx/projects/row_repo_name_repo/sources/imc/nvm-generator/NVMGenerator/validate_auto_load.py",
]
DEFAULT_PYTHONPATH = "/home/sleonarx/.local/lib/python3.12/site-packages"

SUCCESS_RE = re.compile(r"Validation of '.*' succeeded!!!")
# Benign, high-volume warnings that are expected noise, not failures - never
# treated as errors but also stripped from the printed tail so real problems
# aren't buried under thousands of these lines.
BENIGN_WARNING_RE = re.compile(
    r"has invalid AUTO_LOAD_EXT value|"
    r"Only relevant in the case where there is only ONE autoload|"
    r"and missing:|"
    r"^Register '.*' in block '"
)


def find_validator(explicit):
    if explicit:
        return explicit
    env = os.environ.get("NVM_VALIDATOR")
    if env:
        return env
    for c in DEFAULT_VALIDATOR_CANDIDATES:
        if os.path.isfile(c):
            return c
    raise SystemExit(
        "Could not locate validate_auto_load.py. Pass --validator explicitly "
        "or set $NVM_VALIDATOR."
    )


def run_one(al_path, crif_path, validator, pythonpath, log_dir, timeout):
    env_root = find_env_root(al_path)
    crifs_dir = os.path.join(env_root, "crifs")
    mini_crif = mini_crif_path_for(al_path, crifs_dir)

    erased = False
    if os.path.exists(mini_crif):
        os.remove(mini_crif)
        erased = True

    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath
    cmd = ["python3", validator, "-c", crif_path, "-al", al_path]

    log_path = os.path.join(log_dir, os.path.basename(al_path) + ".log")
    lines = []
    print(f"\n=== Validating {al_path}\n    CRIF: {crif_path}\n"
          f"    mini_crif {'erased' if erased else '(none existed)'}: {mini_crif}")

    start = time.time()
    proc = subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        for line in proc.stdout:
            lines.append(line)
            if not BENIGN_WARNING_RE.search(line):
                print("    " + line.rstrip())
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        lines.append(f"\n*** TIMEOUT after {timeout}s - process killed ***\n")

    elapsed = time.time() - start
    with open(log_path, "w") as fh:
        fh.writelines(lines)

    full_text = "".join(lines)
    passed = bool(SUCCESS_RE.search(full_text)) and proc.returncode == 0
    return {
        "al": al_path,
        "crif": crif_path,
        "passed": passed,
        "elapsed": elapsed,
        "log": log_path,
        "returncode": proc.returncode,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("al_files", nargs="*", help="Explicit AL CSV file path(s)")
    ap.add_argument("--git-changed", metavar="REPO_ROOT",
                    help="Auto-discover AL files from git changes in this repo")
    ap.add_argument("--since", metavar="REV",
                    help="With --git-changed: compare REV..HEAD instead of the working tree")
    ap.add_argument("--crif", help="Force this CRIF for all given AL files (skip auto-resolution; "
                                    "only sensible with a single AL file)")
    ap.add_argument("--validator", help="Path to validate_auto_load.py (default: auto-detect)")
    ap.add_argument("--pythonpath", default=DEFAULT_PYTHONPATH,
                    help=f"PYTHONPATH for the validator subprocess (default: {DEFAULT_PYTHONPATH})")
    ap.add_argument("--log-dir", default="/tmp/al_intake_logs",
                    help="Directory to write full per-AL logs (default: /tmp/al_intake_logs)")
    ap.add_argument("--timeout", type=int, default=1800,
                    help="Per-AL subprocess timeout in seconds (default: 1800; large CRIFs "
                         "like lanpe.csv.bz2 can legitimately take ~10 minutes)")
    ap.add_argument("--stop-on-failure", action="store_true")
    args = ap.parse_args()

    al_files = list(args.al_files)
    if args.git_changed:
        al_files += [
            f for f in git_changed_files(args.git_changed, since=args.since)
            if "/ALs/" in f and f.endswith(".csv")
        ]
    al_files = sorted(set(al_files))

    if not al_files:
        raise SystemExit("No AL files given (pass paths, or use --git-changed).")
    if args.crif and len(al_files) > 1:
        print("WARNING: --crif applies to every AL file below; make sure that's intended.",
              file=sys.stderr)

    validator = find_validator(args.validator)
    os.makedirs(args.log_dir, exist_ok=True)

    results = []
    for al in al_files:
        try:
            crif = args.crif or resolve_crif(al, os.path.join(find_env_root(al), "crifs"))
        except ValueError as e:
            results.append({"al": al, "crif": None, "passed": False, "elapsed": 0,
                             "log": None, "returncode": None, "error": str(e)})
            print(f"\n=== SKIPPING {al}: {e}")
            if args.stop_on_failure:
                break
            continue

        res = run_one(al, crif, validator, args.pythonpath, args.log_dir, args.timeout)
        results.append(res)
        if not res["passed"] and args.stop_on_failure:
            break

    print("\n" + "=" * 100)
    print(f"{'Result':<6} {'AL file':<70} {'Elapsed':>8}")
    print("-" * 100)
    n_pass = 0
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        n_pass += r["passed"]
        elapsed = f"{r['elapsed']:.0f}s" if r.get("elapsed") else "-"
        print(f"{status:<6} {r['al']:<70} {elapsed:>8}")
        if not r["passed"]:
            detail = r.get("error") or f"see log: {r.get('log')}"
            print(f"       -> {detail}")
    print("-" * 100)
    print(f"{n_pass}/{len(results)} passed")

    sys.exit(0 if n_pass == len(results) else 1)


if __name__ == "__main__":
    main()
