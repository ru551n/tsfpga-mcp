"""Async subprocess orchestration for invoking the project's own build script.

tsfpga has no standalone CLI either (like VUnit, its build flow is a Python
library), so — mirroring vunit-mcp's ``runner.py`` for VUnit's ``run.py`` —
every operation here is a subprocess:

    <python> <build_script> <args...>

— exactly how a human runs the project's ``build_fpga.py`` from a terminal.
The server never imports the project's own build modules.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
from dataclasses import dataclass
from pathlib import Path

from .project_config import ProjectConfig, _venv_bin_dir_name

# Matches ANSI escape sequences (CSI, OSC, and two-byte Fe/Fn) so captured
# output stays plain text even though tsfpga's build flow colorizes its
# printouts by default.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[\x30-\x7e]"
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences so captured output is plain text."""
    return _ANSI_RE.sub("", text)


class RunTimeoutError(RuntimeError):
    """Raised when a build script invocation exceeds its timeout."""


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill a subprocess and everything it spawned (e.g. ghdl/yosys)."""
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    argv: list[str]

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def full_text(self) -> str:
        """stdout + stderr, untruncated."""
        parts = [p for p in (self.stdout.strip(), self.stderr.strip()) if p]
        return "\n".join(parts) if parts else "(no output)"

    def summary(self, max_chars: int = 6000) -> str:
        """Agent-readable text: stdout, then stderr if any, size-bounded.

        Truncation keeps the TAIL, since the utilization report and the
        final pass/fail summary from ``setup_and_run``'s build flow are
        printed at the end.
        """
        parts = []
        if self.stdout:
            parts.append(self.stdout.strip())
        if self.stderr:
            parts.append(f"--- stderr ---\n{self.stderr.strip()}")
        text = "\n".join(parts) if parts else "(no output)"
        if len(text) > max_chars:
            text = (
                f"… [truncated: showing last {max_chars} of {len(text)} chars]\n"
                + text[-max_chars:]
            )
        return text


def build_argv(config: ProjectConfig, args: list[str]) -> list[str]:
    return [config.python, str(config.build_script), *args, *config.extra_args]


def run_env(config: ProjectConfig) -> dict[str, str]:
    """Environment for the build script subprocess.

    This server may itself be running from its own virtualenv; that venv
    describes *this* process, not the target project, so its
    ``VIRTUAL_ENV``/``PYTHONHOME`` and its ``bin`` dir on PATH are stripped
    here rather than handed to the subprocess (``config.python`` is
    resolved separately — this only prevents the target's own subprocesses,
    e.g. ghdl/yosys invocations made from the build script, from picking up
    the wrong environment).
    """
    env = dict(os.environ)
    own_venv = env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    if own_venv:
        own_bin = str(Path(own_venv) / _venv_bin_dir_name())
        env["PATH"] = os.pathsep.join(
            entry for entry in env.get("PATH", "").split(os.pathsep) if entry != own_bin
        )
    return env


async def run_build_script(
    config: ProjectConfig,
    args: list[str],
    *,
    timeout: float | None = None,
) -> RunResult:
    """Run <python> <build_script> <args> and capture output. Raises on timeout."""
    argv = build_argv(config, args)
    limit = timeout if timeout is not None else config.timeout
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(config.project_dir),
        env=run_env(config),
        # Own process group so a timeout can kill the ghdl/yosys children
        # that the build script spawns, not just the build script itself.
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=limit)
    except asyncio.TimeoutError:
        _kill_process_group(proc)
        await proc.wait()
        raise RunTimeoutError(
            f"Timed out after {limit:.0f}s: {' '.join(argv)}"
        ) from None
    return RunResult(
        returncode=proc.returncode or 0,
        stdout=strip_ansi(out.decode(errors="replace")),
        stderr=strip_ansi(err.decode(errors="replace")),
        argv=argv,
    )
