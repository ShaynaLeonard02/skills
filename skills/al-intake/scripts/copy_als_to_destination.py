#!/usr/bin/env python3
"""
al-intake step 2 (OPTIONAL, requires human confirmation every time):
propagate validated AL / mini_crif files from one environment tree (e.g. an
EMULATION_A0 stepping) into another (e.g. its SIMICS_A0/vpNN counterpart).

HARD RULE (do not weaken this): this script never guesses --dest-root or
--mode. The calling assistant MUST ask the user, every single intake, which
destination path and which method (copy vs symlink) to use - even if a
previous intake used a particular answer. Passing --dest-root/--mode is not
itself confirmation; the assistant must have actually asked the user in the
current conversation before invoking this with --yes.

Without --yes, this only PRINTS the plan (dry-run) - nothing is written.
This is deliberate: it gives the assistant a safe way to show the user
exactly what would happen before asking them to confirm.

Usage:
    python3 copy_als_to_destination.py --src-root <env_root> --dest-root <env_root> \\
        --mode copy|symlink [--yes] <file> [<file> ...]
    python3 copy_als_to_destination.py --src-root <env_root> --dest-root <env_root> \\
        --mode copy|symlink [--yes] --git-changed <repo_root> [--since <git-rev>]

Examples:
    # Show the plan only (no --yes -> dry-run)
    python3 copy_als_to_destination.py \\
        --src-root  /home/sleonarx/cnic/NSC/MGVL_800_EMULATION_A0/R32/FLASH_s \\
        --dest-root /home/sleonarx/cnic/NSC/MGVL_800_SIMICS_A0/vp32/FLASH_s \\
        --mode copy --git-changed /home/sleonarx/cnic --since HEAD~2

    # Actually perform it, after the user has confirmed path + mode
    python3 copy_als_to_destination.py \\
        --src-root  /home/sleonarx/cnic/NSC/MGVL_800_EMULATION_A0/R32/FLASH_s \\
        --dest-root /home/sleonarx/cnic/NSC/MGVL_800_SIMICS_A0/vp32/FLASH_s \\
        --mode copy --yes --git-changed /home/sleonarx/cnic --since HEAD~2
"""
import argparse
import filecmp
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from al_intake_common import git_changed_files


def plan_for(files, src_root, dest_root):
    plan = []
    src_root = os.path.abspath(src_root)
    for f in files:
        f_abs = os.path.abspath(f)
        if not f_abs.startswith(src_root + os.sep):
            plan.append({"src": f_abs, "dest": None, "error": "not under --src-root"})
            continue
        rel = os.path.relpath(f_abs, src_root)
        plan.append({"src": f_abs, "dest": os.path.join(dest_root, rel), "rel": rel})
    return plan


def do_copy(entry):
    src, dest = entry["src"], entry["dest"]
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    prior_mode = None
    if os.path.lexists(dest):
        if os.path.islink(dest):
            os.remove(dest)
        else:
            prior_mode = stat.S_IMODE(os.stat(dest).st_mode)
    with open(src, "rb") as s, open(dest, "wb") as d:
        d.write(s.read())
    if prior_mode is not None:
        os.chmod(dest, prior_mode)  # preserve destination's pre-existing mode bits
    ok = filecmp.cmp(src, dest, shallow=False)
    return ok


def do_symlink(entry, relative):
    src, dest = entry["src"], entry["dest"]
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.lexists(dest):
        os.remove(dest)
    target = os.path.relpath(src, os.path.dirname(dest)) if relative else src
    os.symlink(target, dest)
    return os.path.realpath(dest) == os.path.realpath(src)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="Explicit file path(s) under --src-root")
    ap.add_argument("--git-changed", metavar="REPO_ROOT",
                    help="Auto-discover AL/mini_crif files from git changes in this repo")
    ap.add_argument("--since", metavar="REV",
                    help="With --git-changed: compare REV..HEAD instead of the working tree")
    ap.add_argument("--src-root", required=True,
                    help="Source environment root, e.g. .../NSC/MGVL_800_EMULATION_A0/R32/FLASH_s "
                         "(MUST be confirmed with the user, not assumed)")
    ap.add_argument("--dest-root", required=True,
                    help="Destination environment root, e.g. .../NSC/MGVL_800_SIMICS_A0/vp32/FLASH_s "
                         "(MUST be confirmed with the user, not assumed)")
    ap.add_argument("--mode", required=True, choices=["copy", "symlink"],
                    help="MUST be confirmed with the user, not assumed / defaulted")
    ap.add_argument("--relative-symlink", action="store_true",
                    help="With --mode symlink: create a relative symlink instead of absolute")
    ap.add_argument("--yes", action="store_true",
                    help="Actually perform the operation. Without this flag, only the plan is "
                         "printed (dry-run). Only pass this after the user has explicitly "
                         "confirmed the destination path and method in THIS conversation.")
    args = ap.parse_args()

    files = list(args.files)
    if args.git_changed:
        files += git_changed_files(args.git_changed, since=args.since,
                                    suffixes=(".csv", ".bz2"))
    files = sorted(set(files))
    if not files:
        raise SystemExit("No files given (pass paths, or use --git-changed).")

    plan = plan_for(files, args.src_root, args.dest_root)

    print(f"Mode: {args.mode}    {'(EXECUTING)' if args.yes else '(DRY RUN - nothing will be written)'}")
    print(f"Source root:      {args.src_root}")
    print(f"Destination root: {args.dest_root}\n")

    bad = [p for p in plan if p.get("error")]
    for p in bad:
        print(f"  SKIP (not under --src-root): {p['src']}")

    good = [p for p in plan if not p.get("error")]
    for p in good:
        print(f"  {p['src']}\n    -> {p['dest']}")

    if not args.yes:
        print("\nDry-run only. Re-run with --yes once the user has confirmed "
              "the destination path and copy/symlink method for this intake.")
        return

    ok_count = 0
    for p in good:
        try:
            if args.mode == "copy":
                ok = do_copy(p)
            else:
                ok = do_symlink(p, args.relative_symlink)
        except OSError as e:
            print(f"  ERROR on {p['src']}: {e}")
            continue
        ok_count += ok
        print(f"  {'OK  ' if ok else 'FAIL'} {p['dest']}")

    print(f"\n{ok_count}/{len(good)} files applied successfully "
          f"({len(bad)} skipped, not under --src-root).")
    print("No commit was made - review `git status` in the destination repo before committing.")


if __name__ == "__main__":
    main()
