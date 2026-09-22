#!/usr/bin/env python3
"""
Generate HIF-MC (Host Interface Master Control) autoload CSVs via hifmc_db.py
and copy the resulting *.nvm.csv files into the correct destination repo.

Mirrors the "Run hifmc script" + "Copying files" stages of CachingPipeline.groovy.

Usage:
    python3 generate_hifmc_als.py <target_repo_path> [options]

Examples:
    python3 generate_hifmc_als.py /home/sleonarx/mmg
    python3 generate_hifmc_als.py /home/sleonarx/mmg-800 --mmg800-targets latest,R28
    python3 generate_hifmc_als.py /home/sleonarx/cnic --dry-run
    python3 generate_hifmc_als.py /home/sleonarx/mmg --skip-generate
"""
import argparse
import filecmp
import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Source repos that contain hifmc_db.py, and which --topology values each
# one is responsible for generating.
# ---------------------------------------------------------------------------
DEFAULT_GGL_REPO = "/home/sleonarx/projects/ggl_repo_name_repo/sources/imc/hif-shared/hifmc_db"
DEFAULT_ROW_REPO = "/home/sleonarx/projects/row_repo_name_repo/sources/imc/hif-shared/hifmc_db"

# topology -> (source repo key, whether the topology suffix should be
# stripped from filenames when copied to the destination)
TOPOLOGY_INFO = {
    "mmg":  {"repo": "ggl", "strip_suffix": True},
    "lwc":  {"repo": "row", "strip_suffix": False},
    "bts":  {"repo": "row", "strip_suffix": False},
    "row":  {"repo": "row", "strip_suffix": False},
    "fnic": {"repo": "row", "strip_suffix": False},
}

GEN_ARGS = ["headless", "--generate-autoloads", "--generate-headers", "--generate-memory-buffer"]


class RepoProfile:
    """Describes which topologies a destination repo needs and where the
    generated files land inside it."""

    def __init__(self, name):
        self.name = name

    def topologies(self):
        raise NotImplementedError

    def destinations(self, repo_root, topology, args):
        """Return a list of destination directories (inside repo_root) that
        should receive the *.nvm.csv files generated for `topology`."""
        raise NotImplementedError


class MmgProfile(RepoProfile):
    """MMG (400G) repo: HW + SIMICS, both share the same 'latest' stepping."""

    def topologies(self):
        return ["mmg", "lwc"]

    def destinations(self, repo_root, topology, args):
        return [
            os.path.join(repo_root, "MMG", "HW", "latest", "FLASH_s", "ALs", "HIF"),
            os.path.join(repo_root, "MMG", "SIMICS", "latest", "FLASH_s", "ALs", "HIF"),
        ]


class Mmg800Profile(RepoProfile):
    """MMG800 repo: EMULATION/<R...>. SIMICS/vp<N> is a symlink into the
    matching EMULATION/R<N>, so it never needs a separate copy."""

    def topologies(self):
        return ["mmg"]

    def destinations(self, repo_root, topology, args):
        emulation_dir = os.path.join(repo_root, "MMG800", "EMULATION")
        targets = resolve_mmg800_targets(emulation_dir, args.mmg800_targets)
        return [os.path.join(emulation_dir, t, "FLASH_s", "ALs", "HIF") for t in targets]


class CnicProfile(RepoProfile):
    """CNIC repo: EMULATION_A0 + SIMICS_A0, both at their 'latest' stepping.
    row/fnic land in their own subfolder; bts lands directly under HIF/."""

    def topologies(self):
        return ["row", "bts", "fnic"]

    def destinations(self, repo_root, topology, args):
        bases = [
            os.path.join(repo_root, "NSC", "CNIC_800_EMULATION_A0", "latest"),
            os.path.join(repo_root, "NSC", "CNIC_800_SIMICS_A0", "latest"),
        ]
        subfolder = {"row": "row", "fnic": "fnic", "bts": ""}[topology]
        return [os.path.join(b, "FLASH_s", "ALs", "HIF", subfolder) for b in bases]


