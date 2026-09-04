"""tsfpga-mcp: MCP server for netlist synthesis via tsfpga + GHDL + Yosys.

Each synthesis stages the given source files into a throwaway tsfpga
module and drives ``tsfpga.yosys.project.YosysNetlistBuild`` (or its
Xilinx/Intel/Microchip subclass) to analyze (GHDL), elaborate and
synthesize (Yosys) the design, then reports the aggregated resource
counts from the utilization report. See ``synth.py`` for the mechanics
and its module docstring for what tsfpga does and does not support
(no port-level netlist dump, no chip targets beyond generic/Xilinx/
Intel/Microchip, generics only for a VHDL top level).
"""

from __future__ import annotations

import asyncio
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .capabilities import Capabilities, render_targets
from .config import Config, ConfigError, load_config
from .inspect import inspect_sources, render_inspection
from .synth import SynthError, build_failure, build_success, chip_spec, synthesize

mcp = FastMCP(
    "tsfpga_mcp",
    instructions=(
        "Synthesize VHDL and Verilog designs with tsfpga's Yosys + GHDL "
        "netlist build and get back the resource counts (LUTs, FFs, "
        "DSPs, block RAMs, ...). Workflow: tsfpga_inspect the sources to "
        "discover top levels and generics; tsfpga_targets to see which "
        "chips/families this yosys supports; then tsfpga_synthesize with "
        "top, chip (generic/xilinx/intel/microchip) and family where "
        "needed, and generic overrides. Ask the user when the top, chip, "
        "family or generic value cannot be inferred."
    ),
)

_config: Config | None = None
_capabilities: Capabilities | None = None


def _get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_capabilities(config: Config) -> Capabilities:
    global _capabilities
    if _capabilities is None or _capabilities.yosys != config.yosys:
        _capabilities = Capabilities(config.yosys)
    return _capabilities


def _err(exc: Exception) -> str:
    if isinstance(exc, ConfigError):
        return f"Configuration error: {exc}"
    return f"Error: {exc}"


