"""Timing-analysis report retrieval for an already-built Vivado project.

tsfpga's ``check_timing.tcl`` build-step hook (``STEPS.WRITE_BITSTREAM.
TCL.PRE``) only writes ``timing_summary.rpt`` into the implementation run
directory when it detects a setup/hold violation or an unsafe clock
crossing — on a normal, timing-clean build no such file exists at all. So
"what does the timing report say" can't just mean "read the file tsfpga
already wrote" in the common case.

Instead this module regenerates the report on demand by running Vivado in
batch mode against the already-built project, the same way a human would
from the Tcl console: ``open_project`` the ``.xpr``, ``open_run`` the
requested synth/impl run, then one of Vivado's timing-analysis report
commands to a file. This is the only place in the server that invokes
Vivado directly — the build tools never do (tsfpga does that internally,
via the project's own build script), and this module never imports tsfpga
either. The actual Vivado-invocation/caching machinery is shared with
``utilization_report.py``/``drc_report.py`` via ``vivado_common``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .project_config import ProjectConfig
from .vivado_common import (
    ReportResult,
    VivadoReportError,
    get_or_regenerate_report,
    project_dir,
    run_dir,
    run_name,
    xpr_file,
)

__all__ = [
    "REPORT_FILENAMES",
    "TimingReportError",
    "TimingReportResult",
    "TimingSummary",
    "build_tcl",
    "get_timing_report",
    "parse_timing_summary",
    "project_dir",
    "run_dir",
    "run_name",
    "xpr_file",
]

# Kept for backwards compatibility; new code should use REPORT_FILENAMES.
REPORT_FILENAME = "timing_summary.rpt"

REPORT_FILENAMES: dict[str, str] = {
    "summary": "timing_summary.rpt",
    "pulse_width": "pulse_width.rpt",
    "bus_skew": "bus_skew.rpt",
    "clock_interaction": "clock_interaction.rpt",
}

_TCL_COMMANDS: dict[str, str] = {
    "summary": 'report_timing_summary -max_paths 10 -file "{file}"',
    "pulse_width": 'report_pulse_width -file "{file}"',
    "bus_skew": 'report_bus_skew -file "{file}"',
    "clock_interaction": 'report_clock_interaction -file "{file}"',
}

_TCL_FILENAMES: dict[str, str] = {
    "summary": "tsfpga_mcp_report_timing_summary.tcl",
    "pulse_width": "tsfpga_mcp_report_pulse_width.tcl",
    "bus_skew": "tsfpga_mcp_report_bus_skew.tcl",
    "clock_interaction": "tsfpga_mcp_report_clock_interaction.tcl",
}


class TimingReportError(VivadoReportError):
    """Raised for user-actionable lookup/configuration failures."""


# Kept as an alias for backwards compatibility with the pre-refactor API.
TimingReportResult = ReportResult


def build_tcl(
    xpr: Path, run: str, output_file: Path, report_type: str = "summary"
) -> str:
    """Tcl script: open the built project's given run and request a report."""
    command = _TCL_COMMANDS[report_type].format(file=output_file.as_posix())
    return f'open_project "{xpr.as_posix()}"\nopen_run "{run}"\n{command}\n'


async def get_timing_report(
    config: ProjectConfig,
    project: str,
    run_index: int,
    synth_only: bool,
    force_regenerate: bool,
    timeout: float | None,
    report_type: str = "summary",
) -> ReportResult:
    """Return a timing-analysis report for one build's run, generating it if needed.

    ``report_type`` selects which Vivado report command is used:

    - ``"summary"`` (default): ``report_timing_summary``, the overall
      setup/hold/pulse-width timing summary. The only kind tsfpga itself
      ever writes automatically (and only on violation).
    - ``"pulse_width"``: ``report_pulse_width``, minimum pulse width and
      clock period checks.
    - ``"bus_skew"``: ``report_bus_skew``, skew across buses with
      ``set_bus_skew`` constraints.
    - ``"clock_interaction"``: ``report_clock_interaction``, how each pair
      of clock domains is handled (safe crossing, false path, unsafe, ...).

    Raises:
        TimingReportError: the project/run hasn't been built, or Vivado is
            needed to regenerate the report but isn't configured, or
            ``report_type`` is invalid.
        RunTimeoutError: Vivado exceeded the timeout while regenerating.
    """
    if report_type not in REPORT_FILENAMES:
        raise TimingReportError(
            f"Invalid report_type {report_type!r}. Must be one of: "
            + ", ".join(sorted(REPORT_FILENAMES))
        )

    if report_type == "summary":
        not_configured_message = (
            "No cached timing_summary.rpt for this run (only written "
            "automatically when tsfpga detects a timing violation) and "
            "Vivado is not available to generate one: set TSFPGA_MCP_VIVADO "
            "to the vivado executable, or add it to PATH."
        )
    else:
        not_configured_message = (
            f"No cached {REPORT_FILENAMES[report_type]} for this run "
            "(tsfpga never writes this report kind automatically) and "
            "Vivado is not available to generate one: set TSFPGA_MCP_VIVADO "
            "to the vivado executable, or add it to PATH."
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
            build_command=lambda f: _TCL_COMMANDS[report_type].format(
                file=f.as_posix()
            ),
            not_configured_message=not_configured_message,
        )
    except VivadoReportError as exc:
        raise TimingReportError(str(exc)) from exc


