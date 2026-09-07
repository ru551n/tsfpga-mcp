# tsfpga-mcp

MCP (stdio) server that lets an LLM/agent **synthesize VHDL and Verilog
designs and get back resource counts** (LUTs, FFs, DSPs, block RAMs, ...),
built on [tsfpga](https://github.com/tsfpga/tsfpga)'s
`tsfpga.yosys.project.YosysNetlistBuild` — the same GHDL + Yosys netlist
build tsfpga itself uses for quick resource-utilization feedback.

This repo **replaces
[yosynth-mcp](https://github.com/ru551n/yosynth-mcp)**, which scripted
`yosys`/`ghdl` directly. tsfpga-mcp instead reuses tsfpga's netlist-build
implementation, at the cost of a narrower target list (tsfpga only
supports generic/Xilinx/Intel/Microchip flows, and only reports
aggregated resource counts — no per-port netlist dump).

## How it works

Per synthesis request the server stages the given source files into a
throwaway tsfpga module and drives the matching build class:

- `chip="generic"` → `YosysNetlistBuild` (`synth`) — vendor-independent,
  raw cell counts only.
- `chip="xilinx"` → `YosysXilinxNetlistBuild` (`synth_xilinx`) — LUTs,
  FDs, RAMB18/36, DSP48.
- `chip="intel"` → `YosysIntelNetlistBuild` (`synth_intel`) — MAX10,
  Cyclone IV/IVE/10LP (not the ALM-based Cyclone V/10 GX).
- `chip="microchip"` → `YosysMicrochipNetlistBuild` (`synth_microchip`) —
  PolarFire.

`create()` analyzes the VHDL sources with GHDL; `build()` elaborates the
top (reading any Verilog/SystemVerilog sources first, so mixed-language
designs bind correctly in either direction — a documented GHDL + Yosys
feature) and runs the chip's `synth_*` flow, returning the resource
counts parsed from the utilization report.

The server is **not pinned to a yosys version**: the available synthesis
flows are probed from the installed binary at startup (`tsfpga_status` /
`tsfpga_targets`).

## Requirements

On the `PATH` of the interpreter that runs the server:

1. **yosys** — with the `synth`/`synth_xilinx`/`synth_intel`/
   `synth_microchip` techlibs (stock builds ship all of these).
2. **ghdl** — the CLI, used by tsfpga to analyze VHDL sources.
3. **the ghdl yosys plugin** (`ghdl.so`) — from
   [ghdl-yosys-plugin](https://github.com/ghdl/ghdl-yosys-plugin); set
   `YOSYS_PLUGIN_PATH` or `TSFPGA_MCP_GHDL_PLUGIN` so it can be found.
4. **compiled GHDL std/ieee libraries** — pointed at by `GHDL_PREFIX` /
   `TSFPGA_MCP_GHDL_PREFIX`.

The [`ru551n/hdl-docker`](https://github.com/ru551n/hdl-docker) Docker
image provides all four out of the box — this repo's CI runs in it.

## Setup

Run with `uvx`, straight from git or a local checkout:

```json
{
  "mcpServers": {
    "tsfpga": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/ru551n/tsfpga-mcp.git",
        "tsfpga-mcp"
      ],
      "env": {
        "TSFPGA_MCP_GHDL_PREFIX": "/path/to/ghdl/libs"
      }
    }
  }
}
```

Or with MCP Inspector for manual testing:

```bash
npx @modelcontextprotocol/inspector \
  uvx --from git+https://github.com/ru551n/tsfpga-mcp.git tsfpga-mcp
```

## Configuration (env vars)

| Variable | Meaning | Default |
|---|---|---|
| `TSFPGA_MCP_YOSYS` | yosys binary | `yosys` (on `PATH`) |
| `TSFPGA_MCP_GHDL` | ghdl binary | `ghdl` (on `PATH`) |
| `TSFPGA_MCP_GHDL_PLUGIN` | path to the ghdl plugin (`ghdl.so`) | auto-found via `YOSYS_PLUGIN_PATH` / the yosys share dir |
| `TSFPGA_MCP_GHDL_PREFIX` | dir containing GHDL's compiled `std/`, `ieee/`, `src/` libraries | unset (falls back to `GHDL_PREFIX`, then `ghdl --dispconfig`) |
| `TSFPGA_MCP_TIMEOUT` | max seconds per synthesis | `300` |

### Project mode (env vars)

These configure the `tsfpga_project_*` tools, which drive a real project's
*own* build script (e.g. `build.py`/`build_fpga.py`) as a subprocess, as
opposed to `tsfpga_synthesize`'s ad hoc, in-process staging of loose source
files. See `tsfpga_project_status` to check what a given setup resolves to.

| Variable | Meaning | Default |
|---|---|---|
| `TSFPGA_MCP_PROJECT_DIR` | directory containing the project's build script | the server's current working directory |
| `TSFPGA_MCP_BUILD_SCRIPT` | path to the build script, relative to `TSFPGA_MCP_PROJECT_DIR` unless absolute | whichever of `build.py`/`build_fpga.py` exists in the project dir (`build.py` wins if both do) |
| `TSFPGA_MCP_PROJECT_PYTHON` | interpreter used to run the build script; setting it disables venv auto-creation | the project venv's own python (see below) |
| `TSFPGA_MCP_PROJECT_AUTO_VENV` | create a missing project venv with uv (`0`/`false`/`no`/`off` disables) | enabled |
| `TSFPGA_MCP_UV` | `uv` executable used to create the venv | `uv` on `PATH` |
| `TSFPGA_MCP_PROJECT_VENV_TIMEOUT` | max seconds for venv creation + dependency install | `900` |
| `TSFPGA_MCP_PROJECTS_PATH` | `--projects-path` passed to the build script | `<project dir>/tsfpga_mcp_out/projects` |
| `TSFPGA_MCP_PROJECT_TIMEOUT` | max seconds for one build script invocation | `600` |
| `TSFPGA_MCP_PROJECT_EXTRA_ARGS` | extra arguments appended, verbatim (shell-split), to every build script invocation | unset |
| `TSFPGA_MCP_VIVADO` | `vivado` executable, used only by `tsfpga_project_get_timing_report` to regenerate a timing summary | `vivado` on PATH |

### Project virtualenv

The project's own virtualenv is always used and **activated** for every
build-script (and Vivado) subprocess — `VIRTUAL_ENV` set, `<venv>/bin`
first on `PATH`, `PYTHONHOME` cleared, and this server's own venv removed
from the environment — so nested `python`/`pip`/console-script lookups made
by the build script itself resolve inside it, not just the top-level
interpreter.

Resolution order at startup (project mode only; `tsfpga_synthesize` and the
other ad-hoc tools need no project and no venv):

1. `TSFPGA_MCP_PROJECT_PYTHON`, if set (authoritative; when it points into
   a venv, that venv is activated too, and nothing is ever created).
2. An existing `<project>/.venv`, else `<project>/venv`.
3. Otherwise one is created with `uv`, from whichever of the project's
   dependency declarations works: `uv sync` for a `pyproject.toml`, else
   `uv venv` + `uv pip install -r requirements.txt`, else `uv venv` +
   `uv pip install -r pyproject.toml` (a pyproject that only carries tool
   config falls through to `requirements.txt` instead of failing the run).
4. If the project declares no dependencies, or `uv` is not installed, the
   old behavior applies: `python3`/`python` from `PATH` (this server's own
   venv excluded), and `tsfpga_project_status` reports why.

### Several agents on one code base

Nothing serializes two servers against one checkout: build projects land in
`TSFPGA_MCP_PROJECTS_PATH` (`<project>/tsfpga_mcp_out/projects` by default) and
two agents building the same project name there clobber each other. Give each
agent its own projects path, or — simpler and fully disjoint — its own **git
worktree**, started with that worktree as cwd. Venv provisioning is safe
either way: discovery *and* creation happen under a cross-process lock keyed on
the project path, shared with vunit-mcp (which provisions the same venv), so a
server that arrives mid-install waits for the real thing instead of adopting a
virtualenv that has an interpreter but not yet any packages.

## Tools

| Tool | What it does |
|---|---|
| `tsfpga_synthesize` | Synthesizes the given top level for a chip/family with optional generic overrides, and returns the resource counts or the failure diagnostics. |
| `tsfpga_inspect` | Static scan of the sources: VHDL entities with architectures and generics (name/type/default), Verilog modules with parameters, plus ambiguities. Nothing is compiled. |
| `tsfpga_targets` | The chip targets this server can synthesize for: per chip, the yosys flow, whether it exists in the installed yosys, and the known device families. |
| `tsfpga_status` | Server config: yosys version, available flows, plugin path, `GHDL_PREFIX`, timeout. Call it first when things look misconfigured. |
| `tsfpga_project_status` | Project-mode config: resolved project dir, build script, interpreter, projects path, timeout, resolved `vivado` executable. Call it first to confirm what it resolved to. |
| `tsfpga_project_list_builds` | Lists the project's own build projects (`build.py --list-only`), netlist builds by default. Use to find project name filters. |
| `tsfpga_project_build` | Builds project(s) by running the project's own build script (netlist builds by default). Returns pass/fail plus the build's own output (utilization report included for netlist builds). `num_parallel_builds` builds several matched projects concurrently — the only parallelism netlist builds have, since `num_threads_per_build` (threads within one build) is used by top-level Vivado builds only. For top-level (Vivado) builds, `synth_only` stops after synthesis and `from_impl` resumes a `synth_only` build into a full implementation (place & route + bitstream). On success (full top-level builds only) the written bitstream artifact paths (`.bit`/`.bin`/`.xsa`) are listed; on failure, every Vivado `ERROR:`/`CRITICAL WARNING:` line (plus surrounding context) is surfaced first for triage, ahead of the full output. |
| `tsfpga_project_get_timing_report` | Gets a Vivado timing-analysis report for an already-built project's run (`synth_N`/`impl_N`): `report_type` selects `summary` (default), `pulse_width`, `bus_skew`, or `clock_interaction`. tsfpga only ever writes `timing_summary.rpt` automatically, and only on a timing violation, so for any other case (or report type) this runs Vivado in batch mode (`open_project`/`open_run`/the matching `report_*` command) to generate one on demand — needs `vivado` on PATH or `TSFPGA_MCP_VIVADO` set. For `report_type=summary`, `verbosity=summary` returns just a structured WNS/TNS/WHS/THS header plus the worst failing endpoints instead of the full raw report. `force_regenerate` re-runs Vivado even if a cached report exists. |
| `tsfpga_project_get_utilization_report` | Gets a hierarchical Vivado utilization report (per-module LUT/FF/BRAM/DSP/... breakdown, `hierarchical_depth` levels deep) for an already-built project's run. tsfpga itself already writes this at depth 4 for every build, so the default depth is normally served straight from that file with no Vivado call; any other depth (or `force_regenerate`) runs Vivado (`report_utilization -hierarchical`) to produce and cache its own. |
| `tsfpga_project_get_drc_report` | Gets a Vivado DRC or methodology report (`report_type`: `drc`/`methodology`) for an already-built project's run. tsfpga never runs either as part of its own build flow, so this always runs Vivado (`report_drc`/`report_methodology`) on demand unless a cached report from a previous call already exists — needs `vivado` on PATH or `TSFPGA_MCP_VIVADO` set. |

### Example

`tsfpga_synthesize(sources=["counter.vhd"], top="counter", chip="xilinx",
family="xc7", generics={"WIDTH": "8"})`:

```
Synthesis OK: top `counter` -> xilinx (xc7) in 0.6s.

Resources:
  Total LUTs  3
  FFs         8
```

### Notes

- **`sources`** must cover everything the top needs, and base names must
  be **unique** across the list (all sources are staged into one flat
  directory).
- **Generics** are only supported for a VHDL top level; the declared VHDL
  type (from `tsfpga_inspect`) decides how each value is interpreted
  (boolean/integer/real/vector/string).
- **`vhdl_entities`** is only needed when `top` is a Verilog/SystemVerilog
  module (or the design has no VHDL at all): list the VHDL entity names
  it (or other VHDL entities) instantiate.
- **`family`** is accepted for `xilinx`/`intel`/`microchip` only; not for
  `generic`.
- No per-port netlist is returned — only aggregated resource counts, per
  tsfpga's own `BuildResult.synthesis_size`.

## Skill

This repo ships an agent skill, `skills/tsfpga-mcp/SKILL.md`, that tells
the LLM *when* and *how* to use the tools — including the hard rule that
the top level, chip/family and generic values must be known or **asked
from the user**, never inferred. Install it next to the server so the
agent picks it up automatically.

### Claude Code

```bash
# personal — available in every project
ln -s /path/to/tsfpga-mcp/skills/tsfpga-mcp ~/.claude/skills/tsfpga-mcp

# or project-local — available only in that project
mkdir -p <your-project>/.claude/skills
ln -s /path/to/tsfpga-mcp/skills/tsfpga-mcp <your-project>/.claude/skills/tsfpga-mcp
```

## Development

```bash
uv sync
uv run ruff check .
uv run mypy
uv run pytest -q
```

End-to-end tests need a full yosys + ghdl + ghdl-yosys-plugin + compiled
GHDL std/ieee setup; they are skipped (not failed) without one. The
[`ru551n/hdl-docker`](https://github.com/ru551n/hdl-docker) image provides
this out of the box.
