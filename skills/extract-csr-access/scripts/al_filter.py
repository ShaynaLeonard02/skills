#!/usr/bin/env python3
"""
Shared utility: load the set of (BlockName, RegisterName) pairs present in
NVM Autoload (AL) CSV files so that CRIF-extracted registers can be filtered
to only those that are actually programmed at boot.

AL CSV files begin with '@'-prefixed metadata lines followed by a standard
CSV header: BlockName, RegisterName, AddressWidth, FieldName, ...

Maps directly to CRIF columns:  BlockName  -> registerFile
                                 RegisterName -> Register
"""

import csv
import os
import sys


def load_al_register_set(als_dir: str) -> set:
    """
    Recursively walk *als_dir*, parse every ``*.nvm.csv`` and ``*.csv`` file
    (skipping ``@``-prefixed metadata lines), and return a ``set`` of
    ``(block_name, register_name)`` tuples.

    Returns an empty set (without raising) when *als_dir* does not exist so
    callers can decide whether to warn/skip.
    """
    al_set = set()
    if not os.path.isdir(als_dir):
        return al_set

    for root, _dirs, files in os.walk(als_dir):
        for fname in sorted(files):
            if fname.endswith(".tar.gz"):
                continue
            if not (fname.endswith(".nvm.csv") or fname.endswith(".csv")):
                continue
            fpath = os.path.join(root, fname)
            try:
                # utf-8-sig strips a leading BOM (\xef\xbb\xbf) that some AL
                # files carry; without this the first "@IPName:…" line begins
                # with the BOM bytes and startswith("@") misses it.
                with open(fpath, "r", encoding="utf-8-sig", errors="replace") as f:
                    # Skip metadata lines: those starting with "@" and the
                    # bare "DeviceName:…" lines that lack the "@" prefix.
                    data_lines = [
                        line for line in f
                        if not line.startswith("@") and not line.startswith("DeviceName:")
                    ]
                reader = csv.DictReader(data_lines)
                for row in reader:
                    bn = row.get("BlockName", "").strip()
                    rn = row.get("RegisterName", "").strip()
                    # Skip any row whose BlockName looks like a stray metadata
                    # token (e.g. contains ":" but no "/").
                    if bn and rn and ("/" in bn or bn == "BlockName"):
                        al_set.add((bn, rn))
            except Exception as exc:
                print(f"  [al_filter] WARNING: could not read {fpath}: {exc}", file=sys.stderr)

    return al_set


def load_al_register_sets(als_dirs) -> set:
    """
    Union the (BlockName, RegisterName) sets from one or more AL directories.

    *als_dirs* may be a single path string or an iterable of path strings.
    Directories that do not exist are silently skipped (a warning is printed to stderr).
    """
    if isinstance(als_dirs, str):
        als_dirs = [als_dirs]

    combined: set = set()
    for d in als_dirs:
        d = d.strip()
        if not d:
            continue
        if not os.path.isdir(d):
            print(f"  [al_filter] WARNING: directory not found, skipped: {d}", file=sys.stderr)
            continue
        combined |= load_al_register_set(d)
    return combined