class SynthesizeInput(BaseModel):
    """Input for tsfpga_synthesize."""

    model_config = ConfigDict(str_strip_whitespace=True)

    sources: list[str] = Field(
        default_factory=list,
        description=(
            "HDL source files (.vhd/.vhdl and/or .v/.sv) with no explicit "
            "library: staged into the single library named after 'top'. "
            "All units the top level needs must be covered by these files "
            "plus 'libraries'. Base names must be unique within this list "
            "(they may repeat across different 'libraries' entries)."
        ),
    )
    libraries: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Explicit per-library source grouping: VHDL library name -> "
            "its source files, for designs whose sources must be analyzed "
            "into more than one VHDL library — e.g. a top level that uses "
            "'library <name>; entity <name>.<entity>' to cross into a "
            "sibling library, as produced by tsfpga's own per-module-folder "
            "library convention (tsfpga.module.get_modules()). Each named "
            "library's files are staged and GHDL-analyzed as that library. "
            "Base names must be unique within each library, but may repeat "
            "across different libraries (including the 'sources' library). "
            "Combine with 'sources' for files that belong in the (single) "
            "library named after 'top'."
        ),
    )
    top: str = Field(
        description=(
            "Top level name: the VHDL entity or Verilog/SystemVerilog "
            "module that is synthesized (no library prefix)."
        ),
        min_length=1,
    )
    chip: Literal["generic", "xilinx", "intel", "microchip"] = Field(
        default="generic",
        description=(
            "Target: generic (vendor-independent, default), xilinx, "
            "intel, microchip. Use tsfpga_targets to see which flows the "
            "installed yosys actually provides."
        ),
    )
    family: str | None = Field(
        default=None,
        description=(
            "Device family, where the flow supports it (e.g. 'xc7' for "
            "xilinx, 'cycloneiv' for intel, 'polarfire' for microchip). "
            "Not accepted for chip='generic'. See tsfpga_targets for the "
            "known values."
        ),
    )
    vhdl_entities: list[str] = Field(
        default_factory=list,
        description=(
            "Only used when 'top' is NOT a VHDL entity (i.e. it is a "
            "Verilog/SystemVerilog module, or the design has no VHDL at "
            "all): the names of the VHDL entities that shall be made "
            "available for instantiation from the non-VHDL top. Leave "
            "empty when 'top' is a VHDL entity — its VHDL dependencies "
            "are found automatically."
        ),
    )
    generics: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "VHDL generic overrides, name -> value, e.g. {'WIDTH': '8'}. "
            "Only supported when 'top' is a VHDL entity; the declared "
            "VHDL type (from tsfpga_inspect) decides how the value is "
            "interpreted (boolean/integer/real/vector/string)."
        ),
    )
    vhdl_standard: Literal["93", "08", "19"] = Field(
        default="08",
        description="VHDL standard for the GHDL frontend.",
    )
    discard_ffinit: bool = Field(
        default=False,
        description=(
            "Microchip only: discard flip-flop initial values that can't "
            "be legalized instead of failing synthesis "
            "(synth_microchip -discard-ffinit)."
        ),
    )
    timeout: float | None = Field(
        default=None,
        ge=1,
        le=3600,
        description="Max seconds for this run (default: TSFPGA_MCP_TIMEOUT).",
    )

    @model_validator(mode="after")
    def _check_has_sources(self) -> SynthesizeInput:
        if not self.sources and not any(self.libraries.values()):
            raise ValueError(
                "Provide at least one source file via 'sources' and/or 'libraries'."
            )
        return self


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False))
async def tsfpga_synthesize(input: SynthesizeInput) -> str:
    """Synthesize a VHDL or Verilog design and return the resource counts.

    Stages 'sources' (library named after 'top') and each 'libraries'
    entry (its own named library) into one tsfpga module per library,
    runs GHDL analysis + the chip's Yosys synth flow via
    tsfpga.yosys.project, and returns the aggregated resource counts
    (LUTs, FFs, DSPs, block RAMs — or raw cell counts for chip='generic')
    from the utilization report. Use 'libraries' when the design spans
    multiple VHDL libraries (cross-library 'library x; entity x.y'
    references). On failure returns the captured diagnostics."""
    try:
        config = _get_config()
        caps = _get_capabilities(config)
        await caps.ensure()
        spec = chip_spec(input.chip)
        if not caps.has_flow(spec.flow):
            return (
                f"Error: yosys ({caps.version}) does not provide the "
                f"'{spec.flow}' flow for chip {input.chip!r}. Call "
                "tsfpga_targets to see which chips are available, or "
                "install a yosys built with that techlib."
                + (f" (probe error: {caps.probe_error})" if caps.probe_error else "")
            )
        timeout = input.timeout if input.timeout is not None else config.timeout
        result = await asyncio.wait_for(
            asyncio.to_thread(
                synthesize,
                config=config,
                sources=input.sources,
                libraries=input.libraries,
                top=input.top,
                chip=input.chip,
                family=input.family,
                vhdl_entities=input.vhdl_entities,
                generics=input.generics,
                vhdl_standard=input.vhdl_standard,
                discard_ffinit=input.discard_ffinit,
            ),
            timeout=timeout,
        )
    except (ConfigError, SynthError) as exc:
        return f"Error: {exc}"
    except TimeoutError:
        return f"Error: synthesis exceeded the {timeout:.0f}s timeout."

    if result.success:
        return build_success(
            top=input.top,
            chip=input.chip,
            family=input.family,
            resources=result.resources,
            elapsed=result.elapsed,
        )
    return build_failure(result.output, result.elapsed)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def tsfpga_status() -> str:
    """Report the synthesis setup: yosys version, available synthesis
    flows, ghdl plugin path, GHDL library prefix, ghdl binary and default
    timeout. Call this first when a synthesis fails with
    configuration-looking errors."""
    try:
        config = _get_config()
    except ConfigError as exc:
        return _err(exc)
    caps = _get_capabilities(config)
    await caps.ensure()
    lines = [
        f"tsfpga-mcp {__version__}",
        f"yosys: {config.yosys} ({caps.version})",
        f"ghdl: {config.ghdl}",
        f"synthesis flows: {', '.join(sorted(caps.flows)) or '(none found)'}",
        f"ghdl plugin: {config.plugin}"
        + (" (exists)" if config.plugin.is_file() else " (MISSING)"),
        f"GHDL_PREFIX: {config.ghdl_prefix or 'unset (tsfpga uses ghdl defaults)'}",
        f"default timeout: {config.timeout:.0f}s",
    ]
    if caps.probe_error:
        lines.append(f"warning: {caps.probe_error}")
    return "\n".join(lines)


class InspectInput(BaseModel):
    """Input for tsfpga_inspect."""

    model_config = ConfigDict(str_strip_whitespace=True)

    sources: list[str] = Field(
        description=(
            "HDL source files (.vhd/.vhdl and/or .v/.sv) to scan. The "
            "files only need to be readable; nothing is compiled."
        ),
        min_length=1,
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def tsfpga_inspect(input: InspectInput) -> str:
    """List the synthesizable units in the given sources: VHDL entities
    with their architectures and generics (name, type, default), Verilog
    modules with their parameters (name, default). Use this before
    tsfpga_synthesize to pick the top level and to find out which
    generic values exist; if there are several architectures for the top
    entity or no obvious chip/family, ask the user."""
    try:
        _get_config()
    except ConfigError as exc:
        return _err(exc)
    return render_inspection(inspect_sources(input.sources))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def tsfpga_targets() -> str:
    """List the chip targets this server can synthesize for: the yosys
    synthesis flow (synth, synth_xilinx, synth_intel, synth_microchip) of
    each chip, whether that flow exists in the installed yosys, and the
    known device families. Use it to answer 'which chip/family can I
    synthesize for?' before asking the user."""
    try:
        config = _get_config()
    except ConfigError as exc:
        return _err(exc)
    caps = _get_capabilities(config)
    await caps.ensure()
    if caps.probe_error:
        return f"Error: {caps.probe_error}"
    return render_targets(caps)


def main() -> None:
    mcp.run()
