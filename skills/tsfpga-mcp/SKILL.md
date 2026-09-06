---
name: tsfpga-mcp
description: Synthesize VHDL/Verilog designs and check resource usage (LUTs/FFs/DSPs/block RAMs) through the tsfpga-mcp MCP server (tsfpga_status, tsfpga_inspect, tsfpga_targets, tsfpga_synthesize), or build/list a real project's own netlist builds through its build_fpga.py (tsfpga_project_status, tsfpga_project_list_builds, tsfpga_project_build). Use when the user asks to synthesize an entity or module, check resource counts, or find out which chips/families (generic/xilinx/intel/microchip) the installed yosys can target, and also when the user wants to build/list the netlist (or top-level) projects of an actual tsfpga project on disk; the server drives tsfpga's Yosys+GHDL netlist build and returns aggregated resource counts, and never guesses top, chip, f...
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
| `tsfpga_project_build` | Builds project(s) by running the project's own build script (netlist builds by default). Returns pass/fail plus the build's own output (utilization report included for netlist builds). | one full build subprocess |

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
- `tsfpga_project_build`: `project_filters` (wildcards, empty = all — call `tsfpga_project_list_builds` first so "all" is an informed choice), `netlist_builds` (default `true`), `use_existing_project` (default `true`, faster iteration; set `false` to force a clean re-create), `num_parallel_builds` (projects built concurrently, tsfpga default `8` — the only parallelism knob netlist builds have, so it only helps when the filters match several projects), `num_threads_per_build` (threads inside one build process, tsfpga default `4`; top-level/Vivado builds only — Yosys netlist synthesis is single-threaded and ignores it, and the tool says so if you set it anyway), `timeout` (override for this call).

All three default to whichever of `build.py`/`build_fpga.py` exists in
the server's current working directory (`build.py` wins if both do), no
env var required. Set `TSFPGA_MCP_PROJECT_DIR` (project's directory)
and/or `TSFPGA_MCP_BUILD_SCRIPT` (script name/path, relative to
`TSFPGA_MCP_PROJECT_DIR` unless absolute) when the project isn't the cwd
or the script has a different name; see `tsfpga_project_status` for
what else is configurable (`TSFPGA_MCP_PROJECT_PYTHON`,
`TSFPGA_MCP_PROJECTS_PATH`, `TSFPGA_MCP_PROJECT_TIMEOUT`,
`TSFPGA_MCP_PROJECT_EXTRA_ARGS`).

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
