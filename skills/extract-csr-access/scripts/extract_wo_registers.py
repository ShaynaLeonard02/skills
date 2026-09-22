#!/usr/bin/env python3
"""
Extract fully Write-Only registers from MMG AL register CSV files (bz2-compressed).

A register is included only when EVERY one of its fields carries a WO access type.
Access types treated as WO: WO, WS, WS/V

An optional AL filter retains only registers present in the NVM Autoload CSVs
(BlockName/RegisterName matched against CRIF registerFile/Register).

Usage:
    python3 extract_wo_registers.py [--src <path>] [--out <path>] [--als <path>]

Defaults (run from workspace root, e.g. /home/tyassin/HAS_AI):
    --src   regs-defs-sydk/MMG/A0
    --out   write_only_csr
    --als   nvm-generator-config-mmg/MMG/HW/latest/FLASH_s/ALs
            nvm-generator-config-mmg/MMG/HW/latest/FS_s/ALs
             (pass empty string "" to disable the AL filter)
"""

import argparse
import bz2
import csv
import glob
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from al_filter import load_al_register_sets

OUT_COLUMNS = [
    "registerFile",
    "Register",
    "Field Name",
    "bitOffset",
    "bitWidth",
    "Access",
    "addressOffset",
    "Register_Description",
]

WO_ACCESS_TYPES = {"WO", "WS", "WS/V"}


def parse_args():
    parser = argparse.ArgumentParser(description="Extract fully-WO registers from AL CSV files.")
    parser.add_argument(
        "--src",
        default=os.path.join(os.getcwd(), "regs-defs-sydk/MMG/A0"),
        help="Directory containing *.csv.bz2 IP files (default: <cwd>/regs-defs-sydk/MMG/A0)",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(os.getcwd(), "write_only_csr"),
        help="Output directory for per-IP CSV files (default: <cwd>/write_only_csr)",
    )
    parser.add_argument(
        "--als",
        nargs="+",
        default=[
            os.path.join(os.getcwd(), "nvm-generator-config-mmg/MMG/HW/latest/FLASH_s/ALs"),
            os.path.join(os.getcwd(), "nvm-generator-config-mmg/MMG/HW/latest/FS_s/ALs"),
        ],
        help="One or more NVM Autoload (AL) directories for register filtering. "
             "Only CRIF registers present in any of the AL dirs are kept. "
             "Pass empty string to disable. "
             "(default: FLASH_s/ALs and FS_s/ALs under <cwd>/nvm-generator-config-mmg/MMG/HW/latest/)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    src_dir = args.src
    out_dir = args.out
    als_dirs = [d.strip() for d in (args.als or []) if d.strip()]

    # Load AL register set for filtering (union of all supplied AL dirs)
    al_set = set()
    if als_dirs:
        al_set = load_al_register_sets(als_dirs)
        for d in als_dirs:
            print(f"AL filter: {d}")
        print(f"           {len(al_set)} unique (BlockName, RegisterName) pairs loaded")
    else:
        print("AL filter: disabled")

    os.makedirs(out_dir, exist_ok=True)

    files = sorted(glob.glob(os.path.join(src_dir, "*.csv.bz2")))
    if not files:
        print(f"No *.csv.bz2 files found in: {src_dir}")
        return

    print(f"Source : {src_dir}")
    print(f"Output : {out_dir}")
    print(f"Found  : {len(files)} IP files\n")

    processed = 0
    skipped = 0

    for filepath in files:
        ip_name = os.path.basename(filepath).replace(".csv.bz2", "")
        out_path = os.path.join(out_dir, f"{ip_name}.csv")

        try:
            with bz2.open(filepath, "rt", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                all_rows = list(reader)

            # Group fields by (registerFile, Register) — using just Register as
            # the key would merge same-named registers from different paths.
            reg_fields = defaultdict(list)
            for row in all_rows:
                key = (row.get("registerFile", "").strip(), row.get("Register", "").strip())
                reg_fields[key].append(row)

            # Keep only registers where EVERY field is WO, optionally filtered
            # to those present in the AL set.
            fully_wo_rows = [
                row
                for (rf, reg), fields in reg_fields.items()
                if (not al_set or (rf, reg) in al_set)
                and all(f.get("Access", "").strip() in WO_ACCESS_TYPES for f in fields)
                for row in fields
            ]

            if not fully_wo_rows:
                skipped += 1
                if os.path.exists(out_path):
                    os.remove(out_path)
                continue

            with open(out_path, "w", newline="", encoding="utf-8") as out_f:
                writer = csv.DictWriter(out_f, fieldnames=OUT_COLUMNS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(fully_wo_rows)

            processed += 1
            print(f"  [{processed:>3}] {ip_name}: {len(fully_wo_rows)} fields -> {os.path.basename(out_path)}")

        except Exception as e:
            print(f"  ERROR {ip_name}: {e}")

    print(f"\nDone. {processed} IPs written, {skipped} IPs had no fully-WO registers.")


if __name__ == "__main__":
    main()
