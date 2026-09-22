#!/usr/bin/env python3
"""
Cross-check CRIF register addresses against the official IP address map (export.csv).

For each CRIF file the corresponding IP block's address window is looked up
in the address-map CSV.  Each register's addressOffset is verified in TWO ways:

  LOCAL  : absolute = block_start_PA + crif_offset   (IP-local offset from base)
  GLOBAL : absolute = 0x2000000000  + crif_offset    (chip-space offset)

A register is flagged OUT_OF_RANGE only when NEITHER interpretation maps
it into any of the block's declared windows.

Usage:
    python3 check_crif_vs_map.py [--map <path>] [--src <path>] [--out <path>]

Defaults (run from workspace root, e.g. /home/tyassin/HAS_AI):
    --map   export.csv
    --src   cnic-regs-defs-sydk
    --out   cnic_out_of_range_report.csv
"""

import argparse, bz2, csv, io, glob, os, sys
from collections import defaultdict

CHIP_BASE = 0x2000_0000_00   # functional-space base address

# ─── CRIF directory → export.csv block name ──────────────────────────────────
# "ip_group/sub_mod" is checked first; "ip_group" is the fallback.
# None = skip this IP (not in the address map or too complex to correlate).
CRIF_TO_BLOCK: dict[str, str | None] = {
    # ── specific sub-module overrides ──────────────────────────────────────
    "bsr/bss":            "BSS",
    "bsr/pr":             "Packet Router or PR",
    "cosq/ecq":           "ECQ",
    "cosq/icq":           "ICQ",
    "cosq/msecv3":        "MACSEC",
    "rdma/rdma":          "RDMA",
    "rdma/rdma_pf":       "RDMA-PF BARs",    # PF registers → PF BAR window
    "rdma/rdma_pf_bar0":  "RDMA-PF BARs",
    "rdma/rdma_vf":       "RDMA-VF BARs",    # VF registers → VF BAR window
    "rdma/rdma_vf_bar0":  "RDMA-VF BARs",
    # ── ip_group fallback ──────────────────────────────────────────────────
    "ate":                    "ATE BAR",
    "cxp":                    "CXP",
    "fxp":                    "FXP",
    "hif":                    "HIF",            # both HIF windows combined
    "hif-nocss":              "HIF",
    "hif-pcie-gen6-phy-18a":  None,             # PCIe PHY SRAM/CSR — skip
    "ice":                    "ICE",
    "imc":                    None,             # many IMC sub-blocks — skip
    "ipr":                    "IPR",
    "lanpe":                  "LAN",
    "nlf":                    "NLF Embedded Fabric",
    "nmc":                    None,             # NMF/ECM/SysCon etc. — skip
    "pkb":                    "Packet Builder or PB",
    "rdma":                   "RDMA",           # catch-all fallback
    "ts":                     "TS",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Cross-check CRIF registers against the official IP address map."
    )
    parser.add_argument(
        "--map",
        default=os.path.join(os.getcwd(), "export.csv"),
        help="Address-map CSV (tab-separated, UTF-16). Default: <cwd>/export.csv",
    )
    parser.add_argument(
        "--src",
        default=os.path.join(os.getcwd(), "cnic-regs-defs-sydk"),
        help="Root directory with IP sub-dirs containing *.csv.bz2 files. "
             "Default: <cwd>/cnic-regs-defs-sydk",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(os.getcwd(), "cnic_out_of_range_report.csv"),
        help="Output CSV report path. Default: <cwd>/cnic_out_of_range_report.csv",
    )
    return parser.parse_args()


