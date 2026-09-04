"""Unit tests for config loading (no yosys required)."""

from __future__ import annotations

import pytest

import tsfpga_mcp.config as config
from tsfpga_mcp.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def _no_probes(monkeypatch):
    """Keep the unit tests hermetic: no yosys-config/ghdl probes.

    The probes are covered by TestProbes below (which re-patches the
    low-level _run_probe per test); without this, tests asserting
    "no plugin" / "no prefix" would depend on what is installed on the
    host (and inside CI containers like ru551n/hdl-docker, where the
    probes would succeed by design).
    """
    monkeypatch.setattr(config, "_run_probe", lambda argv: None)


class TestDefaults:
    def test_defaults(self, tmp_path):
        plugin = tmp_path / "ghdl.so"
        plugin.touch()
        config_ = load_config({"TSFPGA_MCP_GHDL_PLUGIN": str(plugin)})
        assert config_.yosys == "yosys"
        assert config_.plugin == plugin
        assert config_.ghdl_prefix is None
        assert config_.timeout == 300.0

    def test_yosys_and_timeout_overrides(self, tmp_path):
        plugin = tmp_path / "ghdl.so"
        plugin.touch()
        config_ = load_config(
            {
                "TSFPGA_MCP_GHDL_PLUGIN": str(plugin),
                "TSFPGA_MCP_YOSYS": "/opt/bin/yosys",
                "TSFPGA_MCP_TIMEOUT": "12.5",
                "TSFPGA_MCP_GHDL_PREFIX": "/some/ghdl/libs",
            }
        )
        assert config_.yosys == "/opt/bin/yosys"
        assert config_.timeout == 12.5
        assert config_.ghdl_prefix == "/some/ghdl/libs"

    def test_blank_ghdl_prefix_treated_as_unset(self, tmp_path):
        plugin = tmp_path / "ghdl.so"
        plugin.touch()
        config_ = load_config(
            {
                "TSFPGA_MCP_GHDL_PLUGIN": str(plugin),
                "TSFPGA_MCP_GHDL_PREFIX": "   ",
            }
        )
        assert config_.ghdl_prefix is None


class TestTimeout:
    @pytest.mark.parametrize("raw", ["0", "-1", "abc", "1e999"])
    def test_invalid_timeout_rejected(self, tmp_path, raw):
        plugin = tmp_path / "ghdl.so"
        plugin.touch()
        with pytest.raises(ConfigError, match="TIMEOUT"):
            load_config(
                {
                    "TSFPGA_MCP_GHDL_PLUGIN": str(plugin),
                    "TSFPGA_MCP_TIMEOUT": raw,
                }
            )


class TestPlugin:
    def test_plugin_from_env(self, tmp_path):
        plugin = tmp_path / "ghdl.so"
        plugin.touch()
        assert load_config({"TSFPGA_MCP_GHDL_PLUGIN": str(plugin)}).plugin == plugin

    def test_plugin_from_yosys_plugin_path(self, tmp_path):
        other = tmp_path / "other.so"
        plugin = tmp_path / "ghdl.so"
        other.touch()
        plugin.touch()
        config_ = load_config({"YOSYS_PLUGIN_PATH": f"/nonexistent:{tmp_path}"})
        assert config_.plugin == plugin

    def test_explicit_plugin_wins_over_path(self, tmp_path):
        from_path = tmp_path / "ghdl.so"
        explicit = tmp_path / "mine.so"
        from_path.touch()
        explicit.touch()
        config_ = load_config(
            {
                "TSFPGA_MCP_GHDL_PLUGIN": str(explicit),
                "YOSYS_PLUGIN_PATH": str(tmp_path),
            }
        )
        assert config_.plugin == explicit

    def test_missing_explicit_plugin_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="not a file"):
            load_config({"TSFPGA_MCP_GHDL_PLUGIN": str(tmp_path / "nope.so")})

    def test_no_plugin_anywhere_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config({"YOSYS_PLUGIN_PATH": str(tmp_path)})


