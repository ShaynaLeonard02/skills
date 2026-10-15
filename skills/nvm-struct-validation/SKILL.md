---
name: nvm-struct-validation
description: 'Context/continuity skill for the MMG/MEV/CNIC NVM "Init Module" & "Runtime Params" structure-validation feature (prevents illegal/backward-incompatible layout changes to fixed-layout NVM boot sections). Two parallel implementations were built: an earlier CSV-based per-map "struct" branch (row_repo_name_repo, dev/sleonarx/struct, ticket 34852) and the chosen header-diff "valid" branch (ggl_repo_name_repo, dev/sleonarx/valid, ticket 36023) that all further work continues on. Use when asked to: continue/extend/fix the init module or runtime params struct validation feature, add a missing project baseline (MMG_TS/MEV_TS/MMG800/NSC), explain what struct_validation.py does or where its baselines live, compare the two implementations, or pick up work on dev/sleonarx/valid.'
argument-hint: "(informational/navigational — no CLI args of its own; see 'Known Gaps / Next Steps' for concrete follow-up work and 'Usage / Procedure' for how to run/extend the validator)"
---

# NVM Init Module & Runtime Params Struct Validation

## Purpose

MMG/MEV/CNIC boot firmware decodes two NVM sections by **absolute bit offset**:

- **Init Module** — words/fields consumed during early boot module injection.
- **Runtime Params** — words/fields consumed as runtime configuration.

Because firmware hardcodes the offsets, an accidental edit to one of these sections (a field
renamed, moved, or the section grown) silently breaks firmware that still expects the old layout.
This feature validates that any change to these sections is a **legal evolution**, not a breaking
one:

- Non-reserved words/fields must keep the same name, bit offset, and bit width.
- `RESERVED`/`RSVD` words may be replaced in place (that's what they're reserved for).
- New words/fields may only be **appended at the end**, consuming reserved space.
- The **total section size must never grow**.

