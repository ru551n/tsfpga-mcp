---
name: tsfpga-mcp
description: Synthesize VHDL/Verilog designs and check resource usage (LUTs/FFs/DSPs/block RAMs) through the tsfpga-mcp MCP server (tsfpga_status, tsfpga_inspect, tsfpga_targets, tsfpga_synthesize). Use when the user asks to synthesize an entity or module, check resource counts, or find out which chips/families (generic/xilinx/intel/microchip) the installed yosys can target; the server drives tsfpga's Yosys+GHDL netlist build and returns aggregated resource counts, and never guesses top, chip, family or generic values.
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
