"""Hierarchical utilization report retrieval for an already-built Vivado project.

tsfpga *does* write a ``hierarchical_utilization.rpt`` automatically for
every build (``report_utilization -hierarchical -hierarchical_depth 4``,
run unconditionally right after synthesis, and again — duplicated — right
before ``write_bitstream`` for full builds), unlike ``timing_summary.rpt``
et al which only appear on a violation. It always uses depth 4 (tsfpga
uses the file itself afterwards to compute the top-level size it prints),
so a request for the default ``hierarchical_depth=4`` can — and should —
be served straight from that existing file without spinning up Vivado
again.

A request for any other depth needs its own regenerated report and its
own cache file: depth changes what Vivado actually reports, so reusing
the depth-4 filename for a different depth would silently serve a stale
report generated at a different depth (mirrors how ``timing.py`` keys its
cache on ``report_type``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .project_config import ProjectConfig
from .vivado_common import ReportResult, VivadoReportError, get_or_regenerate_report

__all__ = [
    "REPORT_FILENAME",
    "UtilizationReportError",
    "UtilizationSummary",
    "get_utilization_report",
    "parse_utilization_summary",
]

# The depth tsfpga itself always uses when it writes this report.
_TSFPGA_DEFAULT_DEPTH = 4

# Kept for backwards compatibility; the depth-4 filename tsfpga itself writes.
REPORT_FILENAME = "hierarchical_utilization.rpt"
_TCL_FILENAME = "tsfpga_mcp_report_utilization.tcl"


class UtilizationReportError(VivadoReportError):
    """Raised for user-actionable lookup/configuration failures."""


def _report_filename(hierarchical_depth: int) -> str:
    """Cache filename for a given depth.

    Depth 4 reuses tsfpga's own filename, since that's what tsfpga itself
    writes at depth 4 for every build — no regeneration needed. Any other
    depth gets its own filename so it's never served (or serves) a
    depth-4 report by mistake.
    """
    if hierarchical_depth == _TSFPGA_DEFAULT_DEPTH:
        return REPORT_FILENAME
    return f"hierarchical_utilization_depth{hierarchical_depth}.rpt"


async def get_utilization_report(
    config: ProjectConfig,
    project: str,
    run_index: int,
    synth_only: bool,
    force_regenerate: bool,
    timeout: float | None,
    hierarchical_depth: int = 4,
) -> ReportResult:
    """Return a hierarchical utilization report for one build's run.

    Regenerates via Vivado's ``report_utilization -hierarchical`` if no
    cached report exists for this run and depth (or
    ``force_regenerate=True``).

    Raises:
        UtilizationReportError: the project/run hasn't been built, or
            Vivado is needed to regenerate the report but isn't
            configured.
        RunTimeoutError: Vivado exceeded the timeout while regenerating.
    """
    report_filename = _report_filename(hierarchical_depth)
    try:
        return await get_or_regenerate_report(
            config,
            project=project,
            run_index=run_index,
            synth_only=synth_only,
            force_regenerate=force_regenerate,
            timeout=timeout,
            report_filename=report_filename,
            tcl_filename=_TCL_FILENAME,
            build_command=lambda f: (
                "report_utilization -hierarchical "
                f'-hierarchical_depth {hierarchical_depth} -file "{f.as_posix()}"'
            ),
            not_configured_message=(
                f"No cached {report_filename} for this run "
                + (
                    "(tsfpga writes this automatically, but only at "
                    f"the default depth {_TSFPGA_DEFAULT_DEPTH} — "
                    "this project may not have finished building yet) "
                    if hierarchical_depth == _TSFPGA_DEFAULT_DEPTH
                    else "(tsfpga never writes this report at a non-default "
                    "hierarchical_depth) "
                )
                + "and Vivado is not "
                "available to generate one: set TSFPGA_MCP_VIVADO to the "
                "vivado executable, or add it to PATH."
            ),
        )
    except VivadoReportError as exc:
        raise UtilizationReportError(str(exc)) from exc


# --- Structured "Utilization by Hierarchy" table parsing --------------------

# Vivado's report_utilization -hierarchical output is a single ASCII table
# (bordered by "+---+" rules) whose first two columns are always "Instance"
# and "Module" — the remaining resource columns vary by Vivado version and
# device family (older releases: "Slice LUTs"/"Slice Registers"/"BRAM Tile"/
# "DSPs"; newer ones (seen from an actual 2026.1 run): "Total LUTs"/
# "Logic LUTs"/"LUTRAMs"/"SRLs"/"FFs"/"RAMB36"/"RAMB18"/"DSP Blocks"). The
# first data row is always the top-level instance (the whole design), with
# every other row nested underneath it.
_HIER_TABLE_RE = re.compile(
    r"\+-+(?:\+-+)+\+\n"
    r"\|(?P<header>[^\n]*\bInstance\b[^\n]*\bModule\b[^\n]*)\|\n"
    r"\+-+(?:\+-+)+\+\n"
    r"(?P<rows>(?:\|[^\n]*\|\n)+?)"
    r"\+-+(?:\+-+)+\+",
)

# Known aliases for the commonly-present resource categories, newest/most
# specific first. Anything not covered here is still preserved verbatim in
# UtilizationSummary.values.
_LUT_ALIASES = ("Slice LUTs", "Total LUTs", "LUTs")
_REGISTER_ALIASES = ("Slice Registers", "FFs", "Registers")
_DSP_ALIASES = ("DSPs", "DSP48Es", "DSP48s", "DSP Blocks")
_BRAM_TILE_ALIASES = ("Block RAM Tile", "BRAM Tile", "Block RAM")


def _split_row(line: str) -> list[str]:
    """Split one "| a | b | c |" table row/header line into stripped cells."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _first_present(values: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in values:
            return values[name]
    return None


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _block_ram_tiles(values: dict[str, str]) -> float | None:
    direct = _to_float(_first_present(values, _BRAM_TILE_ALIASES))
    if direct is not None:
        return direct
    ramb36 = _to_int(values.get("RAMB36"))
    ramb18 = _to_int(values.get("RAMB18"))
    if ramb36 is None and ramb18 is None:
        return None
    # A RAMB18 is half a Block RAM Tile (a RAMB36 is a whole one) — this is
    # how Vivado itself defines "Block RAM Tile" utilization.
    return (ramb36 or 0) + (ramb18 or 0) * 0.5


