#!/usr/bin/env python3
"""
Detect address-range overlaps between IPs using chip-level Physical Addresses.

Strategy
--------
For every IP file the register addressOffsets are converted to chip PAs by
adding the IP's Start PA from the official address-map CSV (export.csv):

    chip_PA = Start_PA  + crif_offset          (LOCAL  method)
    chip_PA = 0x2000000000 + crif_offset       (GLOBAL method, for IPs whose
                                                 CRIF already encodes the
                                                 subsystem-relative address)

The correct method is determined automatically:  if the LOCAL-computed range
falls within the IP's declared window in export.csv, LOCAL is used; otherwise
GLOBAL is tried.  IPs that have no entry in the address map fall back to the
old ABSOLUTE-threshold heuristic (min_addr ≥ 0x100000).

For every pair of mapped IP files whose chip-PA ranges intersect, one row is
written to the output CSV.

Usage:
    python3 check_addr_conflicts.py [--map <path>] [--src <path>] [--out <path>]

Defaults (run from workspace root, e.g. /home/tyassin/HAS_AI):
    --map   export.csv
    --src   cnic-regs-defs-sydk
    --out   cnic_address_overlap_report.csv
"""

import argparse, bz2, csv, io, glob, os, sys
from collections import defaultdict

CHIP_BASE     = 0x2000_0000_00   # NMF functional-space base address
ABS_THRESHOLD = 0x100_000        # fallback: ≥ 1 MB → treat as chip-global

# ─── CRIF directory → export.csv block name ──────────────────────────────────
# "ip_group/sub_mod" checked first; "ip_group" is the fallback.
# None = skip (not in the address map).
CRIF_TO_BLOCK: dict[str, str | None] = {
    "bsr/bss":            "BSS",
    "bsr/pr":             "Packet Router or PR",
    "cosq/ecq":           "ECQ",
    "cosq/icq":           "ICQ",
    "cosq/msecv3":        "MACSEC",
    "rdma/rdma":          "RDMA",
    "rdma/rdma_pf":       "RDMA-PF BARs",
    "rdma/rdma_pf_bar0":  "RDMA-PF BARs",
    "rdma/rdma_vf":       "RDMA-VF BARs",
    "rdma/rdma_vf_bar0":  "RDMA-VF BARs",
    "ate":                    "ATE BAR",
    "cxp":                    "CXP",
    "fxp":                    "FXP",
    "hif":                    "HIF",
    "hif-nocss":              "HIF",
    "hif-pcie-gen6-phy-18a":  None,
    "ice":                    "ICE",
    "imc":                    None,
    "ipr":                    "IPR",
    "lanpe":                  "LAN",
    "nlf":                    "NLF Embedded Fabric",
    "nmc":                    None,
    "pkb":                    "Packet Builder or PB",
    "rdma":                   "RDMA",
    "ts":                     "TS",
}

_WANTED = {v for v in CRIF_TO_BLOCK.values() if v}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Detect chip-PA address-range overlaps between IPs."
    )
    parser.add_argument(
        "--map",
        default=os.path.join(os.getcwd(), "export.csv"),
        help="Address-map CSV with Start PA / End PA columns (UTF-16, tab-separated). "
             "Default: <cwd>/export.csv",
    )
    parser.add_argument(
        "--src",
        default=os.path.join(os.getcwd(), "cnic-regs-defs-sydk"),
        help="Root directory containing IP sub-dirs with *.csv.bz2 files. "
             "Default: <cwd>/cnic-regs-defs-sydk",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(os.getcwd(), "cnic_address_overlap_report.csv"),
        help="Output CSV report path. Default: <cwd>/cnic_address_overlap_report.csv",
    )
    return parser.parse_args()


def parse_hex_str(s: str) -> int | None:
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


def get_block(ip_group: str, sub_mod: str) -> str | None:
    key = f"{ip_group}/{sub_mod}"
    if key in CRIF_TO_BLOCK:
        return CRIF_TO_BLOCK[key]
    return CRIF_TO_BLOCK.get(ip_group)


# ─── Phase 1: parse the address-map CSV ──────────────────────────────────────

def _extract_block_name(raw: str) -> str | None:
    for line in raw.split("\n"):
        if line.strip() in _WANTED:
            return line.strip()
    return None


