"""Shared machinery for tools that regenerate a Vivado report on demand.

Several tools in this server work the same way: given an already-built
project (a ``.xpr`` plus a completed ``synth_N``/``impl_N`` run), open that
run in Vivado batch mode and ask it to write some report (timing summary,
hierarchical utilization, DRC, methodology, ...) into the run directory,
then read the file back. tsfpga itself only ever writes a
``timing_summary.rpt`` automatically, and only when it detects a timing
violation (see ``timing.py``) — every other report kind here is *never*
written by tsfpga, so regenerating on demand is the only way to get one at
all.

This module factors out the parts that are identical across all of those
report kinds (path conventions, spawning Vivado, caching) so each report
kind's own module only has to supply the one Tcl command that produces it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .project_config import ProjectConfig
from .project_runner import RunTimeoutError, _kill_process_group, run_env, strip_ansi


class VivadoReportError(RuntimeError):
    """Raised for user-actionable lookup/configuration failures."""


@dataclass(frozen=True)
class ReportResult:
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


def output_dir(config: ProjectConfig, project: str) -> Path:
    """Where build artifacts (bitstream, ...) are placed for a project.

    Mirrors ``BuildProjectList.get_build_project_output_path`` in tsfpga
    when no explicit ``--output-path`` is passed (this server never
    passes one): ``<projects_path>/<project>``, a sibling of the
    ``project`` subdirectory returned by ``project_dir``.
    """
    return config.projects_path / project


async def run_vivado_batch(
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
        # Same activated environment as a build: this server's own venv
        # must not leak into Vivado, the project's must be in front.
        env=run_env(config),
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


def build_report_tcl(xpr: Path, run: str, command: str) -> str:
    """Tcl script: open the built project's given run, then run ``command``."""
    return f'open_project "{xpr.as_posix()}"\nopen_run "{run}"\n{command}\n'


async def get_or_regenerate_report(
    config: ProjectConfig,
    *,
    project: str,
    run_index: int,
    synth_only: bool,
    force_regenerate: bool,
    timeout: float | None,
    report_filename: str,
    tcl_filename: str,
    build_command: Callable[[Path], str],
    not_configured_message: str,
) -> ReportResult:
    """Return a cached report for one build's run, generating it if needed.

    ``build_command`` receives the report's target file path and returns
    the Vivado Tcl command that writes it (e.g.
    ``report_utilization -hierarchical -file "<path>"``).

    Raises:
        VivadoReportError: the project/run hasn't been built, or Vivado is
            needed to regenerate the report but isn't configured.
        RunTimeoutError: Vivado exceeded the timeout while regenerating.
    """
    xpr = xpr_file(config, project)
    if not xpr.is_file():
        raise VivadoReportError(
            f"No Vivado project found at {xpr}. Build {project!r} first "
            "with tsfpga_project_build (netlist_builds=False for a "
            "top-level Vivado project), then retry."
        )

    rdir = run_dir(config, project, run_index, synth_only)
    if not rdir.is_dir():
        raise VivadoReportError(
            f"Run directory not found: {rdir}. Check 'run_index' and "
            "'synth_only' against the build that actually ran (default "
            "run_index is 1; a netlist build only ever has a 'synth_N' "
            "run, never 'impl_N' — pass synth_only=True for those)."
        )

    report_file = rdir / report_filename
    if report_file.is_file() and not force_regenerate:
        return ReportResult(
            report=report_file.read_text(errors="replace"),
            report_file=report_file,
            regenerated=False,
        )

    if config.vivado is None:
        raise VivadoReportError(not_configured_message)

    tcl_file = rdir / tcl_filename
    tcl_content = build_report_tcl(
        xpr, run_name(run_index, synth_only), build_command(report_file)
    )
    tcl_file.write_text(tcl_content, encoding="utf-8")
    output = await run_vivado_batch(config, tcl_file, cwd=rdir, timeout=timeout)

    if not report_file.is_file():
        raise VivadoReportError(
            f"Vivado did not produce {report_file.name}. Output:\n"
            + (output[-4000:] if output else "(no output)")
        )
    return ReportResult(
        report=report_file.read_text(errors="replace"),
        report_file=report_file,
        regenerated=True,
        vivado_output=output,
    )
