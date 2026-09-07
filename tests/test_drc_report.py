"""Tests for tsfpga_mcp.drc_report (Vivado DRC/methodology reports).

No real Vivado available: a small executable Python stub stands in for
it, same pattern as test_timing.py.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from tsfpga_mcp.drc_report import DrcReportError, checks_found, get_drc_report
from tsfpga_mcp.project_config import ProjectConfig
from tsfpga_mcp.timing import project_dir, run_dir

_FAKE_VIVADO = f"""\
#!{sys.executable}
import re
import sys

content = open(sys.argv[-1], encoding="utf-8").read()
match = re.search(r'report_(drc|methodology)[^\\n]*-file "([^"]+)"', content)
assert match, content
with open(match.group(2), "w", encoding="utf-8") as f:
    f.write("Report {{}}\\nChecks found: 3\\n".format(match.group(1).upper()))
"""


@pytest.fixture
def fake_vivado(tmp_path: Path) -> Path:
    script = tmp_path / "fake_vivado.py"
    script.write_text(_FAKE_VIVADO, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _cfg(tmp_path: Path, **overrides) -> ProjectConfig:
    defaults: dict = {
        "project_dir": tmp_path,
        "build_script": tmp_path / "build_fpga.py",
        "python": sys.executable,
        "projects_path": tmp_path / "projects",
        "timeout": 5.0,
        "vivado": None,
    }
    defaults.update(overrides)
    return ProjectConfig(**defaults)


def _make_build(tmp_path: Path, project: str) -> Path:
    projects_path = tmp_path / "projects"
    pdir = project_dir(_cfg(tmp_path, projects_path=projects_path), project)
    pdir.mkdir(parents=True)
    (pdir / f"{project}.xpr").write_text("", encoding="utf-8")
    (pdir / f"{project}.runs" / "impl_1").mkdir(parents=True)
    return projects_path


def test_checks_found_parses_summary():
    assert checks_found("...\nChecks found: 5\n...") == 5
    assert checks_found("no such line") is None


async def test_invalid_report_type(tmp_path: Path):
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path)
    with pytest.raises(DrcReportError, match="Invalid report_type"):
        await get_drc_report(
            cfg,
            project="counter",
            run_index=1,
            synth_only=False,
            force_regenerate=False,
            timeout=None,
            report_type="bogus",
        )


async def test_project_not_built(tmp_path: Path):
    cfg = _cfg(tmp_path, projects_path=tmp_path / "projects")
    with pytest.raises(DrcReportError, match=r"Build .* first"):
        await get_drc_report(
            cfg,
            project="counter",
            run_index=1,
            synth_only=False,
            force_regenerate=False,
            timeout=None,
        )


async def test_regenerates_drc_via_fake_vivado(tmp_path: Path, fake_vivado):
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=str(fake_vivado))

    result = await get_drc_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=False,
        force_regenerate=False,
        timeout=None,
        report_type="drc",
    )

    assert result.regenerated
    assert "Report DRC" in result.report
    assert result.report_file.name == "drc.rpt"
    assert checks_found(result.report) == 3


async def test_regenerates_methodology_via_fake_vivado(tmp_path: Path, fake_vivado):
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=str(fake_vivado))

    result = await get_drc_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=False,
        force_regenerate=False,
        timeout=None,
        report_type="methodology",
    )

    assert result.regenerated
    assert "Report METHODOLOGY" in result.report
    assert result.report_file.name == "methodology.rpt"


async def test_cached_report_used_without_vivado(tmp_path: Path):
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=None)
    rdir = run_dir(cfg, "counter", 1, synth_only=False)
    (rdir / "drc.rpt").write_text("cached", encoding="utf-8")

    result = await get_drc_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=False,
        force_regenerate=False,
        timeout=None,
        report_type="drc",
    )

    assert not result.regenerated
    assert result.report == "cached"
