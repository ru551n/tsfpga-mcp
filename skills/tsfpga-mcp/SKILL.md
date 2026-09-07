---
name: tsfpga-mcp
description: Synthesize VHDL/Verilog designs and check resource usage (LUTs/FFs/DSPs/block RAMs) through the tsfpga-mcp MCP server (tsfpga_status, tsfpga_inspect, tsfpga_targets, tsfpga_synthesize), or build/list a real project's own netlist or top-level (Vivado synthesis + full implementation) builds through its build_fpga.py (tsfpga_project_status, tsfpga_project_list_builds, tsfpga_project_build, tsfpga_project_get_timing_report). Use when the user asks to synthesize an entity or module, check resource counts, or find out which chips/families (generic/xilinx/intel/microchip) the installed yosys can target, and also when the user wants to build/list the netlist (or top-level Vivado synthesis/implementation) projects of an actual tsfpga project on disk, or get a Vivado timing report for one; the server drives tsfpga's Yosys+GHDL netlist build and returns aggregated resource counts, and never guesses top, chip, f...
---

# tsfpga MCP

## Overview
Use this skill whenever the user asks to **synthesize** or **check the
resources of** a VHDL or Verilog design with the `tsfpga-mcp` MCP server:
"synthesize this entity", "how many LUTs/FFs does this use", "does this
design build for xilinx/intel/microchip", "which chips can this yosys
target". The server stages the sources into a throwaway `tsfpga` module
and drives `tsfpga.yosys.project.YosysNetlistBuild` (GHDL frontend +
`synth`/`synth_xilinx`/`synth_intel`/`synth_microchip`), returning
aggregated resource counts (no per-port netlist — only the utilization
report).

Triggers on: synthesize, synthesis, netlist, resource usage, LUT/FF
count, gate count, synthesize for <chip>, build the FPGA netlist, yosys,
does this design elaborate and synthesize.

If a synthesis fails with a configuration-looking error (missing plugin,
missing GHDL libraries, no flows found), call `tsfpga_status` first — it
reports yosys version, available flows, plugin path, `GHDL_PREFIX` and
timeout.

## Two synthesis modes — pick the right one
This server has two independent ways to get resource counts. Do not mix
them up:

| | `tsfpga_synthesize` (ad hoc) | `tsfpga_project_*` (project) |
| --- | --- | --- |
| Input | Loose source files passed in the call | A real project directory with its own build script |
| Setup | None — works out of the box | None either — defaults to whichever of `build.py`/`build_fpga.py` exists in the server's current working directory; point `TSFPGA_MCP_PROJECT_DIR`/`TSFPGA_MCP_BUILD_SCRIPT` elsewhere if that's not where/what the project's build script is |
| Modules/generics/IP | Only what's in `sources` | Resolved exactly as the project's own build does (its `ModuleList`, register generation, IP, static generics, ...) |
| Runs | In-process (one throwaway tsfpga module) | Subprocess: `<python> build.py <args>` in the project, same as a human would run it from a terminal |
| Use for | "Does this entity/module synthesize / how many LUTs" for arbitrary/pasted/scratch code, quick what-if checks | "Build/check the netlist projects of *this* project", CI-like resource checks that must match the project's real build |

If the user names or clearly means an existing project on disk (a repo
with its own build script, module structure, register generation, etc.),
use the `tsfpga_project_*` tools — call `tsfpga_project_status` first to
confirm what it resolved to (it defaults to whichever of `build.py`/
`build_fpga.py` exists in the current working directory, which is not
necessarily the project the user means).
If they hand you loose source files or ask a generic "would this
synthesize" question with no project context, use `tsfpga_synthesize`
instead. If `tsfpga_project_status` shows the wrong project/script and
the user wants project mode, ask them to set `TSFPGA_MCP_PROJECT_DIR`
and/or `TSFPGA_MCP_BUILD_SCRIPT` (do not fall back to ad hoc mode
silently — module resolution differs and the result would not represent
the real project).

