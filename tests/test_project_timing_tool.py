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

_FAKE_VIVADO = f"""\
#!{sys.executable}
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

    assert "Timing report (summary) for 'counter' (impl_1" in result
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


_FAKE_VIVADO_REAL_SUMMARY = f"""\
#!{sys.executable}
import re
import sys

content = open(sys.argv[-1], encoding="utf-8").read()
match = re.search(r'report_timing_summary[^\\n]*-file "([^"]+)"', content)
with open(match.group(1), "w", encoding="utf-8") as f:
    f.write('''\\
---------------------------------------------------------
| Design Timing Summary
| ----------------------
---------------------------------------------------------

  WNS(ns)  TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints  WHS(ns)  \\
THS(ns)  THS Failing Endpoints  THS Total Endpoints  WPWS(ns)  TPWS(ns)  \\
TPWS Failing Endpoints  TPWS Total Endpoints
  -------  -------  ----------------------  --------------------  \\
-------  -------  ----------------------  --------------------  --------  \\
--------  ----------------------  --------------------
   -1.067   -9.836  12  144  0.045  0.000  0  120  2.000  0.000  0  116


Timing constraints are not met.
''')
"""


@pytest.fixture
def fake_vivado_real_summary(tmp_path: Path) -> Path:
    script = tmp_path / "fake_vivado_real.py"
    script.write_text(_FAKE_VIVADO_REAL_SUMMARY, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


async def test_timing_report_structured_header_in_full_verbosity(
    monkeypatch, tmp_path, fake_vivado_real_summary
):
    config = _install_config(
        monkeypatch, tmp_path, vivado=str(fake_vivado_real_summary)
    )
    _make_build(config, "counter")

    result = await server.tsfpga_project_get_timing_report(
        server.TimingReportInput(project="counter")
    )

    assert "Timing constraints: NOT MET" in result
    assert "WNS(ns): -1.067" in result
    assert "Full report:" in result


async def test_timing_report_summary_verbosity_omits_full_report(
    monkeypatch, tmp_path, fake_vivado_real_summary
):
    config = _install_config(
        monkeypatch, tmp_path, vivado=str(fake_vivado_real_summary)
    )
    _make_build(config, "counter")

    result = await server.tsfpga_project_get_timing_report(
        server.TimingReportInput(project="counter", verbosity="summary")
    )

    assert "Timing constraints: NOT MET" in result
    assert "Full report:" not in result


async def test_timing_report_pulse_width_type(monkeypatch, tmp_path):
    script = f"""\
#!{sys.executable}
import re
import sys

content = open(sys.argv[-1], encoding="utf-8").read()
match = re.search(r'report_pulse_width[^\\n]*-file "([^"]+)"', content)
with open(match.group(1), "w", encoding="utf-8") as f:
    f.write("Pulse Width Report\\n")
"""
    fake = tmp_path / "fake_vivado_pw.py"
    fake.write_text(script, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    config = _install_config(monkeypatch, tmp_path, vivado=str(fake))
    _make_build(config, "counter")

    result = await server.tsfpga_project_get_timing_report(
        server.TimingReportInput(project="counter", report_type="pulse_width")
    )

    assert "Timing report (pulse_width)" in result
    assert "Pulse Width Report" in result
    # Non-summary report types are returned raw, no structured header.
    assert "Timing constraints" not in result


async def test_utilization_report_success(monkeypatch, tmp_path):
    script = f"""\
#!{sys.executable}
import re
import sys

content = open(sys.argv[-1], encoding="utf-8").read()
match = re.search(r'report_utilization[^\\n]*-file "([^"]+)"', content)
with open(match.group(1), "w", encoding="utf-8") as f:
    f.write("Utilization by Hierarchy\\n")
"""
    fake = tmp_path / "fake_vivado_util.py"
    fake.write_text(script, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    config = _install_config(monkeypatch, tmp_path, vivado=str(fake))
    _make_build(config, "counter")

    result = await server.tsfpga_project_get_utilization_report(
        server.UtilizationReportInput(project="counter")
    )

    assert "Hierarchical utilization report" in result
    assert "Utilization by Hierarchy" in result


async def test_drc_report_success(monkeypatch, tmp_path):
    script = f"""\
#!{sys.executable}
import re
import sys

content = open(sys.argv[-1], encoding="utf-8").read()
match = re.search(r'report_drc[^\\n]*-file "([^"]+)"', content)
with open(match.group(1), "w", encoding="utf-8") as f:
    f.write("Report DRC\\nChecks found: 2\\n")
"""
    fake = tmp_path / "fake_vivado_drc.py"
    fake.write_text(script, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    config = _install_config(monkeypatch, tmp_path, vivado=str(fake))
    _make_build(config, "counter")

    result = await server.tsfpga_project_get_drc_report(
        server.DrcReportInput(project="counter")
    )

    assert "DRC report" in result
    assert "Checks found: 2" in result


async def test_drc_report_project_not_built(monkeypatch, tmp_path):
    _install_config(monkeypatch, tmp_path, vivado=None)

    result = await server.tsfpga_project_get_drc_report(
        server.DrcReportInput(project="counter")
    )

    assert result.startswith("Error:")
    assert "Build" in result


async def test_utilization_report_project_not_built(monkeypatch, tmp_path):
    _install_config(monkeypatch, tmp_path, vivado=None)

    result = await server.tsfpga_project_get_utilization_report(
        server.UtilizationReportInput(project="counter")
    )

    assert result.startswith("Error:")
    assert "Build" in result
