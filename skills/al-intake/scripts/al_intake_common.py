#!/usr/bin/env python3
"""
Shared helpers for the al-intake skill: locating the CRIF that matches an
Autoload (AL) CSV, deriving its mini_crif cache path, and discovering which
AL/mini_crif files changed in a repo (working tree or a range of commits).
"""
import bz2
import csv
import io
import os
import subprocess

# Known IP-folder -> CRIF-file aliases seen across MMG / MMG-800 / CNIC
# nvm-generator-config repos. Used as a fast path before falling back to a
# full content scan. Keys/values are matched case-insensitively against the
# stem of files under crifs/*.bz2 (without directory or .csv.bz2 suffix).
KNOWN_CRIF_ALIASES = {
    "ecq_top_map": "ecq",
    "icq_top_map": "icq",
    "lanpe_top_map": "lanpe",
    "ate_top_map": "ate",
    "cxp_top_map": "cxp",
    "fxp_top_map": "fxp",
    "hif_top_map": "hif",
    "hif_core_top_map": "hif",
    "hif_nocss_top_map": "hif_nocss",
    "rdma_top_map": "rdma",
    "pkb_top_map": "pkb",
    "ts_top_map": "ts",
    "ipr_top_map": "ipr",
    "bss_top_map": "bss",
    "pr_top_map": "pr",
    "physs_top_map": "physs_crif",
    "gic700_imc": "gic700_imc",
    "nlf_structure": "nlf_Structure",
}


def find_env_root(al_path):
    """Given a path .../<env_root>/ALs/<IP>/<file>.csv, return <env_root>
    (the directory that has ALs/ and crifs/ as siblings)."""
    d = os.path.abspath(os.path.dirname(al_path))
    while True:
        if os.path.basename(d) == "ALs":
            return os.path.dirname(d)
        parent = os.path.dirname(d)
        if parent == d:
            raise ValueError(
                f"Could not find an 'ALs' directory above {al_path!r}; "
                "pass --crif explicitly instead of relying on auto-resolution."
            )
        d = parent


def mini_crif_path_for(al_path, crifs_dir):
    """Mini-crif filename convention observed across all ALs: the AL's
    basename with only its final .csv extension replaced by
    '_mini_crif.bz2' (e.g. 'ecq_init.nvm.csv' -> 'ecq_init.nvm_mini_crif.bz2',
    'lantx_default.csv' -> 'lantx_default_mini_crif.bz2')."""
    stem, _ext = os.path.splitext(os.path.basename(al_path))
    return os.path.join(crifs_dir, "mini_crif", f"{stem}_mini_crif.bz2")


def _first_data_block_name(al_path):
    """Return the BlockName field of the first data row after the
    'BlockName,RegisterName,...' header line in an AL CSV."""
    with open(al_path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.reader(fh))
    header_idx = None
    for i, row in enumerate(rows):
        if row and row[0].strip() == "BlockName":
            header_idx = i
            break
    if header_idx is None or header_idx + 1 >= len(rows):
        return None
    data_row = rows[header_idx + 1]
    return data_row[0].strip() if data_row else None


def _crif_root_matches(crif_path, block_root, max_rows=5):
    """Peek at a CRIF's first few registerFile entries to see if any starts
    with block_root. Cheap early-exit content check used as a fallback."""
    try:
        with bz2.open(crif_path, "rt", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh)
            for i, row in enumerate(reader):
                if i > max_rows:
                    break
                if row and row[0].strip().startswith(block_root):
                    return True
    except OSError:
        return False
    return False


def resolve_crif(al_path, crifs_dir):
    """Resolve the *.bz2 CRIF file (directly under crifs_dir) that matches an
    AL file, using (1) a filename-alias fast path keyed off the AL's first
    BlockName root, then (2) a content scan fallback. Raises ValueError if
    nothing matches, so the caller can ask for an explicit --crif instead."""
    block_name = _first_data_block_name(al_path)
    if not block_name:
        raise ValueError(
            f"Could not read a BlockName from {al_path!r}; pass --crif explicitly."
        )
    root = block_name.split("/")[0]

    candidates = [f for f in os.listdir(crifs_dir) if f.endswith(".bz2")]
    candidates.sort()

    # Fast path: known alias table.
    alias = KNOWN_CRIF_ALIASES.get(root)
    if alias:
        for c in candidates:
            stem = c[: -len(".csv.bz2")] if c.endswith(".csv.bz2") else c[: -len(".bz2")]
            if stem == alias:
                return os.path.join(crifs_dir, c)

    # Fast path: direct substring match on the root (stripped of _top_map).
    bare_root = root.replace("_top_map", "")
    for c in candidates:
        stem = c[: -len(".csv.bz2")] if c.endswith(".csv.bz2") else c[: -len(".bz2")]
        if stem.lower() == bare_root.lower():
            return os.path.join(crifs_dir, c)

    # Fallback: content scan (first few registerFile rows of every CRIF).
    matches = []
    for c in candidates:
        full = os.path.join(crifs_dir, c)
        if _crif_root_matches(full, root):
            matches.append(full)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous CRIF match for block root {root!r} in {al_path!r}: "
            f"{matches}. Pass --crif explicitly."
        )
    raise ValueError(
        f"No CRIF under {crifs_dir!r} matches block root {root!r} "
        f"(from {al_path!r}). Pass --crif explicitly."
    )


def git_changed_files(repo_root, since=None, suffixes=(".csv", ".bz2")):
    """Return absolute paths changed either in the working tree (since=None:
    'git status --porcelain', modified/added, not deleted) or between
    <since> and HEAD ('git diff --name-only --diff-filter=ACMR <since> HEAD'),
    filtered to the given filename suffixes."""
    if since:
        out = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", since, "HEAD"],
            cwd=repo_root, capture_output=True, text=True, check=True,
        ).stdout
        rel_files = [line.strip() for line in out.splitlines() if line.strip()]
    else:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root, capture_output=True, text=True, check=True,
        ).stdout
        rel_files = []
        for line in out.splitlines():
            status, path = line[:2], line[3:].strip()
            if "D" in status:
                continue
            rel_files.append(path)
    return [
        os.path.join(repo_root, p) for p in rel_files if p.endswith(suffixes)
    ]
