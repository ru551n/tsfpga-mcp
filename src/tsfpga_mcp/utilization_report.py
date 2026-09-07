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

from .project_config import ProjectConfig
from .vivado_common import ReportResult, VivadoReportError, get_or_regenerate_report

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