PROFILES = {
    "mmg": MmgProfile("mmg"),
    "mmg-800": Mmg800Profile("mmg-800"),
    "cnic": CnicProfile("cnic"),
}

EXCLUDED_REPOS = {"mev"}


def resolve_mmg800_targets(emulation_dir, spec):
    """spec is 'latest' (default), 'all', or a comma separated list like 'R27,R28'."""
    if spec == "latest":
        return ["latest"]
    if spec == "all":
        if not os.path.isdir(emulation_dir):
            return ["latest"]
        return sorted(
            d for d in os.listdir(emulation_dir)
            if d.startswith("R") and os.path.isdir(os.path.join(emulation_dir, d))
        )
    return [t.strip() for t in spec.split(",") if t.strip()]


def identify_profile(repo_path):
    base = os.path.basename(os.path.normpath(repo_path)).lower()
    if base in EXCLUDED_REPOS:
        sys.exit(
            f"Repo '{repo_path}' (mev) is intentionally excluded from HIF-MC AL "
            f"generation \u2014 it is not a destination in CachingPipeline.groovy's "
            f"'Copying files' stage. Aborting."
        )
    if base in PROFILES:
        return PROFILES[base]
    sys.exit(
        f"Don't know how to handle repo '{repo_path}' (basename '{base}'). "
        f"Expected one of: {', '.join(sorted(PROFILES))}."
    )


def run_generation(source_repo, topology, dry_run):
    if not os.path.isfile(os.path.join(source_repo, "hifmc_db.py")):
        sys.exit(f"hifmc_db.py not found under '{source_repo}'.")
    cmd = [sys.executable, "hifmc_db.py", "--topology", topology] + GEN_ARGS
    print(f"[generate] {topology}: {' '.join(cmd)}  (cwd={source_repo})")
    if dry_run:
        print("  (dry-run, skipping)")
        return
    result = subprocess.run(cmd, cwd=source_repo, capture_output=True, text=True)
    if result.returncode != 0:
        tail_out = "\n".join(result.stdout.strip().splitlines()[-20:])
        tail_err = "\n".join(result.stderr.strip().splitlines()[-20:])
        msg = (
            f"hifmc_db.py --topology {topology} failed (exit {result.returncode}).\n"
            f"--- stdout (tail) ---\n{tail_out}\n--- stderr (tail) ---\n{tail_err}"
        )
        if "ModuleNotFoundError" in result.stderr or "ModuleNotFoundError" in result.stdout:
            msg += (
                "\n\nHint: install missing deps with:\n"
                "  python3 -m pip install --user tabulate future"
            )
        sys.exit(msg)
    print(f"  done.")


def strip_topology_suffix(filename, topology):
    suffix = f"_{topology}"
    stem, ext1 = os.path.splitext(filename)          # e.g. ('hif_acc_rp_mmg.nvm', '.csv')
    stem2, ext2 = os.path.splitext(stem)              # ('hif_acc_rp_mmg', '.nvm')
    if stem2.endswith(suffix):
        stem2 = stem2[: -len(suffix)]
        return stem2 + ext2 + ext1
    return filename


