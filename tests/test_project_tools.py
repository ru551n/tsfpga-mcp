"""Unit tests for the project-mode tool inputs -> build script argv.

No yosys/ghdl needed: ``run_build_script`` is replaced by a stub that
records the argument list the server would have passed to the project's
own build script.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tsfpga_mcp.server as server
from tsfpga_mcp.project_config import ProjectConfig
from tsfpga_mcp.project_runner import RunResult


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """Stub out the subprocess; return the list of recorded arg lists."""
    script = tmp_path / "build_fpga.py"
    script.write_text("")
    config = ProjectConfig(
        project_dir=tmp_path,
        build_script=script,
        python="python3",
        projects_path=tmp_path / "projects",
        timeout=60.0,
    )
    monkeypatch.setattr(server, "_project_config", config)
    recorded: list[list[str]] = []

    async def fake_run(cfg, args, *, timeout=None):
        recorded.append(list(args))
        return RunResult(returncode=0, stdout="output", stderr="", argv=list(args))

    monkeypatch.setattr(server, "run_build_script", fake_run)
    return recorded


async def test_thread_counts_omitted_when_unset(calls):
    await server.tsfpga_project_build(server.BuildInput(project_filters=["counter"]))

    args = calls[0]
    assert "--num-parallel-builds" not in args
    assert "--num-threads-per-build" not in args


async def test_num_parallel_builds_passed_through(calls):
    await server.tsfpga_project_build(
        server.BuildInput(project_filters=["*"], num_parallel_builds=4)
    )

    args = calls[0]
    assert args[args.index("--num-parallel-builds") + 1] == "4"
    # Filters stay last, after every option.
    assert args[-1] == "*"


async def test_num_threads_per_build_passed_through(calls):
    result = await server.tsfpga_project_build(
        server.BuildInput(netlist_builds=False, num_threads_per_build=16)
    )

    args = calls[0]
    assert args[args.index("--num-threads-per-build") + 1] == "16"
    assert "--netlist-builds" not in args
    # Top-level (Vivado) builds do use it: no warning here.
    assert "is ignored by netlist" not in result


async def test_threads_per_build_warns_for_netlist_builds(calls):
    result = await server.tsfpga_project_build(
        server.BuildInput(num_threads_per_build=16)
    )

    assert "'num_threads_per_build' is ignored by netlist" in result
    assert "num_parallel_builds" in result
    # Still forwarded verbatim, the note only explains what it does.
    assert "--num-threads-per-build" in calls[0]


async def test_no_warning_without_threads_per_build(calls):
    result = await server.tsfpga_project_build(server.BuildInput(num_parallel_builds=8))

    assert "is ignored by netlist" not in result


@pytest.mark.parametrize("field", ["num_parallel_builds", "num_threads_per_build"])
def test_thread_counts_must_be_positive(field):
    with pytest.raises(ValueError):
        server.BuildInput(**{field: 0})


@pytest.mark.parametrize("field", ["num_parallel_builds", "num_threads_per_build"])
def test_thread_counts_are_documented(field):
    description = server.BuildInput.model_fields[field].description
    assert description
    assert "netlist" in description.lower()


def test_project_config_unused_path_is_a_path(tmp_path: Path):
    # Guards the fixture above against ProjectConfig field drift.
    assert isinstance(
        ProjectConfig(
            project_dir=tmp_path,
            build_script=tmp_path / "build.py",
            python="python3",
            projects_path=tmp_path,
            timeout=1.0,
        ).projects_path,
        Path,
    )
