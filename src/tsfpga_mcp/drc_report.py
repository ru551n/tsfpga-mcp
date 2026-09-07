"""DRC / methodology report retrieval for an already-built Vivado project.

tsfpga never runs ``report_drc``/``report_methodology`` as part of its own
build flow, so — same as ``utilization_report.py`` — this always
regenerates the requested report on demand via Vivado batch mode.
"""

from __future__ import annotations

import re

from .project_config import ProjectConfig
from .vivado_common import ReportResult, VivadoReportError, get_or_regenerate_report

REPORT_FILENAMES: dict[str, str] = {
    "drc": "drc.rpt",
    "methodology": "methodology.rpt",
}
_TCL_COMMANDS: dict[str, str] = {
    "drc": "report_drc",
    "methodology": "report_methodology",
}
_TCL_FILENAMES: dict[str, str] = {
    "drc": "tsfpga_mcp_report_drc.tcl",
    "methodology": "tsfpga_mcp_report_methodology.tcl",
}

_CHECKS_FOUND_RE = re.compile(r"Checks found:\s*(\d+)")


class DrcReportError(VivadoReportError):
    """Raised for user-actionable lookup/configuration failures."""


def checks_found(report: str) -> int | None:
    """Number of violations from a DRC/methodology report's summary section."""
    match = _CHECKS_FOUND_RE.search(report)
    return int(match.group(1)) if match else None


async def get_drc_report(
    config: ProjectConfig,
    project: str,
    run_index: int,
    synth_only: bool,
    force_regenerate: bool,
    timeout: float | None,
    report_type: str = "drc",
) -> ReportResult:
    """Return a DRC or methodology report for one build's run.

    ``report_type`` is ``"drc"`` (``report_drc``, physical/electrical
    design-rule violations) or ``"methodology"`` (``report_methodology``,
    design-methodology best-practice checks, e.g. large setup violations).

    Raises:
        DrcReportError: the project/run hasn't been built, ``report_type``
            is invalid, or Vivado is needed to regenerate the report but
            isn't configured.
        RunTimeoutError: Vivado exceeded the timeout while regenerating.
    """
    if report_type not in REPORT_FILENAMES:
        raise DrcReportError(
            f"Invalid report_type {report_type!r}. Must be one of: "
            + ", ".join(sorted(REPORT_FILENAMES))
        )

    try:
        return await get_or_regenerate_report(
            config,
            project=project,
            run_index=run_index,
            synth_only=synth_only,
            force_regenerate=force_regenerate,
            timeout=timeout,
            report_filename=REPORT_FILENAMES[report_type],
            tcl_filename=_TCL_FILENAMES[report_type],
            build_command=(
                lambda f: f'{_TCL_COMMANDS[report_type]} -file "{f.as_posix()}"'
            ),
            not_configured_message=(
                f"No cached {REPORT_FILENAMES[report_type]} for this run "
                "(tsfpga never writes this report automatically) and "
                "Vivado is not available to generate one: set "
                "TSFPGA_MCP_VIVADO to the vivado executable, or add it to "
                "PATH."
            ),
        )
    except VivadoReportError as exc:
        raise DrcReportError(str(exc)) from exc