def parse_hex(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    try:
        if "'" in s:
            return int(s.split("'h")[-1], 16)
        elif s.lower().startswith("0x"):
            return int(s, 16)
        else:
            return int(s, 16)   # export.csv addresses have no 0x prefix
    except ValueError:
        return None


# ─── Phase 1: parse the address-map CSV ──────────────────────────────────────

# All block names we need to locate in export.csv
_WANTED = {v for v in CRIF_TO_BLOCK.values() if v}


def _extract_block_name(raw: str) -> str | None:
    """
    From a (possibly multiline) Block cell, return the first line that
    matches one of the block names we care about.
    """
    for line in raw.split("\n"):
        line = line.strip()
        if line in _WANTED:
            return line
    return None


def parse_address_map(map_path: str) -> dict[str, list[tuple[int, int]]]:
    """Returns: block_name → list of (start_pa, end_pa)."""
    result: dict[str, list[tuple[int, int]]] = defaultdict(list)

    try:
        enc = "utf-16"
        open(map_path, encoding=enc).read(1)   # probe
    except UnicodeError:
        enc = "utf-8"

    with open(map_path, encoding=enc) as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)   # skip header
        for row in reader:
            if len(row) < 5:
                continue
            raw_block = row[1] if len(row) > 1 else ""
            raw_start = row[3].strip() if len(row) > 3 else ""
            raw_end   = row[4].strip() if len(row) > 4 else ""
            if not raw_start or not raw_end:
                continue
            start_pa = parse_hex(raw_start)
            end_pa   = parse_hex(raw_end)
            if start_pa is None or end_pa is None:
                continue
            block = _extract_block_name(raw_block)
            if block:
                result[block].append((start_pa, end_pa))

    return dict(result)


# ─── Phase 2: load CRIF registers ────────────────────────────────────────────

def ip_key(filepath: str, base: str) -> tuple[str, str]:
    rel   = filepath.replace(base + "/", "")
    parts = rel.split("/")
    return parts[0], (parts[1] if len(parts) > 2 else parts[0])


def get_block(ip_group: str, sub_mod: str) -> str | None:
    key = f"{ip_group}/{sub_mod}"
    if key in CRIF_TO_BLOCK:
        return CRIF_TO_BLOCK[key]
    return CRIF_TO_BLOCK.get(ip_group)


def load_crif_registers(src: str) -> list[dict]:
    """
    Returns one dict per unique (file, registerFile, register, addr) tuple.
    Also attaches per-file min_addr for LOCAL/GLOBAL classification.
    """
    files = sorted(glob.glob(src + "/**/*.csv.bz2", recursive=True))
    print(f"[*] Source : {src}")
    print(f"[*] Found {len(files)} CSV.bz2 files – loading …", flush=True)

    all_rows: list[dict] = []

    for idx, fpath in enumerate(files, 1):
        ip_group, sub_mod = ip_key(fpath, src)
        block = get_block(ip_group, sub_mod)
        if block is None:
            continue   # skip IPs not in the map

        try:
            data   = bz2.open(fpath, "rt", errors="replace").read()
            reader = csv.DictReader(io.StringIO(data))
        except Exception as e:
            print(f"  [WARN] {fpath}: {e}", file=sys.stderr)
            continue

        # Collect unique (registerFile, register, addr) per file
        seen: set[tuple] = set()
        file_addrs: list[int] = []
        file_rows: list[dict] = []

        for row in reader:
            reg_file = (row.get("registerFile") or "").strip()
            reg_name = (row.get("Register")     or "").strip()
            ao_raw   = (row.get("addressOffset")or "").strip()
            addr = parse_hex(ao_raw)
            if addr is None:
                continue
            key = (reg_file, reg_name, addr)
            if key in seen:
                continue
            seen.add(key)
            file_addrs.append(addr)
            file_rows.append({
                "ip_group":     ip_group,
                "sub_mod":      sub_mod,
                "block_name":   block,
                "registerFile": reg_file,
                "register":     reg_name,
                "crif_offset":  addr,
                "file":         fpath.replace(src + "/", ""),
            })

        # Attach per-file min_addr so we can use it for classification
        if file_addrs:
            file_min = min(file_addrs)
            for r in file_rows:
                r["file_min_addr"] = file_min
            all_rows.extend(file_rows)

        if idx % 20 == 0:
            print(f"  … processed {idx}/{len(files)}", flush=True)

    print(f"[*] Loaded {len(all_rows)} register entries from mapped IPs.", flush=True)
    return all_rows


# ─── Phase 3: range check ────────────────────────────────────────────────────

