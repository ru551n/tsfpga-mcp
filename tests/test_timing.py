"""Tests for tsfpga_mcp.timing (Vivado timing report retrieval).

No real Vivado available: a small executable Python stub stands in for
it, controlled via the FAKE_VIVADO_MODE env var, mirroring how
test_project_runner.py stubs the build script itself.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from tsfpga_mcp.project_config import ProjectConfig
from tsfpga_mcp.project_runner import RunTimeoutError
from tsfpga_mcp.timing import (
    TimingReportError,
    build_tcl,
    get_timing_report,
    project_dir,
    run_dir,
    run_name,
    xpr_file,
)

_FAKE_VIVADO = """\
#!/usr/bin/env python3
import os
import re
import sys
import time

mode = os.environ.get("FAKE_VIVADO_MODE", "ok")
tcl_file = sys.argv[-1]
content = open(tcl_file, encoding="utf-8").read()
match = re.search(r'report_timing_summary[^\\n]*-file "([^"]+)"', content)

if mode == "sleep":
    time.sleep(5)
elif mode == "error":
    print("ERROR: fake synthesis failure", file=sys.stderr)
    sys.exit(1)
elif mode == "no_report":
    print("ran but wrote nothing")
    sys.exit(0)
else:
    assert match, content
    with open(match.group(1), "w", encoding="utf-8") as f:
        f.write("Timing Summary Report\\nWNS(ns): 1.234\\n")
    print("Vivado stub ran ok")
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


def _make_build(tmp_path: Path, project: str, *, with_impl_run: bool = True) -> Path:
    """Fake up a completed build's directory layout for 'project'."""
    projects_path = tmp_path / "projects"
    pdir = project_dir(_cfg(tmp_path, projects_path=projects_path), project)
    pdir.mkdir(parents=True)
    (pdir / f"{project}.xpr").write_text("", encoding="utf-8")
    if with_impl_run:
        (pdir / f"{project}.runs" / "impl_1").mkdir(parents=True)
    return projects_path


def test_run_name():
    assert run_name(1, synth_only=False) == "impl_1"
    assert run_name(2, synth_only=True) == "synth_2"


def test_path_helpers(tmp_path: Path):
    cfg = _cfg(tmp_path, projects_path=tmp_path / "projects")
    assert project_dir(cfg, "counter") == tmp_path / "projects" / "counter" / "project"
    assert xpr_file(cfg, "counter") == project_dir(cfg, "counter") / "counter.xpr"
    assert (
        run_dir(cfg, "counter", 1, synth_only=False)
        == project_dir(cfg, "counter") / "counter.runs" / "impl_1"
    )


def test_build_tcl_contents(tmp_path: Path):
    tcl = build_tcl(tmp_path / "p.xpr", "impl_1", tmp_path / "timing_summary.rpt")
    assert 'open_project "' in tcl
    assert 'open_run "impl_1"' in tcl
    assert "report_timing_summary" in tcl
    assert "timing_summary.rpt" in tcl


async def test_project_not_built(tmp_path: Path):
    projects_path = tmp_path / "projects"
    cfg = _cfg(tmp_path, projects_path=projects_path)
    with pytest.raises(TimingReportError, match=r"Build .* first"):
        await get_timing_report(
            cfg,
            project="counter",
            run_index=1,
            synth_only=False,
            force_regenerate=False,
            timeout=None,
        )


async def test_run_not_found(tmp_path: Path):
    projects_path = _make_build(tmp_path, "counter", with_impl_run=False)
    cfg = _cfg(tmp_path, projects_path=projects_path)
    with pytest.raises(TimingReportError, match="Run directory not found"):
        await get_timing_report(
            cfg,
            project="counter",
            run_index=1,
            synth_only=False,
            force_regenerate=False,
            timeout=None,
        )


async def test_cached_report_used_without_vivado(tmp_path: Path):
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=None)
    rdir = run_dir(cfg, "counter", 1, synth_only=False)
    (rdir / "timing_summary.rpt").write_text("cached report", encoding="utf-8")

    result = await get_timing_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=False,
        force_regenerate=False,
        timeout=None,
    )

    assert result.report == "cached report"
    assert not result.regenerated


async def test_no_cache_no_vivado_configured(tmp_path: Path):
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=None)

    with pytest.raises(TimingReportError, match="Vivado is not available"):
        await get_timing_report(
            cfg,
            project="counter",
            run_index=1,
            synth_only=False,
            force_regenerate=False,
            timeout=None,
        )


async def test_regenerates_via_fake_vivado(tmp_path: Path, fake_vivado, monkeypatch):
    monkeypatch.setenv("FAKE_VIVADO_MODE", "ok")
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=str(fake_vivado))

    result = await get_timing_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=False,
        force_regenerate=False,
        timeout=None,
    )

    assert result.regenerated
    assert "WNS" in result.report
    assert result.report_file.is_file()


async def test_force_regenerate_overwrites_cache(
    tmp_path: Path, fake_vivado, monkeypatch
):
    monkeypatch.setenv("FAKE_VIVADO_MODE", "ok")
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=str(fake_vivado))
    rdir = run_dir(cfg, "counter", 1, synth_only=False)
    (rdir / "timing_summary.rpt").write_text("stale cached report", encoding="utf-8")

    result = await get_timing_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=False,
        force_regenerate=True,
        timeout=None,
    )

    assert result.regenerated
    assert "WNS" in result.report
    assert "stale" not in result.report


async def test_vivado_failure_no_report_raises(
    tmp_path: Path, fake_vivado, monkeypatch
):
    monkeypatch.setenv("FAKE_VIVADO_MODE", "error")
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=str(fake_vivado))

    with pytest.raises(TimingReportError, match="did not produce"):
        await get_timing_report(
            cfg,
            project="counter",
            run_index=1,
            synth_only=False,
            force_regenerate=False,
            timeout=None,
        )


async def test_vivado_timeout(tmp_path: Path, fake_vivado, monkeypatch):
    monkeypatch.setenv("FAKE_VIVADO_MODE", "sleep")
    projects_path = _make_build(tmp_path, "counter")
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=str(fake_vivado))

    with pytest.raises(RunTimeoutError):
        await get_timing_report(
            cfg,
            project="counter",
            run_index=1,
            synth_only=False,
            force_regenerate=False,
            timeout=0.2,
        )


async def test_synth_only_uses_synth_run_dir(tmp_path: Path, fake_vivado, monkeypatch):
    monkeypatch.setenv("FAKE_VIVADO_MODE", "ok")
    projects_path = tmp_path / "projects"
    cfg = _cfg(tmp_path, projects_path=projects_path, vivado=str(fake_vivado))
    pdir = project_dir(cfg, "counter")
    pdir.mkdir(parents=True)
    (pdir / "counter.xpr").write_text("", encoding="utf-8")
    (pdir / "counter.runs" / "synth_1").mkdir(parents=True)

    result = await get_timing_report(
        cfg,
        project="counter",
        run_index=1,
        synth_only=True,
        force_regenerate=False,
        timeout=None,
    )

    assert (
        result.report_file == pdir / "counter.runs" / "synth_1" / "timing_summary.rpt"
    )
