#!/usr/bin/env python3
"""
create-step step 2 (build, requires human confirmation every time): compose
a new stepping directory tree (e.g. MMG800/EMULATION/R27_R28_hybrid or a new
SIMICS/vpNN) out of one base source tree plus an optional override source
tree whose files replace the base's version for a confirmed set of paths.

This generalizes both known construction patterns found in this workspace's
product repos (mmg-800 / mmg / mev / cnic):
  - SIMICS vpNN steps: every leaf file/dir is a relative symlink into a
    sibling EMULATION/R<N> tree, except a handful of env-specific real-file
    overrides (e.g. RDMA ALs, Features.json, board_id.csv, bid.json).
  - EMULATION hybrid steps (e.g. mmg-800 R28_R29_hybrid): the whole tree is
    REAL file copies of one base stepping, with ACC-related files physically
    overlaid (also as real copies) from a second, newer stepping.

HARD RULE (do not weaken this): never invoke this with --yes until the
calling assistant has explicitly asked the user, in the current
conversation, all three of: (a) the exact --dest-root, (b) --mode copy vs
symlink, and (c) the confirmed --override-glob set for THIS pair of source
steppings. Run `diff_steppings.py` first and show the user the full diff (not
just its acc-candidate hints) - override sets drift release to release and
must never be copy-pasted from a prior step without re-confirming.

Without --yes this only PRINTS the plan (dry-run) - nothing is written.

Usage:
    python3 build_step.py \\
        --base-root <dir> --dest-root <dir> --mode copy|symlink \\
        [--override-root <dir> --override-glob <glob> [--override-glob <glob> ...]] \\
        [--relative-symlink] [--yes]

Example (dry run first):
    python3 build_step.py \\
        --base-root     /home/sleonarx/mmg-800/MMG800/EMULATION/R27 \\
        --override-root /home/sleonarx/mmg-800/MMG800/EMULATION/R28 \\
        --dest-root     /home/sleonarx/mmg-800/MMG800/EMULATION/R27_R28_hybrid \\
        --mode copy \\
        --override-glob 'FLASH_s/ALs/IMC/acc_*.csv' \\
        --override-glob 'FLASH_s/ALs/IMC_PRE/*' \\
        --override-glob 'FLASH_s/crifs/cmn_clkss_top_crif.csv.bz2' \\
        --override-glob 'FLASH_s/crifs/mini_crif/acc_*_mini_crif.bz2' \\
        --override-glob 'FLASH_s/crifs/mini_crif/cmn_clkss_top_mini_crif.bz2'

Then re-run with --yes once the user has confirmed the printed plan.
"""
import argparse
import fnmatch
import os
import stat
import sys


def iter_files(root):
    rels = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            rels.add(os.path.relpath(os.path.join(dirpath, fn), root))
    return rels


def matches_any(rel, patterns):
    rel_posix = rel.replace(os.sep, "/")
    return any(fnmatch.fnmatch(rel_posix, pat) for pat in patterns)


def build_plan(base_root, override_root, override_globs):
    base_root = os.path.abspath(base_root)
    base_files = iter_files(base_root)

    override_files = set()
    overridden_rels = set()
    if override_root:
        override_root = os.path.abspath(override_root)
        override_files = iter_files(override_root)
        overridden_rels = {rel for rel in override_files if matches_any(rel, override_globs)}

    all_rels = sorted(base_files | overridden_rels)
    plan = []
    for rel in all_rels:
        if rel in overridden_rels:
            plan.append({"rel": rel, "src": os.path.join(override_root, rel), "origin": "override"})
        else:
            plan.append({"rel": rel, "src": os.path.join(base_root, rel), "origin": "base"})

    unmatched_globs = [
        g for g in override_globs if not any(fnmatch.fnmatch(r.replace(os.sep, "/"), g) for r in override_files)
    ]
    return plan, unmatched_globs


def do_copy(src, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.lexists(dest):
        os.remove(dest)
    with open(src, "rb") as s, open(dest, "wb") as d:
        d.write(s.read())
    mode = stat.S_IMODE(os.lstat(src).st_mode)
    os.chmod(dest, mode)


def do_symlink(src, dest, relative):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.lexists(dest):
        os.remove(dest)
    target = os.path.relpath(src, os.path.dirname(dest)) if relative else src
    os.symlink(target, dest)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base-root", required=True, help="default source tree (e.g. .../EMULATION/R27)")
    ap.add_argument("--override-root", default=None, help="second source tree for overridden paths (e.g. .../EMULATION/R28)")
    ap.add_argument("--dest-root", required=True, help="new stepping tree to create, e.g. .../EMULATION/R27_R28_hybrid")
    ap.add_argument("--mode", choices=["copy", "symlink"], required=True)
    ap.add_argument(
        "--override-glob",
        action="append",
        default=[],
        help="fnmatch glob (posix-style, relative to the roots) of paths to source from "
        "--override-root instead of --base-root; repeatable",
    )
    ap.add_argument("--relative-symlink", action="store_true", help="use relative instead of absolute symlink targets")
    ap.add_argument("--yes", action="store_true", help="actually write files (default: dry-run / print plan only)")
    args = ap.parse_args()

    if args.override_glob and not args.override_root:
        print("error: --override-glob requires --override-root", file=sys.stderr)
        return 1

    if not os.path.isdir(args.base_root):
        print(f"error: --base-root does not exist: {args.base_root}", file=sys.stderr)
        return 1
    if args.override_root and not os.path.isdir(args.override_root):
        print(f"error: --override-root does not exist: {args.override_root}", file=sys.stderr)
        return 1

    plan, unmatched_globs = build_plan(args.base_root, args.override_root, args.override_glob)

    base_n = sum(1 for p in plan if p["origin"] == "base")
    override_n = sum(1 for p in plan if p["origin"] == "override")
    print(f"Plan: {len(plan)} files total -> {base_n} from base, {override_n} from override")
    if override_n:
        print("\nOverride-sourced files:")
        for p in plan:
            if p["origin"] == "override":
                print(f"  {p['rel']}")
    if unmatched_globs:
        print("\nWARNING: these --override-glob patterns matched nothing in --override-root:")
        for g in unmatched_globs:
            print(f"  {g}")

    if not args.yes:
        print(f"\nDry-run only (no --yes). Would write to: {os.path.abspath(args.dest_root)}")
        print(f"Mode: {args.mode}")
        return 0

    dest_root = os.path.abspath(args.dest_root)
    for p in plan:
        dest = os.path.join(dest_root, p["rel"])
        if args.mode == "copy":
            do_copy(p["src"], dest)
        else:
            do_symlink(p["src"], dest, args.relative_symlink)

    print(f"\n{len(plan)} files written to {dest_root} ({args.mode} mode).")
    print("No commit was performed - review `git status`/`git add -N` yourself before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