@dataclass(frozen=True)
class UtilizationSummary:
    top_instance: str | None
    top_module: str | None
    slice_luts: int | None
    slice_registers: int | None
    block_ram_tiles: float | None
    dsps: int | None
    values: dict[str, str]
    hierarchy: list[dict[str, str]]

    def render(self) -> str:
        if self.top_instance is None:
            return "Utilization summary: could not determine from report text."
        lines = [
            f"Top-level utilization ({self.top_instance!r}, "
            f"module {self.top_module!r}):"
        ]
        if self.slice_luts is not None:
            lines.append(f"  LUTs: {self.slice_luts}")
        if self.slice_registers is not None:
            lines.append(f"  Registers/FFs: {self.slice_registers}")
        if self.block_ram_tiles is not None:
            lines.append(f"  Block RAM tiles: {self.block_ram_tiles:g}")
        if self.dsps is not None:
            lines.append(f"  DSPs: {self.dsps}")
        if len(self.hierarchy) > 1:
            lines.append(
                f"Hierarchy breakdown: {len(self.hierarchy)} instances "
                "(see full report for per-instance detail)."
            )
        return "\n".join(lines)


def parse_utilization_summary(report: str) -> UtilizationSummary:
    """Extract the top-level resource totals from a hierarchical utilization report.

    Best-effort text parsing of ``report_utilization -hierarchical`` output.
    Returns an "unknown" summary (all fields ``None``/empty) rather than
    raising when the report doesn't look as expected, so callers can always
    fall back to showing the raw report.
    """
    table_match = _HIER_TABLE_RE.search(report)
    if not table_match:
        return UtilizationSummary(
            top_instance=None,
            top_module=None,
            slice_luts=None,
            slice_registers=None,
            block_ram_tiles=None,
            dsps=None,
            values={},
            hierarchy=[],
        )

    header = _split_row(table_match.group("header"))
    row_lines = [
        line for line in table_match.group("rows").split("\n") if line.strip()
    ]
    hierarchy: list[dict[str, str]] = []
    for line in row_lines:
        cells = _split_row(line)
        if len(cells) == len(header):
            hierarchy.append(dict(zip(header, cells, strict=True)))

    if not hierarchy:
        return UtilizationSummary(
            top_instance=None,
            top_module=None,
            slice_luts=None,
            slice_registers=None,
            block_ram_tiles=None,
            dsps=None,
            values={},
            hierarchy=[],
        )

    top = hierarchy[0]
    return UtilizationSummary(
        top_instance=top.get("Instance"),
        top_module=top.get("Module"),
        slice_luts=_to_int(_first_present(top, _LUT_ALIASES)),
        slice_registers=_to_int(_first_present(top, _REGISTER_ALIASES)),
        block_ram_tiles=_block_ram_tiles(top),
        dsps=_to_int(_first_present(top, _DSP_ALIASES)),
        values=top,
        hierarchy=hierarchy,
    )
