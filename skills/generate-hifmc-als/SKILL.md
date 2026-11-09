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

## Source Repos (hifmc_db)

| Project family | Projects | Source repo | Path inside repo | Script option |
|---|---|---|---|---|
| ggl | mmg400, mmg800, mev | `networking.ipu.firmware` | `ucode/hif-shared/hifmc_db` | `--ipu-repo` / `HIFMC_IPU_REPO` |
| row | cnic, lwc, mmgts | `firmware.ethernet.imc-fw-row` | `hif-shared/hifmc_db` | `--row-repo` / `HIFMC_ROW_REPO` |

The old ggl repo is archived — never use it. If a clone is missing at the default path, pass the
real path via the option or env var.

## Repo → Topology → Destination Map

| Destination repo basename | Topologies generated | Source repo | Destination paths |
|---|---|---|---|
| `mmg` | `mmg` (suffix stripped) from ipu; `lwc` (suffix kept) from row | `networking.ipu.firmware` + `firmware.ethernet.imc-fw-row` | `MMG/HW/latest/FLASH_s/ALs/HIF`, `MMG/SIMICS/latest/FLASH_s/ALs/HIF` |
| `mmg-800` | `mmg` (suffix stripped) | `networking.ipu.firmware` | `MMG800/EMULATION/<target>/FLASH_s/ALs/HIF` (default target: `latest`) |
| `cnic` | `row`, `bts`, `fnic` (suffix kept) | `firmware.ethernet.imc-fw-row` | `NSC/CNIC_800_{EMULATION,SIMICS}_A0/latest/FLASH_s/ALs/HIF/{row,,fnic}` (bts has no subfolder) |
| `mev` | — | `networking.ipu.firmware` (source only) | **No destination profile** — not a destination in `CachingPipeline.groovy`'s copy stage |
| `mmg-ts` | — | `firmware.ethernet.imc-fw-row` (source only) | **No destination profile yet** — add one in the script once the destination path is known |

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
4. If asked for `mev` or `mmg-ts`, explain there is no destination profile yet (see table above).
5. After copying, remind the user that no commit/push is performed automatically — review
   `git status` in the destination repo(s) before committing.

## Extending

If a new destination repo or topology is added, edit
[generate_hifmc_als.py](./scripts/generate_hifmc_als.py):
- Add a topology to `TOPOLOGY_INFO` (source repo key + whether to strip the topology suffix).
- Add/adjust a `RepoProfile` subclass and register it in `PROFILES` for a new destination repo.
