#!/usr/bin/env python3
"""
Extract sensitive registers from each IP's CSV (bz2-compressed) in regs-defs-sydk/MMG/A0
and write one CSV per IP under sensitive_csr/.

A field is "sensitive" when its Access matches one of the following patterns
(a trailing '*' is a wildcard suffix):
  RO/C/V
  RO/CR*
  RO/SR*
  RW/CR*
  RW/SR*
  RW/O/CR*
  RW/O/SR*

Only fields whose Access matches one of these patterns are emitted, so plain
RW / RO / WO fields are never included.

An optional AL filter retains only registers present in the NVM Autoload CSVs
(BlockName/RegisterName matched against CRIF registerFile/Register).

Usage:
    python3 extract_sensitive_registers.py [--src <path>] [--out <path>] [--als <path>]

Defaults (run from workspace root, e.g. /home/tyassin/HAS_AI):
    --src   regs-defs-sydk/MMG/A0
    --out   sensitive_csr
    --als   nvm-generator-config-mmg/MMG/HW/latest/FLASH_s/ALs
            nvm-generator-config-mmg/MMG/HW/latest/FS_s/ALs
             (pass empty string "" to disable the AL filter)
"""

import argparse
import bz2
import csv
import glob
import os
import re
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
    "Security_PolicyGroup",
    "Register_Description",
]

# A field is sensitive when its Access matches one of these patterns.
# A trailing '*' means "followed by anything".
SENSITIVE_ACCESS_PATTERNS = [
    "RO/C/V",
    "RO/CR*",
    "RO/SR*",
    "RW/CR*",
    "RW/SR*",
    "RW/O/CR*",
    "RW/O/SR*",
]


def _compile_access_patterns(patterns):
    regexes = []
    for p in patterns:
        # Escape the literal parts, then turn a trailing '*' into '.*'.
        if p.endswith("*"):
            rx = re.escape(p[:-1]) + ".*"
        else:
            rx = re.escape(p)
        regexes.append(re.compile("^" + rx + "$", re.IGNORECASE))
    return regexes


SENSITIVE_ACCESS_REGEXES = _compile_access_patterns(SENSITIVE_ACCESS_PATTERNS)


def is_sensitive_access(access: str) -> bool:
    a = (access or "").strip()
    return any(rx.match(a) for rx in SENSITIVE_ACCESS_REGEXES)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract sensitive registers (by Access type) from AL CSV files."
    )
    parser.add_argument(
        "--src",
        default=os.path.join(os.getcwd(), "regs-defs-sydk/MMG/A0"),
        help="Directory containing *.csv.bz2 IP files (default: <cwd>/regs-defs-sydk/MMG/A0)",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(os.getcwd(), "sensitive_csr"),
        help="Output directory for per-IP CSV files (default: <cwd>/sensitive_csr)",
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

            # Emit only fields whose Access matches one of the sensitive
            # patterns, optionally filtered to registers present in the AL set.
            sensitive_rows = [
                row
                for (rf, reg), fields in reg_fields.items()
                if (not al_set or (rf, reg) in al_set)
                for row in fields
                if is_sensitive_access(row.get("Access", ""))
            ]

            if not sensitive_rows:
                skipped += 1
                if os.path.exists(out_path):
                    os.remove(out_path)
                continue

            with open(out_path, "w", newline="", encoding="utf-8") as out_f:
                writer = csv.DictWriter(out_f, fieldnames=OUT_COLUMNS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(sensitive_rows)

            unique_regs = len({r.get("Register", "") for r in sensitive_rows})
            processed += 1
            print(
                f"  [{processed:>3}] {ip_name}: "
                f"{unique_regs} sensitive registers ({len(sensitive_rows)} fields) "
                f"-> {os.path.basename(out_path)}"
            )

        except Exception as e:
            print(f"  ERROR {ip_name}: {e}")

    print(f"\nDone. {processed} IPs had sensitive registers (CSVs written), {skipped} IPs had none.")


if __name__ == "__main__":
    main()
