#!/usr/bin/env python3
"""
create-step step 1 (discovery): compare two existing stepping trees (e.g.
MMG800/EMULATION/R27 vs MMG800/EMULATION/R28) and report every relative path
that differs, split into:
  - only-in-A / only-in-B  (file added/removed between the two revisions)
  - changed                (same relative path, different content)

Flags paths whose name/directory contains likely ACC/CMN/GIC/IMC keywords as
"[acc-candidate]". This is a HINT only, derived from naming conventions seen
in the one known hybrid-step precedent (mmg-800 R28_R29_hybrid: IMC/acc_*.csv,
IMC_PRE/, cmn_clkss_top_crif.csv.bz2). It is NOT a pre-approved override list -
the assistant must show the full diff (and the candidate subset) to the user
and have them confirm the exact set of paths/globs before build_step.py is
ever run with --yes. Keyword hits can both over- and under-match (e.g. a file
named "ice.csv.bz2" added in a newer rev is unrelated to ACC despite no
keyword match needed - it just won't be flagged, which is correct; a vendor
file that happens to contain "acc" as a substring would be flagged but might
not actually be ACC-related - use judgement, don't rubber-stamp).

Usage:
    python3 diff_steppings.py <root_a> <root_b> [--keyword acc --keyword cmn ...]

Example:
    python3 diff_steppings.py \\
        /home/sleonarx/mmg-800/MMG800/EMULATION/R27 \\
        /home/sleonarx/mmg-800/MMG800/EMULATION/R28
"""
import argparse
import filecmp
import os
import sys

DEFAULT_KEYWORDS = ["acc_", "/acc", "cmn", "gic700_imc", "gic700_gicd_acc", "imc_pre"]


def iter_files(root):
    out = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            out.add(rel)
    return out


def is_candidate(rel, keywords):
    low = rel.lower().replace(os.sep, "/")
    return any(k in low for k in keywords)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("root_a", help="first stepping root, e.g. .../EMULATION/R27")
    ap.add_argument("root_b", help="second stepping root, e.g. .../EMULATION/R28")
    ap.add_argument(
        "--keyword",
        action="append",
        default=None,
        help="case-insensitive substring flagged as an override candidate; "
        "repeatable. Default: %s" % DEFAULT_KEYWORDS,
    )
    args = ap.parse_args()

    root_a = os.path.abspath(args.root_a)
    root_b = os.path.abspath(args.root_b)
    if not os.path.isdir(root_a) or not os.path.isdir(root_b):
        print(f"error: both roots must be existing directories ({root_a}, {root_b})", file=sys.stderr)
        return 1

    keywords = [k.lower() for k in (args.keyword or DEFAULT_KEYWORDS)]
    a_files = iter_files(root_a)
    b_files = iter_files(root_b)

    only_a = sorted(a_files - b_files)
    only_b = sorted(b_files - a_files)
    common = sorted(a_files & b_files)
    changed = [
        rel
        for rel in common
        if not filecmp.cmp(os.path.join(root_a, rel), os.path.join(root_b, rel), shallow=False)
    ]

    def show(title, rels):
        print(f"\n=== {title} ({len(rels)}) ===")
        for rel in rels:
            flag = "  [acc-candidate]" if is_candidate(rel, keywords) else ""
            print(f"  {rel}{flag}")

    show(f"Only in A ({root_a})", only_a)
    show(f"Only in B ({root_b})", only_b)
    show("Changed (same relative path, different content)", changed)

    # Candidate scan runs over EVERY path in either tree, not just the ones
    # that differ - an override reason (e.g. "ACC-related") is about a file's
    # identity/ownership, not whether its content happens to already match
    # between the two revisions. Missing this would silently drop candidates
    # like an IMC_PRE/ALs file that is byte-identical in both A and B today.
    candidates = sorted({rel for rel in (a_files | b_files) if is_candidate(rel, keywords)})
    print(f"\n=== Candidate override set ({len(candidates)}) - CONFIRM WITH USER, do not auto-apply ===")
    for rel in candidates:
        where = (
            "only-A" if rel in only_a else "only-B" if rel in only_b else "changed" if rel in changed else "identical"
        )
        print(f"  {rel}  ({where})")

    print(
        "\nNOTE: 'only in A/B' entries that are NOT flagged as candidates are "
        "unrelated file-set drift between the two revisions (additions/removals "
        "unrelated to the override reason). Under a strict 'default to A' rule "
        "those B-only files are simply dropped unless the user says otherwise."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
