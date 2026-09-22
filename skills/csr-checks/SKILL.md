---
name: csr-checks
description: 'Run address sanity checks on CNIC register definition files (cnic-regs-defs-sydk). Use when asked to: check address overlaps between IPs, find registers outside their declared IP window, detect same-address conflicts inside a single IP, verify registers are in correct address range, compare CRIF addresses against export.csv address map, find intra-IP duplicate addresses, detect address-range collisions between IP modules.'
argument-hint: "overlaps | map-check | intra  (optionally: --src <path> --map <path> --out <path>)"
---

# CSR Address Checks

Three scripts for verifying register address correctness in `cnic-regs-defs-sydk` (or any compatible register-definition tree).

| Check | What it finds | Script | Default output |
|---|---|---|---|
| IP range overlaps | Pairs of ABSOLUTE-mode IP files whose address ranges intersect | [check_addr_conflicts.py](./scripts/check_addr_conflicts.py) | `cnic_address_overlap_report.csv` |
| Out-of-range registers | Registers whose address falls outside the IP's window in the official address map | [check_crif_vs_map.py](./scripts/check_crif_vs_map.py) | `cnic_out_of_range_report.csv` |
| Intra-IP address conflicts | Different register names sharing the same address inside a single IP file | [check_intra_addr_conflicts.py](./scripts/check_intra_addr_conflicts.py) | `cnic_intra_ip_addr_conflicts.csv` |

## When to Use

- User asks to check / detect / find address overlaps between IPs
- User asks whether two IPs share or overlap the same address space
- User asks to verify registers are in the correct address range
- User wants to cross-check CRIF addresses against an official address map (export.csv)
- User asks to find registers outside their declared IP window
- User asks to find registers with duplicate / conflicting addresses within the same IP
- User asks whether any register in an IP file maps to the same address as another register

## Usage / Procedure

1. Determine which check(s) the user needs.
2. Run from the **workspace root** that contains `cnic-regs-defs-sydk` (and `export.csv`, if
   using the map-check). `<skill-dir>` below means wherever this skill is installed
   (e.g. `.github/skills/csr-checks` inside the target repo — see the parent
   [skills/README.md](../README.md) for the install helper):

```bash
cd <workspace-root>

# 1. IP address-range overlaps (uses export.csv for Start PA → chip PA conversion)
python3 <skill-dir>/scripts/check_addr_conflicts.py

# 2. Registers outside their declared window (requires export.csv address map)
python3 <skill-dir>/scripts/check_crif_vs_map.py

# 3. Intra-IP same-address conflicts
python3 <skill-dir>/scripts/check_intra_addr_conflicts.py

# Custom paths example
python3 <skill-dir>/scripts/check_addr_conflicts.py \
    --map export.csv \
    --src cnic-regs-defs-sydk \
    --out my_output/overlap_report.csv

python3 <skill-dir>/scripts/check_crif_vs_map.py \
    --map export.csv \
    --src cnic-regs-defs-sydk \
    --out my_output/out_of_range_report.csv

python3 <skill-dir>/scripts/check_intra_addr_conflicts.py \
    --src cnic-regs-defs-sydk \
    --out my_output/intra_conflicts.csv
```

3. Always report: total entries checked, count found, and output file path.
4. If the user asks about a specific IP, filter the output CSV by `ip_group` / `sub_mod` and summarise.

---

## Script 1 — IP Address-Range Overlaps (`check_addr_conflicts.py`)

Scans all `*.csv.bz2` files under `--src` recursively. For each IP, the `Start PA` from the address-map CSV (`--map`) is added to every CRIF `addressOffset` to produce the true chip Physical Address before comparing ranges.

### Address resolution

For each CRIF file the correct method is auto-detected by checking which interpretation lands within the IP's declared window:
- **LOCAL** : `chip_PA = Start_PA + crif_offset` (most IPs — offsets from IP base)
- **GLOBAL** : `chip_PA = 0x2000000000 + crif_offset` (IPs whose CRIF already encodes a subsystem-relative address, e.g. MACSEC, ATE)
- **HEURISTIC_ABS** : no address-map entry found, min offset ≥ 1 MB (treated as chip-global)
- **HEURISTIC_LOCAL_SKIP** : no map entry, small offsets — excluded from overlap check

### Output CSV columns

| Column | Meaning |
|---|---|
| `ip_a` / `ip_b` | IP group of each file (top-level directory) |
| `sub_mod_a` / `sub_mod_b` | Sub-module directory of each file |
| `block_a` / `block_b` | Matched block name from address map |
| `start_pa_a` / `start_pa_b` | Start PA used as base for each IP (hex) |
| `chip_range_a_start` / `chip_range_a_end` | Absolute chip-PA range of file A (hex) |
| `chip_range_b_start` / `chip_range_b_end` | Absolute chip-PA range of file B (hex) |
| `method_a` / `method_b` | Address resolution method used (LOCAL / GLOBAL / HEURISTIC_ABS) |
| `file_a` / `file_b` | Source `.csv.bz2` path relative to `--src` |
| `overlap_start` | Start of the overlapping sub-range (hex) |
| `overlap_end` | End of the overlapping sub-range (hex) |
| `overlap_size_bytes` | Size of the overlap in bytes (decimal) |

