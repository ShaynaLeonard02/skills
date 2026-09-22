---
name: extract-csr-access
description: 'Extract fully RO (read-only), fully WO (write-only), or sensitive registers from MMG AL register CSV files in regs-defs-sydk/MMG/A0. Use when asked to: extract RO registers, extract WO registers, list read-only CSRs, list write-only CSRs, list sensitive CSRs, find secure registers, find SecureOnly or DFD registers, generate per-IP register access CSVs, find fully RO or fully WO registers per IP.'
argument-hint: "ro | wo | sensitive | all  (optionally: --src <path> --out <path>)"
---

# Extract CSR Access Registers

Extracts registers from MMG AL IP register definition files (`regs-defs-sydk/MMG/A0`, bz2-compressed CSVs, one per IP) and writes one output CSV per IP.

| Mode | Rule | Script | Default output |
|---|---|---|---|
| Read-Only (RO) | Every field has RO access (`RO`, `RO/V`, `RO/C`) | [extract_ro_registers.py](./scripts/extract_ro_registers.py) | `read_only_csr/` |
| Write-Only (WO) | Every field has WO access (`WO`, `WS`, `WS/V`) | [extract_wo_registers.py](./scripts/extract_wo_registers.py) | `write_only_csr/` |
| Sensitive | Any field has a non-NonSecure Security_PolicyGroup | [extract_sensitive_registers.py](./scripts/extract_sensitive_registers.py) | `sensitive_csr/` |

## Sensitive Security Policy Groups (HAS Vol 1 §32.16, Table 32-211)

| Security_PolicyGroup | Meaning |
|---|---|
| `SecureOnly` | Requires secure access (interfaces, boot config, CGID) |
| `SecureWO` | Secure writes only; non-secure writes blocked |
| `DFD_L0_secure` | Debug visibility registers (OEM ECM fuse controlled) |
| `DFD_L1_secure` | Debug control (BOBs, DFS, error injection) — disabled in production |
| `DFD_INT_only` | ICE/LCE crypto key registers — Intel Internal only |
| `NonSecure` | *(not sensitive — excluded)* |

A register is sensitive when **any** field carries one of the sensitive groups above.

## When to Use
- User asks to extract / list / generate RO, WO, or sensitive registers
- User wants per-IP CSVs of read-only, write-only, or secure CSRs
- User wants to find which registers of an IP are SecureOnly, DFD-gated, or crypto-key registers

## Usage / Procedure

1. Determine what the user wants: `ro`, `wo`, `sensitive`, `conflicts`, or multiple.
2. Run the script(s) from the **workspace root** that contains `regs-defs-sydk/MMG/A0`.
   `<skill-dir>` below means wherever this skill is installed (e.g.
   `.github/skills/extract-csr-access` inside the target repo — see the parent
   [skills/README.md](../README.md) for the install helper):

```bash
cd <workspace-root>

# Read-Only registers (source: regs-defs-sydk/MMG/A0)
python3 <skill-dir>/scripts/extract_ro_registers.py

# Write-Only registers
python3 <skill-dir>/scripts/extract_wo_registers.py

# Sensitive registers
python3 <skill-dir>/scripts/extract_sensitive_registers.py

# Custom paths example
python3 <skill-dir>/scripts/extract_sensitive_registers.py \
    --src regs-defs-sydk/MMG/A0 \
    --out my_output/sensitive_csr
```

3. Report: IPs processed, CSVs written, output directory / file.
4. If the user asks about a specific IP, read its output CSV and summarise register names, count, and security groups / conflict types present.

## Output CSV Columns

RO / WO: `registerFile`, `Register`, `Field Name`, `bitOffset`, `bitWidth`, `Access`, `addressOffset`, `Register_Description`

Sensitive: same columns **plus** `Security_PolicyGroup`

One file per IP, named after the source bz2 (minus `.csv.bz2`). IPs with no matching registers produce no output file.