See [checkpoint context](#decision-work-continues-on-dev-sleonarxvalid) below — this was built
twice, in parallel, as two different architectures.

## Where things live

| | Path | Branch | Ticket |
|---|---|---|---|
| **Chosen / active** | [ggl_repo_name_repo/sources/imc/nvm-generator](/home/sleonarx/projects/ggl_repo_name_repo/sources/imc/nvm-generator) | `dev/sleonarx/valid` | 36023 |
| (firmware struct source of truth, not part of this feature's code) | [ggl_repo_name_repo/sources/imc/imc_shared](/home/sleonarx/projects/ggl_repo_name_repo/sources/imc/imc_shared) | `dev/sleonarx/valid` | — |
| Earlier parallel attempt (reference only) | [row_repo_name_repo/sources/imc/nvm-generator](/home/sleonarx/projects/row_repo_name_repo/sources/imc/nvm-generator) | `dev/sleonarx/struct` | 34852 |
| " (per-map baseline files) | [row_repo_name_repo/sources/imc/nvm-generator-config-cnic](/home/sleonarx/projects/row_repo_name_repo/sources/imc/nvm-generator-config-cnic), [-mev](/home/sleonarx/projects/row_repo_name_repo/sources/imc/nvm-generator-config-mev), [-mmg](/home/sleonarx/projects/row_repo_name_repo/sources/imc/nvm-generator-config-mmg) | `dev/sleonarx/struct` | 34852 |

Both branches fork from a common ancestor (`d8956f81d`) but never merged into each other or into
`main`; they are mutually exclusive architectures for the same goal. `struct` was authored first
(~May 2026), `valid` was authored ~7 weeks later as an independent rewrite (~June 2026).

## Decision: work continues on `dev/sleonarx/valid`

Going forward, all new work on this feature happens in
[ggl_repo_name_repo/sources/imc/nvm-generator](/home/sleonarx/projects/ggl_repo_name_repo/sources/imc/nvm-generator)
on branch `dev/sleonarx/valid` (ticket 36023). The `dev/sleonarx/struct` branch (row_repo, ticket
34852) is kept only as historical/architectural reference — do not build on it unless explicitly
asked to resurrect it.

## Architecture of the chosen ("valid") implementation

- **Validator module**: [NVMGenerator/struct_validation.py](/home/sleonarx/projects/ggl_repo_name_repo/sources/imc/nvm-generator/NVMGenerator/struct_validation.py)
  (single file, handles both header types).
  - `_classify_header(filename)` — picks `init_module` vs `runtime_params` by filename substring.
  - `_parse_init_module_header()` / `_parse_runtime_params_header()` — parse `#define ..._SHIFT` /
    `..._MASK` pairs out of a **generated `.h` file** (not the CSV).
  - `validate_header(new_path, baseline_path, source_csv_path=None)` — compares one new header to
    one baseline header; raises `ValueError` on an illegal change, propagates any other exception
    (fails **closed**, unlike the struct branch — see Pros/Cons).
  - `validate_headers_against_baseline_dir(new_header_paths, baseline_dir, source_csv_path=None)` —
    matches each new header to a baseline **by basename** in a directory; silently skips files with
    no matching baseline.
  - Also runnable standalone: `python struct_validation.py --new <new.h> --baseline <base.h>`.
- **Baselines (golden reference headers)**: [NVMGeneratorTesting/InputFiles/structs_validation/local/<project>/](/home/sleonarx/projects/ggl_repo_name_repo/sources/imc/nvm-generator/NVMGeneratorTesting/InputFiles/structs_validation/local)
  (`mev`, `mmg`, `cnic`, `mev_anvm`, `lwc`). See the directory's own
  [README.md](/home/sleonarx/projects/ggl_repo_name_repo/sources/imc/nvm-generator/NVMGeneratorTesting/InputFiles/structs_validation/README.md)
  for the full naming convention and the `local/` vs `imcv2/` split.
- **Hook point**: [NVMGenerator/flow_manager.py](/home/sleonarx/projects/ggl_repo_name_repo/sources/imc/nvm-generator/NVMGenerator/flow_manager.py),
  right after header generation (`create_header_files`), in two places:
  - Normal/local header-gen path — baseline dir = `structs_validation/local/<project.lower()>`
    (around line 4337).
  - ANVM header-gen path — baseline dir = `structs_validation/local/<mapped-subdir>` via
    `_ANVM_BASELINE_SUBDIR_MAP = {'mev': 'mev_anvm', 'qat': 'lwc'}`, else `project.lower()` (around
    line 4408).
  - Both call `validate_headers_against_baseline_dir(...)`; if the computed baseline directory
    doesn't exist (`os.path.isdir` check), validation is **silently skipped** for that project.

## Known gaps / next steps (confirmed, not merely suspected)

1. **Only 4 projects are actually wired up and have baselines**: MEV (ANVM), MMG, CNIC, QAT/LWC
   (per the baselines' own README). Despite ticket 36023's commit message listing
   `MMG, MMG_TS, MEV, MEV_TS, LWC, CNIC, MMG800` as in scope, there are **no** `mmg_ts`, `mev_ts`,
   or `mmg800` baseline directories under `structs_validation/local/` — those projects' generated
   headers are never validated today (the `os.path.isdir` check fails and the hook is a no-op).
2. **`structs_validation/imcv2/` baseline files are dead code** — they exist (copied from
   `imc_shared`'s real header-generation output, e.g. `mev_ts_veloce_nvm_init_module.h`,
   `nsc_anvm_2_simics_veloce_nvm_runtime_params.h`) but are not referenced anywhere in
   `flow_manager.py` or `struct_validation.py`. They look like the raw material intended to seed
   the missing MEV_TS/MMG_TS/MMG800/NSC baselines in `local/`, but that wiring was never finished.
3. **No unit tests** exist for `struct_validation.py` (the struct/row branch has ~584 lines of UTs
   that this implementation never ported over).
4. **Silent skip on missing baseline dir** is a fail-open design — worth considering whether a
   missing baseline for an in-scope project should log a warning (or fail) rather than say nothing.

When asked to continue this feature, prefer closing gap #1 first (it's the actual functional hole
relative to the ticket's stated scope), using the `imcv2/` files from gap #2 as the starting
baselines once confirmed correct, then add unit tests (gap #3).

## Reference: the rejected "struct" implementation (for history/porting ideas only)

Row_repo's `dev/sleonarx/struct` (ticket 34852) validated the **raw CSV map rows** directly
(re-parsing `Section Name`/`Word Name`/`Field Name`/`Lower Bit`/`Upper Bit` columns) against one
generated `_struct.h` baseline **per CSV map file**, committed next to each CSV across all three
config repos (`nvm-generator-config-cnic/-mev/-mmg`). Two generator scripts produced those
baselines: `generate_init_module_struct_headers.py` / `generate_runtime_params_struct_headers.py`,
under `NVMGenerator/Scripts/` in that branch. It had broader *committed* coverage (including CNIC
and MEV_TS) and dedicated unit tests, but a much heavier/scattered maintenance footprint, its own
re-implementation of CSV-to-bitfield interpretation (risk of drifting from the real generator), and
a fail-open `except Exception: warn-and-skip` around its core validation call. Do not resurrect
this branch without being asked — use it only as a source of ideas (e.g. its broader project
coverage, its unit-test patterns) when closing the gaps above.

## Pros / Cons summary

| | struct (34852, reference only) | valid (36023, **active**) |
|---|---|---|
| Source of truth | Raw CSV re-parsed | Real generated header (reuses real generator output) |
| Baseline footprint | Huge, scattered across 3 config repos | Small, centralized in `nvm-generator` itself |
| Committed project coverage | Broad (incl. CNIC, MEV_TS) | Only MEV/MMG/CNIC/QAT-LWC wired up today |
| Error handling | Fails open (`except Exception: warn & skip`) | Fails closed (only `ValueError` caught; else propagates) |
| Unit tests | Yes | No (gap) |
| `flow_manager.py` footprint | ~215 lines, 3 traversal methods | ~30 lines, 1 hook reused twice |

## When to Use

- Continuing, fixing, or extending the init module / runtime params struct-validation feature.
- Adding baseline coverage for a project that's missing it (MMG_TS, MEV_TS, MMG800, NSC).
- Explaining what `struct_validation.py` does, how it's wired into `flow_manager.py`, or where its
  baseline files live.
- Asked to compare the two historical implementations or justify why `dev/sleonarx/valid` was
  chosen.
- Picking up work in [ggl_repo_name_repo/sources/imc/nvm-generator](/home/sleonarx/projects/ggl_repo_name_repo/sources/imc/nvm-generator)
  on branch `dev/sleonarx/valid`.

## Usage / Procedure

```bash
# Always work in the chosen implementation:
cd /home/sleonarx/projects/ggl_repo_name_repo/sources/imc/nvm-generator
git checkout dev/sleonarx/valid

# Validate one generated header against one baseline by hand:
python NVMGenerator/struct_validation.py \
    --new  <path/to/newly_generated_nvm_init_module.h> \
    --baseline NVMGeneratorTesting/InputFiles/structs_validation/local/<project>/<same_basename>.h

# To add coverage for a currently-missing project (e.g. closing gap #1 for MMG_TS):
# 1. Generate that project's current headers via the normal flow_manager.py header-gen path
#    (whatever -p/-i args that project normally uses — see the baselines' own README.md for
#    the per-project arg convention already documented for MEV/MMG/CNIC/QAT).
# 2. Review the output headers are correct/intentional (this becomes the new "golden" baseline).
# 3. Copy them into a new NVMGeneratorTesting/InputFiles/structs_validation/local/<project>/
#    directory (basenames must match exactly what flow_manager.py's create_header_files emits).
# 4. If the project is an ANVM project needing a subdir remap (like mev->mev_anvm, qat->lwc),
#    add an entry to _ANVM_BASELINE_SUBDIR_MAP in flow_manager.py.
# 5. Re-run header generation for that project once more — it should now validate cleanly against
#    the baseline you just added, with no ValueError raised.

# To update an existing baseline after an intentionally-approved structural change:
# replace the corresponding file under .../structs_validation/local/<project>/ with the new
# generated header, after review (per that directory's own README.md "Updating Baselines" note).
```