## Hard rule: never infer — ask
The server does not guess anything, and neither should you. Before calling
`tsfpga_synthesize`, every one of these must be **known** (stated by the
user, or unambiguously discoverable from `tsfpga_inspect` output):

| Parameter | Known when | Otherwise |
| --- | --- | --- |
| `top` | the user named it, or `tsfpga_inspect` shows exactly one synthesizable candidate | **ask the user** which top level to synthesize (list the candidates) |
| `chip` / `family` | the user named the target | **ask the user** which chip to target (list the ones `tsfpga_targets` reports; mention `generic` = vendor-independent RTL check) |
| `generics` | the user gave values, or the top has no generics | **ask the user**: list each generic with its type and default, and ask for values or "use defaults" |
| `vhdl_entities` (non-VHDL top only) | the user named the VHDL units instantiated from the top | **ask the user** which VHDL entities (if any) the top instantiates |

"Ambiguous" is defined by the inspection's own `Notes:` section (multiple
architectures, a unit declared in both languages, ...) — whenever a note
points at a choice, that choice is a question to the user, not a guess.
Do not pick a "likely" top, a "reasonable" chip, or default generic
values silently. The cost of one question is far less than synthesizing
the wrong thing.

## Tools

| Tool | What it does | Cost |
| --- | --- | --- |
| `tsfpga_status` | Server config: yosys version, available synthesis flows, ghdl plugin path (exists/missing), `GHDL_PREFIX`, timeout. Diagnose configuration problems here. | free |
| `tsfpga_inspect` | Static scan of the given sources: VHDL entities with architectures + generics (name, type, default), Verilog modules with parameters (name, default), plus a `Notes:` list of ambiguities and per-file read errors. Nothing is compiled. | free |
| `tsfpga_targets` | Chip targets this server can synthesize for: per chip — the yosys flow (`synth`, `synth_xilinx`, `synth_intel`, `synth_microchip`), whether that flow exists in the installed yosys, and the known device families. Use before asking the user which chip/family to target. | free |
| `tsfpga_synthesize` | Runs the synthesis and returns the resource counts or `Synthesis FAILED` with diagnostics. | one GHDL + yosys run |
| `tsfpga_project_status` | Project-mode config: resolved project dir, build script, interpreter, projects path, timeout. Call first to confirm what it resolved to (defaults to whichever of `build.py`/`build_fpga.py` exists in the current working directory). | free |
| `tsfpga_project_list_builds` | Lists the project's own build projects (`build.py --list-only`), netlist builds by default. Use to find project name filters. | one subprocess call |
| `tsfpga_project_build` | Builds project(s) by running the project's own build script (netlist builds by default). Returns pass/fail plus the build's own output (utilization report included for netlist builds); on success (full top-level builds only) lists the written bitstream artifact paths, on failure surfaces every Vivado `ERROR:`/`CRITICAL WARNING:` line (plus context) first. | one full build subprocess |
| `tsfpga_project_get_timing_report` | Timing-analysis report for an already-built project's run: `report_type` = `summary` (default, with a structured WNS/TNS/WHS/THS header + worst failing endpoints), `pulse_width`, `bus_skew`, or `clock_interaction`. Regenerates via Vivado batch mode when no cached report exists. | one Vivado batch run (or free if cached) |
| `tsfpga_project_get_utilization_report` | Hierarchical per-module utilization (LUT/FF/BRAM/DSP/...) `hierarchical_depth` levels deep. Default depth (4) is normally free — tsfpga already writes that file for every build; other depths regenerate via Vivado. | free at default depth, else one Vivado batch run |
| `tsfpga_project_get_drc_report` | DRC or methodology report (`report_type`: `drc`/`methodology`) for an already-built project's run. tsfpga never writes either automatically, so this always regenerates via Vivado unless a cached report from a previous call exists. | one Vivado batch run (or free if cached) |

## `tsfpga_synthesize` inputs
- `sources` — HDL files (`.vhd`/`.vhdl` and/or `.v`/`.sv`) of the design,
  any order. **All units the top needs must be covered by these files.**
  Base names must be unique across the list (files are staged into one
  flat directory).
