"""Server configuration from environment variables.

The server drives ``tsfpga.yosys.project.YosysNetlistBuild`` (and its
Xilinx/Intel/Microchip subclasses), which in turn shells out to the
``ghdl`` CLI (to analyze VHDL sources) and to ``yosys`` with the
ghdl-yosys-plugin loaded (to elaborate + synthesize). Where to find all of
that — plus the GHDL library prefix and a per-run timeout — comes from the
environment:

===========================  =============================================
``TSFPGA_MCP_YOSYS``         yosys binary (default: ``yosys`` on PATH)
``TSFPGA_MCP_GHDL``          ghdl binary (default: ``ghdl`` on PATH)
``TSFPGA_MCP_GHDL_PLUGIN``   path to ``ghdl.so`` (the ghdl-yosys-plugin);
                             falls back to ``ghdl.so`` in each
                             ``YOSYS_PLUGIN_PATH`` entry, then to the
                             plugin dir reported by ``yosys-config``
                             (``<datdir>/plugins/ghdl.so``)
``TSFPGA_MCP_GHDL_PREFIX``   passed to tsfpga as the GHDL library prefix
                             (where ghdl finds std/ieee libraries); falls
                             back to the caller's ``GHDL_PREFIX``, then to
                             the library prefix of the ``ghdl`` CLI on
                             PATH (``ghdl --dispconfig``)
``TSFPGA_MCP_TIMEOUT``       max seconds for one synthesis (default: 300)
===========================  =============================================
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 300.0
DEFAULT_YOSYS = "yosys"
DEFAULT_GHDL = "ghdl"
PLUGIN_BASENAME = "ghdl.so"


class ConfigError(Exception):
    """Required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Resolved server configuration."""

    plugin: Path
    yosys: str = DEFAULT_YOSYS
    ghdl: str = DEFAULT_GHDL
    ghdl_prefix: str | None = None
    timeout: float = DEFAULT_TIMEOUT


def _run_probe(argv: list[str]) -> str | None:
    """Run a short-lived probe command; its stdout or None."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _probe_datdir_plugin(yosys: str) -> Path | None:
    """``ghdl.so`` in the yosys data dir, per ``yosys-config --datdir``.

    The ghdl-yosys-plugin installs itself as ``<datdir>/plugins/ghdl.so``,
    which is also where yosys looks for a bare ``-m ghdl`` — so this is a
    sound last-resort fallback (e.g. inside the ru551n/hdl-docker image,
    where no plugin env var is set).
    """
    candidates: list[str] = []
    on_path = shutil.which("yosys-config")
    if on_path:
        candidates.append(on_path)
    if "/" in yosys:
        sibling = Path(yosys).expanduser().parent / "yosys-config"
        if sibling.is_file():
            candidates.append(str(sibling))
    for exe in candidates:
        out = _run_probe([exe, "--datdir"])
        if out is None:
            continue
        datdir = out.strip()
        if not datdir:
            continue
        for name in (PLUGIN_BASENAME, "ghdl_yosys.so"):
            candidate = Path(datdir) / "plugins" / name
            if candidate.is_file():
                return candidate
    return None


def _probe_ghdl_prefix(ghdl: str) -> str | None:
    """The library prefix of the ``ghdl`` CLI, or None.

    ``ghdl --dispconfig`` reports the prefix the CLI (and the libghdl the
    plugin embeds, from the same install) uses for its compiled std/ieee
    libraries. Used only when no prefix is configured explicitly.
    """
    if shutil.which(ghdl) is None:
        return None
    out = _run_probe([ghdl, "--dispconfig"])
    if out is None:
        return None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("library prefix:"):
            return stripped.split(":", 1)[1].strip() or None
    return None


def _find_plugin(env: Mapping[str, str], yosys: str) -> Path | None:
    raw = env.get("TSFPGA_MCP_GHDL_PLUGIN", "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise ConfigError(
                f"TSFPGA_MCP_GHDL_PLUGIN={raw!r} is not a file; build the "
                "plugin (ghdl-yosys-plugin) with `make` and point the "
                "variable at the resulting ghdl.so"
            )
        return path
    for entry in env.get("YOSYS_PLUGIN_PATH", "").split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        candidate = Path(entry).expanduser() / PLUGIN_BASENAME
        if candidate.is_file():
            return candidate
    return _probe_datdir_plugin(yosys)


def _find_timeout(env: Mapping[str, str]) -> float:
    raw = env.get("TSFPGA_MCP_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ConfigError(f"TSFPGA_MCP_TIMEOUT={raw!r} is not a number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError("TSFPGA_MCP_TIMEOUT must be a finite number > 0")
    return timeout


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a :class:`Config` from ``env`` (default: ``os.environ``).

    Raises:
        ConfigError: if no ghdl plugin can be located or the timeout is
            invalid. The MCP tools translate this into an actionable
            error string instead of raising.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    yosys = source.get("TSFPGA_MCP_YOSYS", DEFAULT_YOSYS).strip() or DEFAULT_YOSYS
    ghdl = source.get("TSFPGA_MCP_GHDL", DEFAULT_GHDL).strip() or DEFAULT_GHDL
    plugin = _find_plugin(source, yosys)
    if plugin is None:
        raise ConfigError(
            "ghdl-yosys-plugin not found: set TSFPGA_MCP_GHDL_PLUGIN to the "
            "path of ghdl.so (built from ghdl-yosys-plugin), add its "
            f"directory to YOSYS_PLUGIN_PATH so {PLUGIN_BASENAME} is found, "
            "or install it into the yosys plugin dir (yosys-config "
            "--datdir)/plugins"
        )
    ghdl_prefix = source.get("TSFPGA_MCP_GHDL_PREFIX", "").strip() or None
    if ghdl_prefix is None:
        ghdl_prefix = source.get("GHDL_PREFIX", "").strip() or None
    if ghdl_prefix is None:
        ghdl_prefix = _probe_ghdl_prefix(ghdl)
    return Config(
        plugin=plugin,
        yosys=yosys,
        ghdl=ghdl,
        ghdl_prefix=ghdl_prefix,
        timeout=_find_timeout(source),
    )
