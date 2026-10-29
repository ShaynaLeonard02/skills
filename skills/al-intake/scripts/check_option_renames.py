#!/usr/bin/env python3
"""
al-intake step 1b: detect renamed AL option (column) names and the
meta_data/super_features entries that still reference the old names.

An AL's option columns are the header cells of the 'BlockName,...' row (e.g.
'lantxhspm_HostEP.EthMode_NtwkArb_HostEP_1_EthQoS_ptc_PaTCa_MGV800'). The
meta_data feature option files point at them with a "source_csv_col" value, so
when a column is renamed in an AL, the matching "source_csv_col" must be
updated or the option no longer resolves.

For every changed AL this script compares the header at the base revision
(HEAD for working-tree changes, or --since REV) against the current one, pairs
renamed columns by position (only when the column count is unchanged and the
same number of cells differ), and scans the meta_data directory for
"source_csv_col" values equal to a removed name.

Without --apply nothing is written (report only). --apply rewrites the exact
quoted "<old>" -> "<new>" strings in the meta_data JSON files. The calling
assistant MUST ask the user before using --apply.

Usage:
    python3 check_option_renames.py --git-changed <repo_root> [--since REV] [--meta-dir DIR] [--apply]
    python3 check_option_renames.py <al_file> [<al_file> ...] [--meta-dir DIR] [--apply]

Exit code: 0 = no rename needs attention, 2 = stale meta_data references or
unmapped removed columns found (or --apply performed), 1 = usage error.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from al_intake_common import git_changed_files

DEFAULT_META_SUBDIR = os.path.join("networking.ipu.anvm", "meta_data", "super_features")


def repo_root_of(path):
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=os.path.dirname(os.path.abspath(path)),
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def header_of(text):
    for line in text.replace("\r", "").split("\n"):
        if line.startswith("BlockName,"):
            return line.rstrip(",").split(",")
    return None


def old_header(repo_root, rel_path, rev):
    res = subprocess.run(["git", "show", f"{rev}:{rel_path}"], cwd=repo_root, capture_output=True)
    if res.returncode != 0:
        return None  # file is new at this revision
    return header_of(res.stdout.decode("utf-8-sig", errors="replace"))


def new_header(path):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        return header_of(fh.read())


def diff_columns(old, new):
    """Return (renames {old: new}, removed_unmapped [old], added_unmapped [new])."""
    removed = [c for c in old if c not in new]
    added = [c for c in new if c not in old]
    renames = {}
    if len(old) == len(new) and len(removed) == len(added) and removed:
        for a, b in zip(old, new):
            if a != b and a in removed and b in added:
                renames[a] = b
    mapped_old = set(renames)
    mapped_new = set(renames.values())
    return (renames,
            [c for c in removed if c not in mapped_old],
            [c for c in added if c not in mapped_new])


def find_meta_refs(meta_dir, names):
    """Return {name: [json_path, ...]} for "source_csv_col" values equal to a name."""
    refs = {n: [] for n in names}
    for dirpath, _dirs, files in os.walk(meta_dir):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            cols = set()

            def walk(o):
                if isinstance(o, dict):
                    v = o.get("source_csv_col")
                    if isinstance(v, str):
                        cols.add(v)
                    for x in o.values():
                        walk(x)
                elif isinstance(o, list):
                    for x in o:
                        walk(x)

            walk(data)
            for n in names:
                if n in cols:
                    refs[n].append(p)
    return refs


def apply_renames(renames, refs):
    changed = 0
    for old, paths in refs.items():
        new = renames.get(old)
        if not new:
            continue
        for p in paths:
            with open(p, encoding="utf-8", newline="") as fh:
                text = fh.read()
            updated = text.replace(f'"{old}"', f'"{new}"')
            if updated != text:
                with open(p, "w", encoding="utf-8", newline="") as fh:
                    fh.write(updated)
                changed += 1
                print(f"  UPDATED {p}\n    {old}\n    -> {new}")
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("al_files", nargs="*")
    ap.add_argument("--git-changed", metavar="REPO_ROOT")
    ap.add_argument("--since", metavar="REV", help="Compare REV..HEAD instead of HEAD..working tree")
    ap.add_argument("--meta-dir", help=f"super_features dir (default: <repo>/{DEFAULT_META_SUBDIR})")
    ap.add_argument("--apply", action="store_true",
                    help="Rewrite stale source_csv_col values. Only after the user confirmed.")
    args = ap.parse_args()

    files = list(args.al_files)
    if args.git_changed:
        files += [f for f in git_changed_files(args.git_changed, since=args.since)
                  if "/ALs/" in f and f.endswith(".csv")]
    files = sorted(set(os.path.abspath(f) for f in files))
    if not files:
        print("No AL files given (pass paths, or use --git-changed).", file=sys.stderr)
        return 1

    all_renames, unmapped_removed, per_file = {}, {}, []
    repo_root = None
    for f in files:
        root = args.git_changed and os.path.abspath(args.git_changed) or repo_root_of(f)
        repo_root = repo_root or root
        rel = os.path.relpath(f, root)
        old = old_header(root, rel, args.since or "HEAD")
        new = new_header(f)
        if old is None or new is None:
            continue  # new file or no header row: nothing to compare
        ren, rem, add = diff_columns(old, new)
        if ren or rem:
            per_file.append((rel, len(ren), rem, add))
        all_renames.update(ren)
        for c in rem:
            unmapped_removed[c] = f

    if not all_renames and not unmapped_removed:
        print("No AL option (column) names were renamed or removed.")
        return 0

    print(f"Option-name changes in {len(per_file)} AL file(s):")
    for rel, n, rem, add in per_file:
        print(f"  {rel}: {n} renamed, {len(rem)} removed/unpaired, {len(add)} added/unpaired")
    for c in unmapped_removed:
        print(f"  UNPAIRED removed column (cannot auto-map): {c}")

    meta_dir = args.meta_dir or os.path.join(repo_root, DEFAULT_META_SUBDIR)
    if not os.path.isdir(meta_dir):
        print(f"\nmeta_data dir not found: {meta_dir} (pass --meta-dir). Nothing to update.")
        return 0

    refs = find_meta_refs(meta_dir, set(all_renames) | set(unmapped_removed))
    stale = {n: p for n, p in refs.items() if p}
    print(f"\nScanned {meta_dir}")
    if not stale:
        print("No meta_data 'source_csv_col' references the old option names - nothing to update.")
        return 0

    print(f"{len(stale)} old option name(s) still referenced by meta_data:")
    for old, paths in stale.items():
        new = all_renames.get(old)
        print(f"  {old}\n    -> {new or 'NO AUTOMATIC MAPPING (manual fix needed)'}")
        for p in paths:
            print(f"    in {p}")

    if not args.apply:
        print("\nReport only. Re-run with --apply after the user confirms the meta_data update.")
        return 2

    print()
    n = apply_renames(all_renames, stale)
    print(f"\n{n} meta_data file(s) updated. No commit was made.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
