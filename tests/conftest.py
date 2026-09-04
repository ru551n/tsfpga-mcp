"""Shared fixtures.

The test configuration is taken from the environment as-is: the
``TSFPGA_MCP_*`` variables when set, otherwise the config probes
(``yosys-config --datdir`` for the plugin, ``ghdl --dispconfig`` for the
GHDL library prefix) do the discovery. End-to-end tests run wherever a
full yosys + ghdl + ghdl plugin + compiled GHDL std/ieee setup exists (a
local install, the ru551n/hdl-docker image, or CI) and are skipped — not
failed — elsewhere.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tsfpga_mcp.config import Config, ConfigError, load_config

DESIGNS_DIR = Path(__file__).parent / "designs"


def _prefix_usable(prefix: str | None) -> bool:
    """A GHDL prefix must contain compiled std/ieee libraries."""
    if prefix is None:
        # Unset: ghdl falls back to its baked-in /usr/local/lib/ghdl.
        return Path("/usr/local/lib/ghdl/std").is_dir()
    base = Path(prefix)
    return (base / "std").is_dir() and (base / "ieee").is_dir()


def e2e_available(env: dict[str, str]) -> bool:
    """Whether a full yosys+ghdl+plugin+GHDL-libs setup is usable here."""
    if shutil.which(env.get("TSFPGA_MCP_YOSYS", "yosys")) is None:
        return False
    if shutil.which(env.get("TSFPGA_MCP_GHDL", "ghdl")) is None:
        return False
    try:
        config = load_config(env)
    except ConfigError:
        return False
    return _prefix_usable(config.ghdl_prefix)


@pytest.fixture
def config_env() -> dict[str, str]:
    return dict(os.environ)


@pytest.fixture
def config(config_env) -> Config:
    """A loadable config; skip when no plugin can be found."""
    try:
        return load_config(config_env)
    except ConfigError as exc:
        pytest.skip(f"tsfpga-mcp environment unavailable: {exc}")


@pytest.fixture
def e2e(config_env) -> Iterator[Config]:
    """A config for real synthesis runs; skip without a full setup."""
    if not e2e_available(config_env):
        pytest.skip(
            "yosys binary, ghdl binary, ghdl plugin, and GHDL std/ieee "
            "libraries all required (set TSFPGA_MCP_GHDL_PLUGIN / "
            "TSFPGA_MCP_GHDL_PREFIX)"
        )
    yield load_config(config_env)


@pytest.fixture
def designs_dir() -> Path:
    return DESIGNS_DIR
