"""Timing report retrieval for an already-built Vivado project.

tsfpga's ``check_timing.tcl`` build-step hook (``STEPS.WRITE_BITSTREAM.
TCL.PRE``) only writes ``timing_summary.rpt`` into the implementation run
directory when it detects a setup/hold violation or an unsafe clock
crossing — on a normal, timing-clean build no such file exists at all. So
"what does the timing report say" can't just mean "read the file tsfpga
already wrote" in the common case.

Instead this module regenerates the report on demand by running Vivado in
batch mode against the already-built project, the same way a human would
from the Tcl console: ``open_project`` the ``.xpr``, ``open_run`` the
requested synth/impl run, ``report_timing_summary`` to a file. This is the
only place in the server that invokes Vivado directly — the build tools
never do (tsfpga does that internally, via the project's own build
script), and this module never imports tsfpga either.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .project_config import ProjectConfig
from .project_runner import RunTimeoutError, _kill_process_group, strip_ansi

REPORT_FILENAME = "timing_summary.rpt"
_TCL_FILENAME = "tsfpga_mcp_report_timing_summary.tcl"


class TimingReportError(RuntimeError):
    """Raised for user-actionable lookup/configuration failures."""


@dataclass(frozen=True)
class TimingReportResult:
    report: str
    report_file: Path
    regenerated: bool
    vivado_output: str = ""


def run_name(run_index: int, synth_only: bool) -> str:
    """The Vivado run name for a given run index, e.g. 'impl_1'."""
    return f"{'synth' if synth_only else 'impl'}_{run_index}"


def project_dir(config: ProjectConfig, project: str) -> Path:
    """Where a build project's own Vivado project directory lives.

    Mirrors ``BuildProjectList.get_build_project_path`` in tsfpga:
    ``<projects_path>/<project>/project``.
    """
    return config.projects_path / project / "project"


def xpr_file(config: ProjectConfig, project: str) -> Path:
    return project_dir(config, project) / f"{project}.xpr"


def run_dir(
    config: ProjectConfig, project: str, run_index: int, synth_only: bool
) -> Path:
    return (
        project_dir(config, project)
        / f"{project}.runs"
        / run_name(run_index, synth_only)
    )


def build_tcl(xpr: Path, run: str, output_file: Path) -> str:
    """Tcl script: open the built project's given run and report timing."""
    return (
        f'open_project "{xpr.as_posix()}"\n'
        f'open_run "{run}"\n'
        f'report_timing_summary -max_paths 10 -file "{output_file.as_posix()}"\n'
    )


async def _run_vivado(
    config: ProjectConfig, tcl_file: Path, cwd: Path, timeout: float | None
) -> str:
    """Run Vivado in batch mode against ``tcl_file``; return combined output."""
    assert config.vivado is not None
    argv = [
        config.vivado,
        "-mode",
        "batch",
        "-notrace",
        "-nojournal",
        "-nolog",
        "-source",
        str(tcl_file),
    ]
    limit = timeout if timeout is not None else config.timeout
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        # Own process group so a timeout can kill Vivado's children too.
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=limit)
    except asyncio.TimeoutError:
        _kill_process_group(proc)
        await proc.wait()
        raise RunTimeoutError(
            f"Timed out after {limit:.0f}s: {' '.join(argv)}"
        ) from None
    return strip_ansi(out.decode(errors="replace") + err.decode(errors="replace"))


async def get_timing_report(
    config: ProjectConfig,
    project: str,
    run_index: int,
    synth_only: bool,
    force_regenerate: bool,
    timeout: float | None,
) -> TimingReportResult:
    """Return the timing summary for one build's run, generating it if needed.

    Raises:
        TimingReportError: the project/run hasn't been built, or Vivado is
            needed to regenerate the report but isn't configured.
        RunTimeoutError: Vivado exceeded the timeout while regenerating.
    """
    xpr = xpr_file(config, project)
    if not xpr.is_file():
        raise TimingReportError(
            f"No Vivado project found at {xpr}. Build {project!r} first "
            "with tsfpga_project_build (netlist_builds=False for a "
            "top-level Vivado project), then retry."
        )

    rdir = run_dir(config, project, run_index, synth_only)
    if not rdir.is_dir():
        raise TimingReportError(
            f"Run directory not found: {rdir}. Check 'run_index' and "
            "'synth_only' against the build that actually ran (default "
            "run_index is 1; a netlist build only ever has a 'synth_N' "
            "run, never 'impl_N' — pass synth_only=True for those)."
        )

    report_file = rdir / REPORT_FILENAME
    if report_file.is_file() and not force_regenerate:
        return TimingReportResult(
            report=report_file.read_text(errors="replace"),
            report_file=report_file,
            regenerated=False,
        )

    if config.vivado is None:
        raise TimingReportError(
            "No cached timing_summary.rpt for this run (only written "
            "automatically when tsfpga detects a timing violation) and "
            "Vivado is not available to generate one: set TSFPGA_MCP_VIVADO "
            "to the vivado executable, or add it to PATH."
        )

    tcl_file = rdir / _TCL_FILENAME
    tcl_file.write_text(
        build_tcl(xpr, run_name(run_index, synth_only), report_file), encoding="utf-8"
    )
    output = await _run_vivado(config, tcl_file, cwd=rdir, timeout=timeout)

    if not report_file.is_file():
        raise TimingReportError(
            f"Vivado did not produce {report_file.name}. Output:\n"
            + (output[-4000:] if output else "(no output)")
        )
    return TimingReportResult(
        report=report_file.read_text(errors="replace"),
        report_file=report_file,
        regenerated=True,
        vivado_output=output,
    )
