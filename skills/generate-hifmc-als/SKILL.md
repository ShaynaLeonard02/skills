---
name: generate-hifmc-als
description: 'Generate HIF-MC (Host Interface Master Control) autoload CSVs via hifmc_db.py and copy them into the correct destination repo (mmg, mmg-800, or cnic). Use when asked to: generate the hifmc als, regenerate HIF autoloads, run hifmc_db.py, refresh HIF ALs for a repo, update hif_acc/hif_pcir/hif_car_rst autoload CSVs.'
argument-hint: "<target_repo_path> [--dry-run] [--skip-generate] [--mmg800-targets latest|all|R27,R28]"
---

# Generate HIF-MC Autoloads (ALs)

Runs `hifmc_db.py` to (re)generate HIF Master Control autoload CSVs and copies the resulting
`*.nvm.csv` files into the right place inside a destination repo. Mirrors the "Run hifmc script"
+ "Copying files" stages of `CachingPipeline.groovy`.

## When to Use

- "generate the hifmc als for the repo `<path>`"
- "regenerate HIF autoloads for mmg / mmg-800 / cnic"
- "refresh the hif_acc_rp / hif_pcir_config_space / hif_car_rst_hifmc_* CSVs"

## Repo → Topology → Destination Map

| Destination repo basename | Topologies generated | Source repo (has `hifmc_db.py`) | Destination paths |
|---|---|---|---|
| `mmg` | `mmg` (suffix stripped), `lwc` (suffix kept) | ggl repo (`.../hif-shared/hifmc_db`) | `MMG/HW/latest/FLASH_s/ALs/HIF`, `MMG/SIMICS/latest/FLASH_s/ALs/HIF` |
| `mmg-800` | `mmg` (suffix stripped) | ggl repo | `MMG800/EMULATION/<target>/FLASH_s/ALs/HIF` (default target: `latest`) |
| `cnic` | `row`, `bts`, `fnic` (suffix kept) | row repo (`.../hif-shared/hifmc_db`) | `NSC/CNIC_800_{EMULATION,SIMICS}_A0/latest/FLASH_s/ALs/HIF/{row,,fnic}` (bts has no subfolder) |
| `mev` | — | — | **Excluded intentionally** — not a destination in `CachingPipeline.groovy`'s copy stage |

Only `*.nvm.csv` files are copied (never `.bin`/`.h` — those stay in the source `output_<topology>/` dir).

Notes:
- `mmg-800/MMG800/SIMICS/vp<N>` is a **symlink** into `EMULATION/R<N>`, so copying into
  `EMULATION/<target>` automatically satisfies the SIMICS view — no separate copy needed.
- HIF AL filenames are `.gitignore`d in `mmg`/`mmg-800` (show as `!!` in `git status` — expected).
  In `cnic` they are **not** ignored (show as `M`/`??`).

## Usage / Procedure

1. Ensure deps are installed once per environment: `python3 -m pip install --user tabulate future`
   (pillow/jsonschema are normally already present).
2. Run the script, pointing it at the target repo. `<skill-dir>` below means wherever this skill
   is installed (e.g. `.github/skills/generate-hifmc-als` — see the parent
   [skills/README.md](../README.md) for the install helper):

```bash
python3 <skill-dir>/scripts/generate_hifmc_als.py <target_repo_path>
```

Examples:

```bash
# Regenerate + copy into mmg (HW + SIMICS, mmg + lwc topologies)
python3 <skill-dir>/scripts/generate_hifmc_als.py /home/sleonarx/mmg

# Regenerate + copy into mmg-800, targeting both R27 and R28 (not just 'latest')
python3 <skill-dir>/scripts/generate_hifmc_als.py /home/sleonarx/mmg-800 --mmg800-targets latest,R28

# Regenerate + copy into cnic (row/bts/fnic topologies)
python3 <skill-dir>/scripts/generate_hifmc_als.py /home/sleonarx/cnic

# Preview only, no generation or file writes
python3 <skill-dir>/scripts/generate_hifmc_als.py /home/sleonarx/mmg --dry-run

# Reuse already-generated output_<topology> dirs, only redo the copy step
python3 <skill-dir>/scripts/generate_hifmc_als.py /home/sleonarx/mmg --skip-generate
```

3. Review the printed summary (`N new, N updated, N unchanged` files). The script diffs existing
   destination files by content before overwriting, so unchanged files are left untouched (no
   needless mtime churn).
4. If asked for `mev`, refuse and explain it is intentionally excluded (see table above).
5. After copying, remind the user that no commit/push is performed automatically — review
   `git status` in the destination repo(s) before committing.

## Extending

If a new destination repo or topology is added, edit
[generate_hifmc_als.py](./scripts/generate_hifmc_als.py):
- Add a topology to `TOPOLOGY_INFO` (source repo key + whether to strip the topology suffix).
- Add/adjust a `RepoProfile` subclass and register it in `PROFILES` for a new destination repo.
