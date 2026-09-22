#!/usr/bin/env python3
"""
Detect intra-IP address conflicts: registers within the same file that share
the same addressOffset but carry different register names.

For every *.csv.bz2 under --src, each address is grouped by register name.
When two or more *different* register names appear at the same address inside
the same file, all of them are written to the output CSV as a conflict group.

Output: one row per register involved in a conflict.

Usage:
    python3 check_intra_addr_conflicts.py [--src <path>] [--out <path>]

Defaults (run from workspace root, e.g. /home/tyassin/HAS_AI):
    --src   cnic-regs-defs-sydk
    --out   cnic_intra_ip_addr_conflicts.csv
"""

import argparse, bz2, csv, io, glob, os, sys
from collections import defaultdict


def parse_args():
    parser = argparse.ArgumentParser(
        description="Find registers in the same IP file that share an address."
    )
    parser.add_argument(
        "--src",
        default=os.path.join(os.getcwd(), "cnic-regs-defs-sydk"),
        help="Root directory with IP sub-dirs containing *.csv.bz2 files "
             "(default: <cwd>/cnic-regs-defs-sydk)",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(os.getcwd(), "cnic_intra_ip_addr_conflicts.csv"),
        help="Output CSV report path (default: <cwd>/cnic_intra_ip_addr_conflicts.csv)",
    )
    return parser.parse_args()


def parse_hex(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    try:
        if "'" in s:
            return int(s.split("'h")[-1], 16)
        return int(s, 16)
    except ValueError:
        return None


def ip_key(filepath: str, base: str) -> tuple[str, str]:
    rel   = filepath.replace(base + "/", "")
    parts = rel.split("/")
    return parts[0], (parts[1] if len(parts) > 2 else parts[0])


def scan_file(fpath: str, base: str) -> list[dict]:
    """
    Returns conflict rows for one CSV.bz2 file.
    One row per register involved in a same-address conflict.
    """
    ip_group, sub_mod = ip_key(fpath, base)
    rel_file = fpath.replace(base + "/", "")

    try:
        data   = bz2.open(fpath, "rt", errors="replace").read()
        reader = csv.DictReader(io.StringIO(data))
    except Exception as e:
        print(f"  [WARN] {fpath}: {e}", file=sys.stderr)
        return []

    # addr_int → {reg_name → registerFile}
    # (use first registerFile seen for a given reg_name at an address)
    addr_regs: dict[int, dict[str, str]] = defaultdict(dict)

    for row in reader:
        reg_file = (row.get("registerFile") or "").strip()
        reg_name = (row.get("Register")     or "").strip()
        ao_raw   = (row.get("addressOffset")or "").strip()
        addr = parse_hex(ao_raw)
        if addr is None or not reg_name:
            continue
        if reg_name not in addr_regs[addr]:
            addr_regs[addr][reg_name] = reg_file

    # Collect addresses with more than one distinct register name
    rows: list[dict] = []
    for addr, reg_map in addr_regs.items():
        if len(reg_map) < 2:
            continue
        all_names = sorted(reg_map.keys())
        for reg_name, reg_file in sorted(reg_map.items()):
            rows.append({
                "ip_group":            ip_group,
                "sub_mod":             sub_mod,
                "file":                rel_file,
                "address":             hex(addr),
                "register":            reg_name,
                "registerFile":        reg_file,
                "conflicting_registers": "|".join(n for n in all_names if n != reg_name),
            })

    return rows


def write_report(conflicts: list[dict], out_path: str):
    fieldnames = [
        "ip_group", "sub_mod", "file",
        "address", "register", "registerFile",
        "conflicting_registers",
    ]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(sorted(
            conflicts,
            key=lambda r: (r["ip_group"], r["sub_mod"], int(r["address"], 16))
        ))
    print(f"[*] Report written → {out_path}  ({len(conflicts)} rows)")


def main():
    args  = parse_args()
    files = sorted(glob.glob(args.src + "/**/*.csv.bz2", recursive=True))

    print(f"[*] Source : {args.src}")
    print(f"[*] Found {len(files)} CSV.bz2 files – scanning …", flush=True)

    all_conflicts: list[dict] = []

    for idx, fpath in enumerate(files, 1):
        all_conflicts.extend(scan_file(fpath, args.src))
        if idx % 20 == 0:
            print(f"  … processed {idx}/{len(files)}", flush=True)

    # ── summary ──────────────────────────────────────────────────────────────
    conflict_addrs  = len({(r["file"], r["address"]) for r in all_conflicts})
    conflict_files  = len({r["file"] for r in all_conflicts})

    print(f"\n{'='*65}")
    print(f"  Files with conflicts    : {conflict_files:6d}  / {len(files)}")
    print(f"  Conflicting addresses   : {conflict_addrs:6d}")
    print(f"  Total report rows       : {len(all_conflicts):6d}")
    print(f"{'='*65}\n")

    if all_conflicts:
        from collections import Counter
        print("  Top 15 IPs by conflicting-address count:")
        cnt = Counter((r["ip_group"], r["sub_mod"]) for r in all_conflicts)
        # each conflict address appears 2+ times, so divide to get address count
        addr_cnt: dict[tuple, set] = defaultdict(set)
        for r in all_conflicts:
            addr_cnt[(r["ip_group"], r["sub_mod"])].add(r["address"])
        for (ip, sub), addrs in sorted(addr_cnt.items(), key=lambda x: -len(x[1]))[:15]:
            print(f"    {ip}/{sub:40s}  {len(addrs):6d} addresses")

    write_report(all_conflicts, args.out)


if __name__ == "__main__":
    main()