- `top` — the VHDL entity or Verilog/SystemVerilog module that is
  synthesized (no library prefix).
- `chip` — `generic` (vendor-independent, the server default), `xilinx`,
  `intel`, `microchip`. Use `tsfpga_targets` for the list this yosys
  actually provides.
- `family` — device family for the chip (e.g. `xc7` for xilinx,
  `cycloneiv` for intel, `polarfire` for microchip). Not accepted for
  `generic`.
- `vhdl_entities` — only when `top` is NOT a VHDL entity: the VHDL entity
  names that shall be made available for instantiation from the top (or
  from other VHDL entities). Leave empty when `top` is a VHDL entity —
  its dependencies are found automatically.
- `generics` — VHDL generic overrides, name → value, e.g.
  `{"WIDTH": "8"}`. **Only supported when `top` is a VHDL entity.** The
  declared VHDL type (from `tsfpga_inspect`) decides interpretation:
  boolean/integer/natural/positive/real/std_logic_vector(+unsigned/
  signed)/string.
- `vhdl_standard` — `"93"`, `"08"` (default), or `"19"`.
- `discard_ffinit` — `microchip` only: discard un-legalizable flip-flop
  initial values instead of failing.
- `timeout` — max seconds for this run (default `TSFPGA_MCP_TIMEOUT`).

## `tsfpga_project_*` inputs
- `tsfpga_project_status` — no inputs.
- `tsfpga_project_list_builds`: `netlist_builds` (default `true`), `project_filters` (wildcards, e.g. `["*canny*"]`, empty = all).
- `tsfpga_project_build`: `project_filters` (wildcards, empty = all — call `tsfpga_project_list_builds` first so "all" is an informed choice), `netlist_builds` (default `true`), `use_existing_project` (default `true`, faster iteration; set `false` to force a clean re-create), `num_parallel_builds` (projects built concurrently, tsfpga default `8` — the only parallelism knob netlist builds have, so it only helps when the filters match several projects), `num_threads_per_build` (threads inside one build process, tsfpga default `4`; top-level/Vivado builds only — Yosys netlist synthesis is single-threaded and ignores it, and the tool says so if you set it anyway), `synth_only` (top-level/Vivado builds only: stop after synthesis, no place & route/bitstream — a no-op for netlist builds, which are always synthesis-only already), `from_impl` (resume a prior `synth_only=true` top-level build into a full implementation run instead of starting over; mutually exclusive with `synth_only`, requires `use_existing_project=true`), `timeout` (override for this call).
- `tsfpga_project_get_timing_report`: `project` (exact build name, required — not a wildcard), `run_index` (default `1`, matches the `N` in `synth_N`/`impl_N`), `synth_only` (report on the `synth_N` run instead of `impl_N` — use for netlist builds and for top-level builds that were themselves built with `synth_only=true`), `report_type` (`summary` default, `pulse_width`, `bus_skew`, `clock_interaction`), `verbosity` (`full` default or `summary` — `summary` only affects `report_type=summary` and returns just the structured WNS/TNS/WHS/THS header + worst failing endpoints, not the full raw report), `force_regenerate` (default `false`; re-run Vivado even if a cached report exists), `timeout` (override for this call).
- `tsfpga_project_get_utilization_report`: `project` (required), `run_index` (default `1`), `synth_only` (default `false`), `hierarchical_depth` (default `4` — matches what tsfpga itself already writes, so this depth is normally served free with no Vivado call; any other value regenerates and caches separately), `force_regenerate`, `timeout`.
- `tsfpga_project_get_drc_report`: `project` (required), `run_index` (default `1`), `synth_only` (default `false`), `report_type` (`drc` default or `methodology`), `force_regenerate`, `timeout`. Response includes a `Checks found: N` line parsed from the report when present.