# --- Structured "Design Timing Summary" header parsing ---------------------

_SUMMARY_TABLE_RE = re.compile(
    r"\|\s*Design Timing Summary\s*\n.*?\n-+\n\n"
    r"(?P<header>.+)\n"
    r"\s*-+(?:\s+-+)*\n"
    r"(?P<values>.+)\n",
)
_CONSTRAINTS_RE = re.compile(r"Timing constraints are (not )?met")
_VIOLATED_ENDPOINT_RE = re.compile(
    r"Slack \(VIOLATED\)\s*:\s*(?P<slack>-?[\d.]+)ns[^\n]*\n"
    r"\s*Source:\s*(?P<source>\S+).*?\n"
    r"(?:(?!\s*Destination:).*\n)*?"
    r"\s*Destination:\s*(?P<destination>\S+)"
)


@dataclass(frozen=True)
class TimingSummary:
    constraints_met: bool | None
    values: dict[str, str]
    failing_endpoints: list[dict[str, str]]

    def render(self, max_endpoints: int = 5) -> str:
        lines = []
        if self.constraints_met is None:
            lines.append("Timing constraints: could not determine from report text.")
        elif self.constraints_met:
            lines.append("Timing constraints: MET")
        else:
            lines.append("Timing constraints: NOT MET")
        for key in (
            "WNS(ns)",
            "TNS(ns)",
            "TNS Failing Endpoints",
            "WHS(ns)",
            "THS(ns)",
            "THS Failing Endpoints",
            "WPWS(ns)",
            "TPWS(ns)",
            "TPWS Failing Endpoints",
        ):
            if key in self.values:
                lines.append(f"  {key}: {self.values[key]}")
        if self.failing_endpoints:
            lines.append("Worst failing endpoints (report order, setup before hold):")
            for ep in self.failing_endpoints[:max_endpoints]:
                lines.append(
                    f"  slack {ep['slack']}ns: {ep['source']} -> {ep['destination']}"
                )
        return "\n".join(lines)


def parse_timing_summary(report: str) -> TimingSummary:
    """Extract WNS/TNS/WHS/THS/... and the worst failing endpoints.

    Best-effort text parsing of ``report_timing_summary`` output. Returns
    an "unknown" summary (``constraints_met=None``, empty ``values``)
    rather than raising when the report doesn't look as expected (e.g. a
    different report_type's text was passed in), so callers can always
    fall back to showing the raw report.
    """
    table_match = _SUMMARY_TABLE_RE.search(report)
    values: dict[str, str] = {}
    if table_match:
        names = re.split(r"\s{2,}", table_match.group("header").strip())
        raw_values = table_match.group("values").split()
        if len(names) == len(raw_values):
            values = dict(zip(names, raw_values, strict=True))

    constraints_match = _CONSTRAINTS_RE.search(report)
    constraints_met = (
        None if constraints_match is None else not constraints_match.group(1)
    )

    failing_endpoints = [
        {
            "slack": m.group("slack"),
            "source": m.group("source"),
            "destination": m.group("destination"),
        }
        for m in _VIOLATED_ENDPOINT_RE.finditer(report)
    ]

    return TimingSummary(
        constraints_met=constraints_met,
        values=values,
        failing_endpoints=failing_endpoints,
    )