def parse_address_map(map_path: str) -> dict[str, list[tuple[int, int]]]:
    """Returns block_name → list of (start_pa, end_pa)."""
    if not os.path.exists(map_path):
        print(f"  [WARN] Address map not found: {map_path} — falling back to heuristic mode",
              file=sys.stderr)
        return {}

    try:
        enc = "utf-16"
        open(map_path, encoding=enc).read(1)
    except UnicodeError:
        enc = "utf-8"

    result: dict[str, list[tuple[int, int]]] = defaultdict(list)
    with open(map_path, encoding=enc) as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)
        for row in reader:
            if len(row) < 5:
                continue
            raw_block = row[1] if len(row) > 1 else ""
            start_pa  = parse_hex_str(row[3]) if len(row) > 3 else None
            end_pa    = parse_hex_str(row[4]) if len(row) > 4 else None
            if start_pa is None or end_pa is None:
                continue
            block = _extract_block_name(raw_block)
            if block:
                result[block].append((start_pa, end_pa))
    return dict(result)


# ─── Phase 2: scan every file – compute chip-PA range per file ───────────────

def _resolve_chip_range(
    min_crif: int, max_crif: int,
    windows: list[tuple[int, int]],
) -> tuple[int, int, str]:
    """
    Given CRIF min/max offsets and the IP's declared windows, return
    (abs_min, abs_max, method) where method is 'LOCAL' or 'GLOBAL'.

    LOCAL  : chip_PA = min(start_pa) + crif_offset
    GLOBAL : chip_PA = CHIP_BASE    + crif_offset
    """
    min_start = min(s for s, e in windows)

    # LOCAL
    lo_l = min_start + min_crif
    hi_l = min_start + max_crif
    if any(s <= lo_l <= e for s, e in windows):
        return lo_l, hi_l, "LOCAL"

    # GLOBAL
    lo_g = CHIP_BASE + min_crif
    hi_g = CHIP_BASE + max_crif
    if any(s <= lo_g <= e for s, e in windows):
        return lo_g, hi_g, "GLOBAL"

    # Neither validated — return LOCAL anyway (will be excluded from overlap check)
    return lo_l, hi_l, "UNVERIFIED"


def load_file_ranges(base: str, addr_map: dict) -> list[dict]:
    files = sorted(glob.glob(base + "/**/*.csv.bz2", recursive=True))
    print(f"[*] Source : {base}")
    print(f"[*] Found {len(files)} CSV.bz2 files – scanning …", flush=True)

    file_ranges: list[dict] = []

    for idx, fpath in enumerate(files, 1):
        ip_group, sub_mod = ip_key(fpath, base)
        addrs: list[int] = []
        try:
            data   = bz2.open(fpath, "rt", errors="replace").read()
            reader = csv.DictReader(io.StringIO(data))
            for row in reader:
                addr = parse_hex_str((row.get("addressOffset") or "").strip())
                if addr is not None:
                    addrs.append(addr)
        except Exception as e:
            print(f"  [WARN] {fpath}: {e}", file=sys.stderr)
            continue

        if not addrs:
            continue

        min_crif = min(addrs)
        max_crif = max(addrs)

        block   = get_block(ip_group, sub_mod)
        windows = addr_map.get(block) if block else None

        if windows:
            abs_min, abs_max, method = _resolve_chip_range(min_crif, max_crif, windows)
            start_pa = hex(min(s for s, e in windows))

            # Clip the computed chip range to the union of the declared windows.
            # This removes the "gap" between non-contiguous windows (e.g. HIF has
            # two windows with RDMA in between) so no false cross-block overlaps
            # arise from the gap region.
            chip_segments = [
                (max(abs_min, s), min(abs_max, e))
                for s, e in windows
                if max(abs_min, s) <= min(abs_max, e)
            ]
            if not chip_segments:
                # Computed range outside every declared window — keep but mark
                method = "UNVERIFIED"
                chip_segments = [(abs_min, abs_max)]
        else:
            # Fallback: no map entry
            if min_crif >= ABS_THRESHOLD:
                abs_min, abs_max, method = min_crif, max_crif, "HEURISTIC_ABS"
            else:
                abs_min, abs_max, method = min_crif, max_crif, "HEURISTIC_LOCAL_SKIP"
            start_pa = "N/A"
            chip_segments = [(abs_min, abs_max)]

        file_ranges.append({
            "ip_group":      ip_group,
            "sub_mod":       sub_mod,
            "file":          fpath.replace(base + "/", ""),
            "block":         block or "—",
            "start_pa":      start_pa,
            "crif_min":      min_crif,
            "crif_max":      max_crif,
            "abs_min":       min(lo for lo, hi in chip_segments),
            "abs_max":       max(hi for lo, hi in chip_segments),
            "chip_segments": chip_segments,   # list of clipped (lo, hi) per window
            "method":        method,
        })

        if idx % 20 == 0:
            print(f"  … processed {idx}/{len(files)}", flush=True)

    # Summary
    by_method: dict[str, int] = defaultdict(int)
    for m in file_ranges:
        by_method[m["method"]] += 1
    for meth, cnt in sorted(by_method.items()):
        print(f"[*] Method {meth:25s} : {cnt} files")

    return file_ranges


