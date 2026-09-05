"""End-to-end tests for project-mode tools: real yosys + ghdl + ghdl
plugin + GHDL std/ieee libs, driving a throwaway fixture project's own
build_fpga.py exactly like tsfpga_synthesize's e2e tests drive real
sources — skipped (not failed) without the full synthesis setup, see the
``e2e`` fixture in conftest.

The fixture project (tests/fixture_project) has one netlist build
project ('counter', tests/fixture_project/modules/counter) and mirrors
a real project's own build_fpga.py — the server never imports it, it
only runs it as `<python> build_fpga.py <args>`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import tsfpga_mcp.server as server

FIXTURE_PROJECT = Path(__file__).parent / "fixture_project"


async def _reset_project_config(monkeypatch):
    monkeypatch.setattr(server, "_project_config", None)


async def _inject_project_env(monkeypatch, config_env):
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_DIR", str(FIXTURE_PROJECT))
    # The fixture has no venv of its own; use this server's own interpreter,
    # which already has tsfpga installed (a dependency of tsfpga-mcp itself).
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_PYTHON", sys.executable)
    for key in (
        "TSFPGA_MCP_BUILD_SCRIPT",
        "TSFPGA_MCP_PROJECTS_PATH",
        "TSFPGA_MCP_PROJECT_TIMEOUT",
        "TSFPGA_MCP_PROJECT_EXTRA_ARGS",
    ):
        monkeypatch.delenv(key, raising=False)
    # module_counter.py reads these directly to locate the ghdl-yosys-plugin,
    # the same way tsfpga_mcp.config does for the ad-hoc tsfpga_synthesize tool.
    for key in ("TSFPGA_MCP_GHDL_PLUGIN", "TSFPGA_MCP_GHDL_PREFIX"):
        if key in config_env:
            monkeypatch.setenv(key, config_env[key])


async def test_project_status(e2e, monkeypatch, config_env):
    await _reset_project_config(monkeypatch)
    await _inject_project_env(monkeypatch, config_env)

    result = await server.tsfpga_project_status()

    assert str(FIXTURE_PROJECT) in result
    assert "build_fpga.py" in result


async def test_list_builds(e2e, monkeypatch, config_env):
    await _reset_project_config(monkeypatch)
    await _inject_project_env(monkeypatch, config_env)

    result = await server.tsfpga_project_list_builds(
        server.ListBuildsInput(netlist_builds=True)
    )

    assert "counter" in result
    assert "Listed 1 builds" in result


async def test_list_builds_filter_no_match(e2e, monkeypatch, config_env):
    await _reset_project_config(monkeypatch)
    await _inject_project_env(monkeypatch, config_env)

    result = await server.tsfpga_project_list_builds(
        server.ListBuildsInput(netlist_builds=True, project_filters=["nope_*"])
    )

    assert "Listed 0 builds" in result


async def test_build_succeeds_and_reports_resources(
    e2e, monkeypatch, config_env, tmp_path
):
    await _reset_project_config(monkeypatch)
    await _inject_project_env(monkeypatch, config_env)
    monkeypatch.setenv("TSFPGA_MCP_PROJECTS_PATH", str(tmp_path / "projects"))

    result = await server.tsfpga_project_build(
        server.BuildInput(project_filters=["counter"])
    )

    assert result.startswith("Build succeeded"), result
    assert "$_DFF_P_" in result


async def test_build_no_matching_projects_still_succeeds(
    e2e, monkeypatch, config_env, tmp_path
):
    await _reset_project_config(monkeypatch)
    await _inject_project_env(monkeypatch, config_env)
    monkeypatch.setenv("TSFPGA_MCP_PROJECTS_PATH", str(tmp_path / "projects"))

    result = await server.tsfpga_project_build(
        server.BuildInput(project_filters=["nope_*"])
    )

    assert result.startswith("Build succeeded"), result
