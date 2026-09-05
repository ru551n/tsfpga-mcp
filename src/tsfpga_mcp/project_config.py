"""Environment-bound configuration for driving a real project's build script.

A single tsfpga project is addressed via environment variables, read once at
startup. The server shells out to the project's own build script (typically
a copy of ``tsfpga.examples.build_fpga.py``, using
``tsfpga.examples.build_fpga_utils.arguments()``/``setup_and_run()``), the
same way a human would run it from a terminal — nothing about the project's
module layout needs to be known in-process. This mirrors how vunit-mcp
drives a project's own ``run.py`` rather than importing VUnit directly.

===============================  ===========================================
``TSFPGA_MCP_PROJECT_DIR``       directory containing the project's build
                                  script (default: the server's current
                                  working directory).
``TSFPGA_MCP_BUILD_SCRIPT``      path to the build script, relative to
                                  ``TSFPGA_MCP_PROJECT_DIR`` unless absolute
                                  (default: ``build.py``).
``TSFPGA_MCP_PROJECT_PYTHON``    interpreter used to run the build script
                                  (default: resolved the same way vunit-mcp
                                  resolves its target project's interpreter
                                  — the project's own ``.venv``/``venv``
                                  first, else PATH with this server's own
                                  venv excluded, else ``sys.executable``).
``TSFPGA_MCP_PROJECTS_PATH``     ``--projects-path`` passed to the build
                                  script (default:
                                  ``<project dir>/tsfpga_mcp_out/projects``).
``TSFPGA_MCP_PROJECT_TIMEOUT``   max seconds for one build script invocation
                                  (default: 600).
``TSFPGA_MCP_PROJECT_EXTRA_ARGS``extra arguments appended, verbatim
                                  (shell-split), to every build script
                                  invocation.
===============================  ===========================================
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


class ProjectConfigError(RuntimeError):
    """Raised when the project-mode server cannot be configured/validated."""


@dataclass(frozen=True)
class ProjectConfig:
    project_dir: Path
    build_script: Path
    python: str
    projects_path: Path
    timeout: float
    extra_args: list[str] = field(default_factory=list)


def _venv_bin_dir_name() -> str:
    """The platform-specific scripts subdir name inside a virtualenv."""
    return "Scripts" if os.name == "nt" else "bin"


def _own_venv_bin() -> str | None:
    """This server's own virtualenv 'bin'/'Scripts' dir, if running from one."""
    venv = os.environ.get("VIRTUAL_ENV")
    return str(Path(venv) / _venv_bin_dir_name()) if venv else None


def _resolve_python(project_dir: Path, env: Mapping[str, str]) -> str:
    """Pick the interpreter that shall run the *target* project's build script.

    Same rationale and preference order as vunit-mcp's ``_resolve_python``:
    this server's own virtualenv has no reason to contain the target
    project's dependencies (tsfpga, hdl-registers, ...), so using
    ``sys.executable`` unconditionally would reproduce a
    ``ModuleNotFoundError`` in the subprocess.

    1. A virtualenv inside the project itself (``.venv``/``venv``).
    2. Whatever ``python3``/``python`` a plain shell *in the project* would
       find on PATH, explicitly excluding this server's own virtualenv's
       ``bin`` dir.
    3. ``sys.executable`` as a last resort.
    """
    bin_dir_name = _venv_bin_dir_name()
    exe_names = (
        ("python.exe", "python3.exe") if os.name == "nt" else ("python3", "python")
    )
    for venv_name in (".venv", "venv"):
        for exe_name in exe_names:
            candidate = project_dir / venv_name / bin_dir_name / exe_name
            if candidate.is_file():
                return str(candidate)

    own_bin = _own_venv_bin()
    path_entries = [
        entry
        for entry in env.get("PATH", "").split(os.pathsep)
        if entry and entry != own_bin
    ]
    sanitized_path = os.pathsep.join(path_entries)
    for exe_name in ("python3", "python"):
        found = shutil.which(exe_name, path=sanitized_path)
        if found:
            return found

    return sys.executable


def load_project_config(env: Mapping[str, str] | None = None) -> ProjectConfig:
    """Build a :class:`ProjectConfig` from ``env`` (default: ``os.environ``).

    ``TSFPGA_MCP_PROJECT_DIR`` defaults to the current working directory,
    and ``TSFPGA_MCP_BUILD_SCRIPT`` to ``build.py`` in it — set either
    explicitly when the server isn't launched from the project directory,
    or the build script has a different name/location.

    Raises:
        ProjectConfigError: if ``TSFPGA_MCP_PROJECT_DIR`` is not a
            directory, the build script does not exist, or the timeout is
            invalid.
    """
    source: Mapping[str, str] = os.environ if env is None else env

    project_dir_env = source.get("TSFPGA_MCP_PROJECT_DIR", "").strip()
    project_dir = (
        Path(project_dir_env).expanduser().resolve() if project_dir_env else Path.cwd()
    )
    if not project_dir.is_dir():
        raise ProjectConfigError(
            f"TSFPGA_MCP_PROJECT_DIR is not a directory: {project_dir}"
        )

    build_script = Path(source.get("TSFPGA_MCP_BUILD_SCRIPT", "build.py").strip())
    if not build_script.is_absolute():
        build_script = project_dir / build_script
    build_script = build_script.resolve()
    if not build_script.is_file():
        raise ProjectConfigError(
            f"Build script not found: {build_script}. Set TSFPGA_MCP_PROJECT_DIR "
            "to the project directory and/or TSFPGA_MCP_BUILD_SCRIPT to the "
            "build script's name/path if it isn't build.py in the current "
            "working directory."
        )

    python = source.get("TSFPGA_MCP_PROJECT_PYTHON", "").strip() or _resolve_python(
        project_dir, source
    )

    projects_path_env = source.get("TSFPGA_MCP_PROJECTS_PATH", "").strip()
    if projects_path_env:
        projects_path = Path(projects_path_env).expanduser()
        if not projects_path.is_absolute():
            projects_path = project_dir / projects_path
        projects_path = projects_path.resolve()
    else:
        projects_path = (project_dir / "tsfpga_mcp_out" / "projects").resolve()

    timeout_env = source.get("TSFPGA_MCP_PROJECT_TIMEOUT", "").strip()
    if not timeout_env:
        timeout = 600.0
    else:
        try:
            timeout = float(timeout_env)
        except ValueError as exc:
            raise ProjectConfigError(
                "TSFPGA_MCP_PROJECT_TIMEOUT must be a number of seconds, "
                f"got {timeout_env!r}"
            ) from exc
        if timeout <= 0:
            raise ProjectConfigError(
                f"TSFPGA_MCP_PROJECT_TIMEOUT must be positive, got {timeout}"
            )

    extra_args_env = source.get("TSFPGA_MCP_PROJECT_EXTRA_ARGS", "")
    extra_args = shlex.split(extra_args_env) if extra_args_env else []

    return ProjectConfig(
        project_dir=project_dir,
        build_script=build_script,
        python=python,
        projects_path=projects_path,
        timeout=timeout,
        extra_args=extra_args,
    )