All default to whichever of `build.py`/`build_fpga.py` exists in
the server's current working directory (`build.py` wins if both do), no
env var required. Set `TSFPGA_MCP_PROJECT_DIR` (project's directory)
and/or `TSFPGA_MCP_BUILD_SCRIPT` (script name/path, relative to
`TSFPGA_MCP_PROJECT_DIR` unless absolute) when the project isn't the cwd
or the script has a different name; see `tsfpga_project_status` for
what else is configurable (`TSFPGA_MCP_PROJECT_PYTHON`,
`TSFPGA_MCP_PROJECTS_PATH`, `TSFPGA_MCP_PROJECT_TIMEOUT`,
`TSFPGA_MCP_PROJECT_EXTRA_ARGS`, `TSFPGA_MCP_VIVADO`).

### Project virtualenv
The project's own `.venv`/`venv` is always used **and activated** for the build
script (and for Vivado in the `tsfpga_project_get_timing_report`/
`tsfpga_project_get_utilization_report`/`tsfpga_project_get_drc_report`
tools): `VIRTUAL_ENV`
set, `<venv>/bin` first on `PATH`, `PYTHONHOME` cleared, this server's own venv
removed. If the project has no venv, one is created with uv from
`pyproject.toml` (`uv sync`) or `requirements.txt`, under a cross-process lock
shared with vunit-mcp so two servers/agents starting at once cannot race.
`tsfpga_project_status` reports the venv and what was done. Disable with
`TSFPGA_MCP_PROJECT_AUTO_VENV=0`; setting `TSFPGA_MCP_PROJECT_PYTHON` also
disables it.

### Several agents on one code base
Build projects are written to `TSFPGA_MCP_PROJECTS_PATH`
(`<project>/tsfpga_mcp_out/projects` by default) — two agents building the same
project name there will clobber each other. Give each agent its own path, or
better, its own git worktree (then the cwd defaults are already disjoint).

### Vivado reports on demand (top-level/Vivado builds only)
Three tools invoke Vivado directly (the build tools never do — tsfpga does
that internally) to get reports beyond what a build itself produces:
`tsfpga_project_get_timing_report`, `tsfpga_project_get_utilization_report`,
`tsfpga_project_get_drc_report`. All need the project already built via
`tsfpga_project_build` (`netlist_builds=false` and `synth_only=false` for an
`impl_N` run) plus a `vivado` executable on `PATH` or `TSFPGA_MCP_VIVADO`
set — check `tsfpga_project_status` if that's unclear.

Which reports tsfpga writes automatically differs per kind, and drives
whether a call is free (reads a cached file) or spins up Vivado:
- **Only on a violation** (`timing_summary.rpt`, `pulse_width.rpt`,
  `bus_skew.rpt`, `clock_interaction.rpt`): a normal, timing-clean
  implementation build produces **no report at all** for any of these — a
  clean run always means `tsfpga_project_get_timing_report` regenerates
  via Vivado batch mode (`open_project`/`open_run`/the matching
  `report_*` command).
- **Always, for every build** (`hierarchical_utilization.rpt`, at depth
  4): `tsfpga_project_get_utilization_report`'s default
  `hierarchical_depth=4` is therefore normally free (reads that existing
  file, no Vivado call) — only a different depth, or
  `force_regenerate=true`, triggers regeneration (cached separately per
  depth so different depths never collide).
- **Never** (DRC, methodology): `tsfpga_project_get_drc_report` always
  regenerates via Vivado unless a previous call for the same run/type
  already cached one.

For `tsfpga_project_get_timing_report` with the default `report_type=
summary`, prefer `verbosity=summary` when you only need to know whether
timing is met and by how much — it skips the full per-path report and
returns just the parsed WNS/TNS/WHS/THS numbers plus the worst failing
endpoints.

## Output shape

Success (resource counts only — no per-port netlist):
```
Synthesis OK: top `counter` -> xilinx (xc7) in 0.6s.

Resources:
  Total LUTs  3
  FFs         8
```

Failure:
```
Synthesis FAILED (0.3s).

Diagnostics:
  ERROR: ...
```
Read the `Diagnostics:` for the actual GHDL/yosys errors.

## Workflows (user request → tool calls)

**"Synthesize <design> [for <chip>]"**
1. `tsfpga_inspect(sources=...)` — see the units, architectures,
   generics, and the `Notes:` ambiguities.
2. Resolve the unknowns **by asking the user**, per the hard rule above:
   top, chip/family (`tsfpga_targets` for the candidate list), generic
   values, and `vhdl_entities` (non-VHDL top only). Only proceed once
   every parameter is known.
3. `tsfpga_synthesize(sources, top, chip, family?, generics?,
   vhdl_entities?)`. Report the resource counts; offer to change a
   generic or retarget another chip.

**"Does this design build?" / "any errors?"**
→ Same workflow; the answer is the FAILED diagnostics or the OK summary.
Use `chip="generic"` only when the user accepts a vendor-independent
check (say so).

**"What chips/families can I synthesize for?"**
→ `tsfpga_targets`. Availability is probed from the installed yosys at
runtime — never assume a flow exists just because a chip is listed.

**"What are the resources of <top>? (already known params)"**
→ Skip straight to `tsfpga_synthesize` with the parameters the user gave.

**Mixed-language design (Verilog top + VHDL units, or vice versa)**
- A **Verilog/SystemVerilog top may instantiate VHDL units**: pass both
  files in `sources` and list the VHDL entity names in `vhdl_entities`.
- A **VHDL top may instantiate Verilog units** too: pass both files in
  `sources`; the server reads the Verilog first, so GHDL's unbound
  component instantiation binds straight to the real Verilog module
  instead of becoming a black box. No extra parameter needed in this
  direction.

**Synthesis failed**
→ Read the `Diagnostics:` (the actual GHDL/yosys errors). Common causes:
a source file missing from `sources`, a generic given for a non-VHDL
top, an unknown/misspelled `vhdl_entities` name, or a `family` value the
installed yosys doesn't recognize.

**Ports / netlist-level questions**
→ Not supported by this server (tsfpga's build produces resource counts
only, no port-level netlist dump). Say so if asked; suggest
`tsfpga_inspect` for the source-level port/generic declarations instead.

**"Build/check the netlist projects of this project" (real project on disk)**
1. Call `tsfpga_project_status` first — it defaults to whichever of
   `build.py`/`build_fpga.py` exists in the current working directory,
   which may not be the project the user means. If it resolved to the
   wrong project/script, fix it via
   `TSFPGA_MCP_PROJECT_DIR`/`TSFPGA_MCP_BUILD_SCRIPT` (ask the user)
   before proceeding.
2. `tsfpga_project_list_builds()` — see what project names exist before
   guessing filters.
3. `tsfpga_project_build(project_filters=...)` — report pass/fail and the
   resource counts from the output. On failure, the output tail has the
   real GHDL/yosys/build error; do not fall back to `tsfpga_synthesize`
   to "work around" a project build failure — that would synthesize
   different sources than the project actually builds.

**"Build/implement <project> for real" / "does it meet timing?" / "what's
the bitstream path?" (top-level Vivado build)**
1. `tsfpga_project_build(project_filters=[...], netlist_builds=false)` —
   full implementation by default (`synth_only=true` to stop after
   synthesis first, e.g. for a quick utilization/timing look before
   committing to place & route). On failure, the surfaced
   `ERROR:`/`CRITICAL WARNING:` lines are the root cause — read those
   first, don't dig through the full output. On success, the listed
   bitstream artifact paths (`.bit`/`.bin`/`.xsa`) answer "where's the
   bitstream" directly.
2. "Does it meet timing?" → `tsfpga_project_get_timing_report(project=...,
   verbosity="summary")` for a quick WNS/TNS/WHS/THS + worst-endpoints
   answer; drop `verbosity` (or set `report_type` to `pulse_width`/
   `bus_skew`/`clock_interaction`) for the full report or a specific
   check.
3. "How is the design's resources broken down / which module is biggest?"
   → `tsfpga_project_get_utilization_report(project=...)` (free at the
   default depth 4; increase `hierarchical_depth` for finer-grained
   detail, at the cost of a Vivado run).
4. "Any DRC/methodology issues?" → `tsfpga_project_get_drc_report(
   project=..., report_type="drc")` (or `"methodology"`).