# ─── Phase 3: find chip-PA range overlaps ────────────────────────────────────

def find_overlaps(file_ranges: list[dict]) -> list[dict]:
    # Only compare files with a validated chip PA (exclude LOCAL_SKIP and UNVERIFIED)
    candidates = [
        m for m in file_ranges
        if m["method"] not in ("HEURISTIC_LOCAL_SKIP", "UNVERIFIED")
    ]
    candidates.sort(key=lambda m: m["abs_min"])

    overlaps: list[dict] = []

    for i, a in enumerate(candidates):
        for b in candidates[i + 1:]:
            # Skip same-block: sub-modules of the same IP legitimately share a window.
            if a["block"] == b["block"]:
                continue

            # Check every segment pair for a cross-block chip-PA overlap.
            for lo_a, hi_a in a["chip_segments"]:
                for lo_b, hi_b in b["chip_segments"]:
                    if lo_b <= hi_a and lo_a <= hi_b:
                        ov_lo   = max(lo_a, lo_b)
                        ov_hi   = min(hi_a, hi_b)
                        ov_size = ov_hi - ov_lo + 1
                        overlaps.append({
                            "ip_a":               a["ip_group"],
                            "sub_mod_a":          a["sub_mod"],
                            "block_a":            a["block"],
                            "start_pa_a":         a["start_pa"],
                            "chip_range_a_start": hex(lo_a),
                            "chip_range_a_end":   hex(hi_a),
                            "method_a":           a["method"],
                            "file_a":             a["file"],
                            "ip_b":               b["ip_group"],
                            "sub_mod_b":          b["sub_mod"],
                            "block_b":            b["block"],
                            "start_pa_b":         b["start_pa"],
                            "chip_range_b_start": hex(lo_b),
                            "chip_range_b_end":   hex(hi_b),
                            "method_b":           b["method"],
                            "file_b":             b["file"],
                            "overlap_start":      hex(ov_lo),
                            "overlap_end":        hex(ov_hi),
                            "overlap_size_bytes": ov_size,
                        })

    return overlaps


# ─── Phase 4: write CSV report ───────────────────────────────────────────────

def write_report(overlaps: list[dict], out_path: str):
    fieldnames = [
        "ip_a", "sub_mod_a", "block_a", "start_pa_a",
        "chip_range_a_start", "chip_range_a_end", "method_a", "file_a",
        "ip_b", "sub_mod_b", "block_b", "start_pa_b",
        "chip_range_b_start", "chip_range_b_end", "method_b", "file_b",
        "overlap_start", "overlap_end", "overlap_size_bytes",
    ]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(overlaps)
    print(f"[*] Report written → {out_path}  ({len(overlaps)} overlap pairs)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args     = parse_args()
    addr_map = parse_address_map(args.map)

    if addr_map:
        print(f"[*] Address map : {args.map}  ({len(addr_map)} blocks loaded)")
    else:
        print("[*] No address map — using ABSOLUTE-threshold heuristic only")

    file_ranges = load_file_ranges(args.src, addr_map)

    print(f"\n  Chip-PA ranges per IP file (sorted by absolute start):")
    for m in sorted((m for m in file_ranges if m["method"] != "HEURISTIC_LOCAL_SKIP"),
                    key=lambda m: m["abs_min"]):
        print(f"    [{hex(m['abs_min']):>16} – {hex(m['abs_max']):>16}]"
              f"  {m['ip_group']}/{m['sub_mod']:30s}"
              f"  base={m['start_pa']}  [{m['method']}]")

    overlaps = find_overlaps(file_ranges)

    print(f"\n{'='*70}")
    if not overlaps:
        print("  No chip-PA address-range overlaps found.")
        print(f"{'='*70}")
    else:
        print(f"  Found {len(overlaps)} overlapping IP pairs:\n")
        for ov in overlaps:
            print(f"    {ov['ip_a']}/{ov['sub_mod_a']}  ∩  {ov['ip_b']}/{ov['sub_mod_b']}"
                  f"  → [{ov['overlap_start']} – {ov['overlap_end']}]"
                  f"  ({ov['overlap_size_bytes']:,} bytes)")
        print(f"{'='*70}")

    write_report(overlaps, args.out)


if __name__ == "__main__":
    main()

