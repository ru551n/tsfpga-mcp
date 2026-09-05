"""Tests for env-var configuration (tsfpga_mcp.project_config)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tsfpga_mcp.project_config import ProjectConfigError, load_project_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No TSFPGA_MCP_* env may leak in from the developer's environment."""
    for key in list(os.environ):
        if key.startswith("TSFPGA_MCP_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    (d / "build.py").write_text("", encoding="utf-8")
    return d


def test_project_dir_defaults_to_cwd(monkeypatch, project):
    monkeypatch.chdir(project)
    cfg = load_project_config()
    assert cfg.project_dir == project.resolve()
    assert cfg.build_script == project.resolve() / "build.py"


def test_project_dir_not_a_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_DIR", str(tmp_path / "nope"))
    with pytest.raises(ProjectConfigError, match="not a directory"):
        load_project_config()


def test_build_script_missing(monkeypatch, project):
    (project / "build.py").unlink()
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_DIR", str(project))
    with pytest.raises(ProjectConfigError, match="Build script not found"):
        load_project_config()


def test_defaults(monkeypatch, project):
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_DIR", str(project))
    cfg = load_project_config()
    assert cfg.project_dir == project
    assert cfg.build_script == project / "build.py"
    assert cfg.python
    assert cfg.projects_path == project / "tsfpga_mcp_out" / "projects"
    assert cfg.timeout == 600.0
    assert cfg.extra_args == []


def test_env_overrides(monkeypatch, project):
    (project / "custom").mkdir()
    (project / "custom" / "build.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_DIR", str(project))
    monkeypatch.setenv("TSFPGA_MCP_BUILD_SCRIPT", "custom/build.py")
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_PYTHON", "/usr/bin/python3")
    monkeypatch.setenv("TSFPGA_MCP_PROJECTS_PATH", "custom_out/projects")
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_TIMEOUT", "42")
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_EXTRA_ARGS", "--no-color -p 4")
    cfg = load_project_config()
    assert cfg.build_script == project / "custom" / "build.py"
    assert cfg.python == "/usr/bin/python3"
    assert cfg.projects_path == project / "custom_out" / "projects"
    assert cfg.timeout == 42.0
    assert cfg.extra_args == ["--no-color", "-p", "4"]


def test_build_script_absolute_path(monkeypatch, project, tmp_path):
    other = tmp_path / "elsewhere.py"
    other.write_text("", encoding="utf-8")
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_DIR", str(project))
    monkeypatch.setenv("TSFPGA_MCP_BUILD_SCRIPT", str(other))
    cfg = load_project_config()
    assert cfg.build_script == other.resolve()


def test_invalid_timeout(monkeypatch, project):
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_DIR", str(project))
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_TIMEOUT", "not-a-number")
    with pytest.raises(ProjectConfigError, match="must be a number of seconds"):
        load_project_config()


def test_non_positive_timeout(monkeypatch, project):
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_DIR", str(project))
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_TIMEOUT", "0")
    with pytest.raises(ProjectConfigError, match="must be positive"):
        load_project_config()


def test_python_prefers_project_venv(monkeypatch, project):
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    py = venv_bin / "python3"
    py.write_text("", encoding="utf-8")
    monkeypatch.setenv("TSFPGA_MCP_PROJECT_DIR", str(project))
    cfg = load_project_config()
    assert cfg.python == str(py)