class TestProbes:
    """The yosys-config / ghdl --dispconfig fallbacks (driving _run_probe)."""

    def _which(self, monkeypatch, names: dict[str, str | None]):
        monkeypatch.setattr(config.shutil, "which", lambda name: names.get(name))

    def test_datdir_fallback_finds_plugin(self, monkeypatch, tmp_path):
        datdir = tmp_path / "share" / "yosys"
        (datdir / "plugins").mkdir(parents=True)
        plugin = datdir / "plugins" / "ghdl.so"
        plugin.touch()
        self._which(monkeypatch, {"yosys-config": "/usr/bin/yosys-config"})
        monkeypatch.setattr(
            config,
            "_run_probe",
            lambda argv: (
                str(datdir) if argv == ["/usr/bin/yosys-config", "--datdir"] else None
            ),
        )
        config_ = load_config({})
        assert config_.plugin == plugin

    def test_datdir_fallback_ghdl_yosys_name(self, monkeypatch, tmp_path):
        datdir = tmp_path / "share" / "yosys"
        (datdir / "plugins").mkdir(parents=True)
        plugin = datdir / "plugins" / "ghdl_yosys.so"
        plugin.touch()
        self._which(monkeypatch, {"yosys-config": "/usr/bin/yosys-config"})
        monkeypatch.setattr(
            config,
            "_run_probe",
            lambda argv: (
                str(datdir) if argv == ["/usr/bin/yosys-config", "--datdir"] else None
            ),
        )
        assert load_config({}).plugin == plugin

    def test_ghdl_prefix_probe_parses_dispconfig(self, monkeypatch, tmp_path):
        self._which(monkeypatch, {"ghdl": "/usr/bin/ghdl"})
        dispconfig = (
            "command_name: ghdl\n"
            "command line prefix (--PREFIX): (not set)\n"
            "library prefix: /usr/lib/ghdl\n"
            "default library paths:\n"
            " /usr/lib/ghdl/ieee/v93/\n"
        )
        monkeypatch.setattr(
            config,
            "_run_probe",
            lambda argv: dispconfig if argv == ["ghdl", "--dispconfig"] else None,
        )
        plugin = tmp_path / "ghdl.so"
        plugin.touch()
        config_ = load_config({"TSFPGA_MCP_GHDL_PLUGIN": str(plugin)})
        assert config_.ghdl_prefix == "/usr/lib/ghdl"

    def test_explicit_prefix_wins_over_inherited_and_probe(self, monkeypatch, tmp_path):
        self._which(monkeypatch, {"ghdl": "/usr/bin/ghdl"})
        monkeypatch.setattr(
            config,
            "_run_probe",
            lambda argv: "library prefix: /usr/lib/ghdl",
        )
        plugin = tmp_path / "ghdl.so"
        plugin.touch()
        config_ = load_config(
            {
                "TSFPGA_MCP_GHDL_PLUGIN": str(plugin),
                "TSFPGA_MCP_GHDL_PREFIX": "/explicit",
                "GHDL_PREFIX": "/inherited",
            }
        )
        assert config_.ghdl_prefix == "/explicit"

    def test_inherited_prefix_wins_over_probe(self, monkeypatch, tmp_path):
        self._which(monkeypatch, {"ghdl": "/usr/bin/ghdl"})
        monkeypatch.setattr(
            config,
            "_run_probe",
            lambda argv: "library prefix: /usr/lib/ghdl",
        )
        plugin = tmp_path / "ghdl.so"
        plugin.touch()
        config_ = load_config(
            {"TSFPGA_MCP_GHDL_PLUGIN": str(plugin), "GHDL_PREFIX": "/inherited"}
        )
        assert config_.ghdl_prefix == "/inherited"

    def test_probe_prefix_used_when_nothing_else(self, monkeypatch, tmp_path):
        self._which(monkeypatch, {"ghdl": "/usr/bin/ghdl"})
        monkeypatch.setattr(
            config,
            "_run_probe",
            lambda argv: "library prefix: /usr/lib/ghdl",
        )
        plugin = tmp_path / "ghdl.so"
        plugin.touch()
        config_ = load_config({"TSFPGA_MCP_GHDL_PLUGIN": str(plugin)})
        assert config_.ghdl_prefix == "/usr/lib/ghdl"

    def test_probe_failures_fall_back_to_none(self, monkeypatch, tmp_path):
        self._which(monkeypatch, {})  # no yosys-config, no ghdl on PATH
        plugin = tmp_path / "ghdl.so"
        plugin.touch()
        config_ = load_config({"TSFPGA_MCP_GHDL_PLUGIN": str(plugin)})
        assert config_.ghdl_prefix is None
        with pytest.raises(ConfigError, match="not found"):
            load_config({})