def check_in_range(
    crif_offset: int,
    file_min: int,
    windows: list[tuple[int, int]],
) -> tuple[bool, str, int]:
    """
    Returns (in_range, method, absolute_addr).

    Tries LOCAL first (min_start_pa + offset), then GLOBAL (CHIP_BASE + offset).
    Returns after the first hit; if both miss, returns (False, "NONE", -1).
    """
    min_start = min(s for s, e in windows)

    # LOCAL: treat crif_offset as offset from the block's base address
    abs_local = min_start + crif_offset
    if any(s <= abs_local <= e for s, e in windows):
        return True, "LOCAL", abs_local

    # GLOBAL: treat crif_offset as chip-PA minus CHIP_BASE
    abs_global = CHIP_BASE + crif_offset
    if any(s <= abs_global <= e for s, e in windows):
        return True, "GLOBAL", abs_global

    return False, "NONE", abs_local   # return LOCAL absolute for reference


def find_out_of_range(
    registers: list[dict],
    addr_map: dict[str, list[tuple[int, int]]],
) -> list[dict]:
    out: list[dict] = []

    for reg in registers:
        block   = reg["block_name"]
        windows = addr_map.get(block)
        if not windows:
            continue   # block not found in export.csv — skip silently

        ok, method, absolute = check_in_range(
            reg["crif_offset"], reg["file_min_addr"], windows
        )
        if not ok:
            windows_str = ";".join(f"{hex(s)}-{hex(e)}" for s, e in sorted(windows))
            out.append({
                "ip_group":         reg["ip_group"],
                "sub_mod":          reg["sub_mod"],
                "block_name":       block,
                "registerFile":     reg["registerFile"],
                "register":         reg["register"],
                "crif_offset":      hex(reg["crif_offset"]),
                "computed_local":   hex(min(s for s,e in windows) + reg["crif_offset"]),
                "computed_global":  hex(CHIP_BASE + reg["crif_offset"]),
                "block_windows":    windows_str,
                "source_file":      reg["file"],
            })

    return out


# ─── Phase 4: write CSV report ───────────────────────────────────────────────

def write_report(out_of_range: list[dict], out_path: str):
    fieldnames = [
        "ip_group", "sub_mod", "block_name",
        "registerFile", "register",
        "crif_offset", "computed_local", "computed_global",
        "block_windows", "source_file",
    ]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(sorted(
            out_of_range,
            key=lambda r: (r["ip_group"], r["sub_mod"], int(r["crif_offset"], 16))
        ))
    print(f"[*] Report written → {out_path}  ({len(out_of_range)} out-of-range registers)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    # Load address map
    addr_map = parse_address_map(args.map)
    print(f"[*] Address map : {args.map}")
    print(f"[*] Blocks found: {sorted(addr_map.keys())}\n")
    for block, wins in sorted(addr_map.items()):
        for s, e in wins:
            print(f"    {block:30s}  {hex(s)} – {hex(e)}")

    # Load CRIF registers (only for mapped IPs)
    registers = load_crif_registers(args.src)

    # Check ranges
    out_of_range = find_out_of_range(registers, addr_map)

    # Summary
    total   = len(registers)
    bad     = len(out_of_range)
    good    = total - bad
    print(f"\n{'='*65}")
    print(f"  Total registers checked : {total:7,}")
    print(f"  In range                : {good:7,}  ({100*good/total:.1f}%)" if total else "")
    print(f"  OUT OF RANGE            : {bad:7,}  ({100*bad/total:.1f}%)" if total else "")
    print(f"{'='*65}")

    if out_of_range:
        from collections import Counter
        print("\n  Out-of-range breakdown by IP/block:")
        cnt = Counter((r["ip_group"], r["sub_mod"], r["block_name"]) for r in out_of_range)
        for (ip, sub, block), n in sorted(cnt.items(), key=lambda x: -x[1]):
            print(f"    {ip}/{sub:30s} → {block:25s}  {n} registers")
    else:
        print("\n[OK] All registers are within their declared IP address windows.")

    write_report(out_of_range, args.out)


if __name__ == "__main__":
    main()
