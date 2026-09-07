"""Tests for tsfpga_mcp.utilization_report (Vivado hierarchical utilization).

No real Vivado available: a small executable Python stub stands in for
it, same pattern as test_timing.py.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from tsfpga_mcp.project_config import ProjectConfig
from tsfpga_mcp.timing import project_dir, run_dir
from tsfpga_mcp.utilization_report import UtilizationReportError, get_utilization_report

_FAKE_VIVADO = f"""\
#!{sys.executable}
import re
import sys

content = open(sys.argv[-1], encoding="utf-8").read()
match = re.search(r'report_utilization[^\\n]*-file "([^"]+)"', content)
assert match, content
with open(match.group(1), "w", encoding="utf-8") as f:
    f.write("Utilization Design Information\\n1. Utilization by Hierarchy\\n")
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


async def test_project_not_built(tmp_path: Path):
    cfg = _cfg(tmp_path, projects_path=tmp_path / "projects")
    with pytest.raises(UtilizationReportError, match=r"Build .* first"):
        await get_utilization_report(
            cfg,
            project="counter",
            run_index=1,
            synth_only=False,
            force_regenerate=False,
            timeout=None,
        )


async def test_no_cache_no_vivado_configured(tmp_path: Path):
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=None)
    with pytest.raises(UtilizationReportError, match="Vivado is not available"):
        await get_utilization_report(
            cfg,
            project="counter",
            run_index=1,
            synth_only=False,
            force_regenerate=False,
            timeout=None,
        )


async def test_regenerates_via_fake_vivado(tmp_path: Path, fake_vivado):
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=str(fake_vivado))

    result = await get_utilization_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=False,
        force_regenerate=False,
        timeout=None,
    )

    assert result.regenerated
    assert "Utilization by Hierarchy" in result.report
    assert result.report_file.name == "hierarchical_utilization.rpt"


async def test_cached_report_used_without_vivado(tmp_path: Path):
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=None)
    rdir = run_dir(cfg, "counter", 1, synth_only=False)
    (rdir / "hierarchical_utilization.rpt").write_text("cached", encoding="utf-8")

    result = await get_utilization_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=False,
        force_regenerate=False,
        timeout=None,
    )

    assert not result.regenerated
    assert result.report == "cached"


async def test_different_depth_is_not_served_stale_cache(tmp_path: Path, fake_vivado):
    """A cached report at one depth must not be served for a different one."""
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=str(fake_vivado))
    rdir = run_dir(cfg, "counter", 1, synth_only=False)
    (rdir / "hierarchical_utilization.rpt").write_text(
        "stale depth-4 cache", encoding="utf-8"
    )

    result = await get_utilization_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=False,
        force_regenerate=False,
        timeout=None,
        hierarchical_depth=7,
    )

    assert result.regenerated
    assert result.report_file.name == "hierarchical_utilization_depth7.rpt"
    assert "stale depth-4 cache" not in result.report


async def test_hierarchical_depth_passed_through(tmp_path: Path, fake_vivado):
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=str(fake_vivado))
    rdir = run_dir(cfg, "counter", 1, synth_only=False)

    await get_utilization_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=False,
        force_regenerate=False,
        timeout=None,
        hierarchical_depth=7,
    )

    tcl_content = (rdir / "tsfpga_mcp_report_utilization.tcl").read_text(
        encoding="utf-8"
    )
    assert "-hierarchical_depth 7" in tcl_content
