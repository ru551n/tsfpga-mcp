"""Tests for tsfpga_mcp.project_runner."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tsfpga_mcp.project_config import ProjectConfig
from tsfpga_mcp.project_runner import (
    RunResult,
    RunTimeoutError,
    build_argv,
    run_build_script,
    run_env,
    strip_ansi,
)


def _cfg(**overrides) -> ProjectConfig:
    defaults = {
        "project_dir": Path("/p"),
        "build_script": Path("/p/build_fpga.py"),
        "python": sys.executable,
        "projects_path": Path("/p/tsfpga_mcp_out/projects"),
        "timeout": 600.0,
        "extra_args": [],
    }
    defaults.update(overrides)
    return ProjectConfig(**defaults)


def test_strip_ansi():
    assert strip_ansi("\x1b[31mred\x1b[0m plain") == "red plain"


def test_build_argv_includes_extra_args():
    cfg = _cfg(extra_args=["--no-color"])
    argv = build_argv(cfg, ["--list-only"])
    assert argv == [cfg.python, str(cfg.build_script), "--list-only", "--no-color"]


def test_run_env_strips_own_virtualenv(tmp_path, monkeypatch):
    own_venv = tmp_path / "server_venv"
    own_bin = own_venv / "bin"
    own_bin.mkdir(parents=True)
    monkeypatch.setenv("VIRTUAL_ENV", str(own_venv))
    monkeypatch.setenv("PYTHONHOME", "/should/be/removed")
    monkeypatch.setenv("PATH", f"{own_bin}{os.pathsep}/usr/bin")

    env = run_env(_cfg())

    assert "VIRTUAL_ENV" not in env
    assert "PYTHONHOME" not in env
    assert str(own_bin) not in env["PATH"].split(os.pathsep)
    assert "/usr/bin" in env["PATH"].split(os.pathsep)


def test_run_env_no_virtualenv(monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    env = run_env(_cfg())
    assert "VIRTUAL_ENV" not in env


def test_result_ok():
    assert RunResult(returncode=0, stdout="", stderr="", argv=[]).ok
    assert not RunResult(returncode=1, stdout="", stderr="", argv=[]).ok


def test_full_text_no_output():
    assert (
        RunResult(returncode=0, stdout="", stderr="", argv=[]).full_text
        == "(no output)"
    )


def test_summary_short_untouched():
    r = RunResult(returncode=0, stdout="ok", stderr="", argv=[])
    assert r.summary() == "ok"


def test_summary_tail_truncation_keeps_the_end():
    r = RunResult(
        returncode=1,
        stdout="\n".join(f"line {i}" for i in range(1000)),
        stderr="final error line",
        argv=[],
    )
    text = r.summary(max_chars=500)
    assert text.endswith("final error line")
    assert "line 999" in text
    assert "line 0" not in text
    assert "truncated" in text


def test_summary_stderr_included():
    r = RunResult(returncode=1, stdout="out", stderr="err", argv=[])
    text = r.summary()
    assert "out" in text and "err" in text


@pytest.mark.asyncio
async def test_run_build_script_success(tmp_path):
    script = tmp_path / "build_fpga.py"
    script.write_text("import sys\nprint('hello', *sys.argv[1:])\n", encoding="utf-8")
    cfg = _cfg(project_dir=tmp_path, build_script=script, python=sys.executable)

    result = await run_build_script(cfg, ["--list-only"])

    assert result.ok
    assert "hello --list-only" in result.stdout


@pytest.mark.asyncio
async def test_run_build_script_failure_returncode(tmp_path):
    script = tmp_path / "build_fpga.py"
    script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    cfg = _cfg(project_dir=tmp_path, build_script=script, python=sys.executable)

    result = await run_build_script(cfg, [])

    assert not result.ok
    assert result.returncode == 1


@pytest.mark.asyncio
async def test_run_build_script_timeout(tmp_path):
    script = tmp_path / "build_fpga.py"
    script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    cfg = _cfg(project_dir=tmp_path, build_script=script, python=sys.executable)

    with pytest.raises(RunTimeoutError):
        await run_build_script(cfg, [], timeout=0.2)
