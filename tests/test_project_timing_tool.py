"""Tests for the tsfpga_project_get_timing_report MCP tool.

Exercises server.py's wiring (input validation, config lookup, error
translation) with the fake Vivado stub from test_timing.py's approach —
no real Vivado needed.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

import tsfpga_mcp.server as server
from tsfpga_mcp.project_config import ProjectConfig
from tsfpga_mcp.timing import project_dir, run_dir

_FAKE_VIVADO = """\
#!/usr/bin/env python3
import os
import re
import sys

tcl_file = sys.argv[-1]
content = open(tcl_file, encoding="utf-8").read()
match = re.search(r'report_timing_summary[^\\n]*-file "([^"]+)"', content)
with open(match.group(1), "w", encoding="utf-8") as f:
    f.write("Timing Summary Report\\nWNS(ns): 1.234\\n")
"""


@pytest.fixture
def fake_vivado(tmp_path: Path) -> Path:
    script = tmp_path / "fake_vivado.py"
    script.write_text(_FAKE_VIVADO, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _install_config(
    monkeypatch, tmp_path: Path, *, vivado: str | None
) -> ProjectConfig:
    config = ProjectConfig(
        project_dir=tmp_path,
        build_script=tmp_path / "build_fpga.py",
        python=sys.executable,
        projects_path=tmp_path / "projects",
        timeout=5.0,
        vivado=vivado,
    )
    monkeypatch.setattr(server, "_project_config", config)
    return config


def _make_build(config: ProjectConfig, project: str) -> None:
    pdir = project_dir(config, project)
    pdir.mkdir(parents=True)
    (pdir / f"{project}.xpr").write_text("", encoding="utf-8")
    (pdir / f"{project}.runs" / "impl_1").mkdir(parents=True)


async def test_timing_report_success(monkeypatch, tmp_path, fake_vivado):
    config = _install_config(monkeypatch, tmp_path, vivado=str(fake_vivado))
    _make_build(config, "counter")

    result = await server.tsfpga_project_get_timing_report(
        server.TimingReportInput(project="counter")
    )

    assert "Timing report for 'counter' (impl_1" in result
    assert "regenerated via Vivado" in result
    assert "WNS" in result


async def test_timing_report_uses_cache(monkeypatch, tmp_path, fake_vivado):
    config = _install_config(monkeypatch, tmp_path, vivado=str(fake_vivado))
    _make_build(config, "counter")
    rdir = run_dir(config, "counter", 1, synth_only=False)
    (rdir / "timing_summary.rpt").write_text("cached", encoding="utf-8")

    result = await server.tsfpga_project_get_timing_report(
        server.TimingReportInput(project="counter")
    )

    assert "cached from a previous run" in result
    assert "cached" in result


async def test_timing_report_project_not_built(monkeypatch, tmp_path):
    _install_config(monkeypatch, tmp_path, vivado=None)

    result = await server.tsfpga_project_get_timing_report(
        server.TimingReportInput(project="counter")
    )

    assert result.startswith("Error:")
    assert "Build" in result


async def test_timing_report_no_vivado_configured(monkeypatch, tmp_path):
    config = _install_config(monkeypatch, tmp_path, vivado=None)
    _make_build(config, "counter")

    result = await server.tsfpga_project_get_timing_report(
        server.TimingReportInput(project="counter")
    )

    assert result.startswith("Error:")
    assert "Vivado is not available" in result


async def test_timing_report_empty_project_name_rejected():
    with pytest.raises(ValueError):
        server.TimingReportInput(project="")


async def test_project_status_reports_vivado_path(monkeypatch, tmp_path, fake_vivado):
    _install_config(monkeypatch, tmp_path, vivado=str(fake_vivado))

    result = await server.tsfpga_project_status()

    assert str(fake_vivado) in result


async def test_project_status_reports_missing_vivado(monkeypatch, tmp_path):
    _install_config(monkeypatch, tmp_path, vivado=None)

    result = await server.tsfpga_project_status()

    assert "not found" in result
    assert "TSFPGA_MCP_VIVADO" in result