### Console output

- Per-file method classification summary
- Sorted chip-PA ranges for all compared files
- All overlapping pairs with overlap sub-range and byte size

### Arguments

| Argument | Default | Description |
|---|---|---|
| `--map` | `<cwd>/export.csv` | Address-map CSV (UTF-16, tab-separated) with `Start PA` / `End PA` columns |
| `--src` | `<cwd>/cnic-regs-defs-sydk` | Root directory with IP sub-dirs |
| `--out` | `<cwd>/cnic_address_overlap_report.csv` | Output CSV path |

---

## Script 2 — Registers Outside Declared Window (`check_crif_vs_map.py`)

Cross-checks every CRIF register address against the official IP address windows from an address-map CSV (`export.csv`).

### Address interpretation

For each register, two interpretations are tried:
- **LOCAL**: `absolute = block_start_PA + crif_offset`
- **GLOBAL**: `absolute = 0x2000000000 + crif_offset`

A register is flagged **OUT_OF_RANGE** only if **both** interpretations fail to land inside any of the block's declared windows.

### IP → block name mapping

The script contains a `CRIF_TO_BLOCK` dict mapping CRIF directory names to export.csv block names:

| CRIF path | Block |
|---|---|
| `ate` | ATE BAR |
| `bsr/bss` | BSS |
| `bsr/pr` | Packet Router or PR |
| `cosq/ecq` | ECQ |
| `cosq/icq` | ICQ |
| `cosq/msecv3` | MACSEC |
| `cxp` | CXP |
| `fxp` | FXP |
| `hif`, `hif-nocss` | HIF (both NMF windows) |
| `ice` | ICE |
| `ipr` | IPR |
| `lanpe` | LAN |
| `nlf` | NLF Embedded Fabric |
| `pkb` | Packet Builder or PB |
| `rdma/rdma`, `rdma/rdma_pf` | RDMA |
| `rdma/rdma_pf_bar0` | RDMA-PF BARs |
| `rdma/rdma_vf`, `rdma/rdma_vf_bar0` | RDMA-VF BARs |
| `ts` | TS |
| `imc`, `nmc`, `hif-pcie-gen6-phy-18a` | *(skipped — too complex / not in map)* |

### Output CSV columns

| Column | Meaning |
|---|---|
| `ip_group` | Top-level IP directory |
| `sub_mod` | Sub-module directory |
| `block_name` | Matched block from export.csv |
| `registerFile` | CRIF `registerFile` hierarchy path |
| `register` | Register instance name |
| `crif_offset` | Raw CRIF addressOffset (hex) |
| `computed_local` | `block_start_PA + crif_offset` (hex) |
| `computed_global` | `0x2000000000 + crif_offset` (hex) |
| `block_windows` | All declared windows for the block (`start-end;…`) |
| `source_file` | Source `.csv.bz2` path relative to `--src` |

The file is always written, even if empty (0 out-of-range registers).

### Arguments

| Argument | Default | Description |
|---|---|---|
| `--map` | `<cwd>/export.csv` | Address-map CSV (tab-separated, UTF-16) |
| `--src` | `<cwd>/cnic-regs-defs-sydk` | Root directory with IP sub-dirs |
| `--out` | `<cwd>/cnic_out_of_range_report.csv` | Output CSV path |

---

## Script 3 — Intra-IP Same-Address Conflicts (`check_intra_addr_conflicts.py`)

For every `*.csv.bz2` file, finds addresses where two or more **different** register names exist inside the same file.

### What is flagged

- `GICD.GICD_SETSPI_NSR` and `GITS_TRANSLATER.GITS_TRANSLATER` both at `0x40` in `imc/gic700_imc` → GIC700 architectural alias
- `pcie_ss0_ctrlx16_dbi_dsp_inst…` and `pcie_ss0_ctrlx16_dbi_usp_inst…` at the same addresses in `hif/snps` and `hif/hif` → PCIe DSP/USP port mirroring

### Output CSV columns

| Column | Meaning |
|---|---|
| `ip_group` | Top-level IP directory |
| `sub_mod` | Sub-module directory |
| `file` | Source `.csv.bz2` path relative to `--src` |
| `address` | Hex address where the conflict occurs |
| `register` | This register's name |
| `registerFile` | CRIF `registerFile` hierarchy path |
| `conflicting_registers` | `\|`-separated names of other registers at the same address |

One row per register involved; for a 2-way conflict at one address, two rows are written.

### Arguments

| Argument | Default | Description |
|---|---|---|
| `--src` | `<cwd>/cnic-regs-defs-sydk` | Root directory with IP sub-dirs |
| `--out` | `<cwd>/cnic_intra_ip_addr_conflicts.csv` | Output CSV path |