def copy_files(output_dir, dest_dir, topology, strip_suffix, dry_run):
    """Copy *.nvm.csv files from output_dir into dest_dir. Returns
    (new_count, updated_count, unchanged_count) or None if output_dir/dest_dir
    doesn't apply."""
    if not os.path.isdir(output_dir):
        print(f"  [skip] source output dir does not exist: {output_dir}")
        return 0, 0, 0
    csv_files = sorted(f for f in os.listdir(output_dir) if f.endswith(".nvm.csv"))
    if not csv_files:
        print(f"  [skip] no *.nvm.csv files found in: {output_dir}")
        return 0, 0, 0

    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)
    elif not os.path.isdir(dest_dir):
        print(f"  [dry-run] would create directory: {dest_dir}")

    new_count = updated_count = unchanged_count = 0
    for fname in csv_files:
        dest_name = strip_topology_suffix(fname, topology) if strip_suffix else fname
        src_path = os.path.join(output_dir, fname)
        dst_path = os.path.join(dest_dir, dest_name)

        if os.path.exists(dst_path):
            if filecmp.cmp(src_path, dst_path, shallow=False):
                unchanged_count += 1
                continue
            updated_count += 1
            status = "UPDATED"
        else:
            new_count += 1
            status = "NEW"

        print(f"    [{status}] {dst_path}")
        if not dry_run:
            shutil.copy2(src_path, dst_path)

    return new_count, updated_count, unchanged_count


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo_path", help="Destination repo to generate HIF-MC ALs for (mmg, mmg-800, or cnic)")
    parser.add_argument("--ggl-repo", default=DEFAULT_GGL_REPO, help="Path to the hifmc_db source repo for topology 'mmg'")
    parser.add_argument("--row-repo", default=DEFAULT_ROW_REPO, help="Path to the hifmc_db source repo for topologies lwc/bts/row/fnic")
    parser.add_argument("--mmg800-targets", default="latest",
                         help="For mmg-800 destinations only: 'latest' (default), 'all', or a comma list like 'R27,R28'")
    parser.add_argument("--skip-generate", action="store_true", help="Skip running hifmc_db.py; reuse existing output_<topology> dirs")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen without generating or copying anything")
    args = parser.parse_args()

    repo_path = os.path.abspath(args.repo_path)
    if not os.path.isdir(repo_path):
        sys.exit(f"Target repo path does not exist: {repo_path}")

    profile = identify_profile(repo_path)
    source_repos = {"ggl": args.ggl_repo, "row": args.row_repo}

    print(f"=== Generating HIF-MC ALs for '{profile.name}' repo: {repo_path} ===")

    topologies = profile.topologies()
    print(f"Required topologies: {', '.join(topologies)}")

    generated_output_dirs = {}
    for topology in topologies:
        info = TOPOLOGY_INFO[topology]
        source_repo = source_repos[info["repo"]]
        if not args.skip_generate:
            run_generation(source_repo, topology, args.dry_run)
        else:
            print(f"[skip-generate] {topology}: reusing existing output_{topology}")
        generated_output_dirs[topology] = os.path.join(source_repo, f"output_{topology}")

    totals = {"new": 0, "updated": 0, "unchanged": 0}
    for topology in topologies:
        info = TOPOLOGY_INFO[topology]
        output_dir = generated_output_dirs[topology]
        dest_dirs = profile.destinations(repo_path, topology, args)
        print(f"\n[copy] topology '{topology}' -> {len(dest_dirs)} destination(s)")
        for dest_dir in dest_dirs:
            print(f"  -> {dest_dir}")
            n, u, s = copy_files(output_dir, dest_dir, topology, info["strip_suffix"], args.dry_run)
            totals["new"] += n
            totals["updated"] += u
            totals["unchanged"] += s

    print(
        f"\n=== Summary: {totals['new']} new, {totals['updated']} updated, "
        f"{totals['unchanged']} unchanged file(s) across {len(topologies)} "
        f"topolog{'y' if len(topologies) == 1 else 'ies'} ==="
    )
    if args.dry_run:
        print("(dry-run: no files were actually generated or written)")

    note_gitignore(profile.name)


def note_gitignore(profile_name):
    if profile_name in ("mmg", "mmg-800"):
        print(
            "\nNote: HIF AL filenames are typically listed in this repo's .gitignore, "
            "so `git status` will show them as ignored ('!!'). This is expected."
        )
    elif profile_name == "cnic":
        print(
            "\nNote: in the cnic repo these files are NOT gitignored, so `git status` "
            "will show new/changed files as untracked ('??') or modified ('M')."
        )


if __name__ == "__main__":
    main()
