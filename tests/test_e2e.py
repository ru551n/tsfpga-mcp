"""End-to-end tests: real yosys + ghdl + ghdl plugin + GHDL std/ieee libs.

Skipped (not failed) on machines without the full synthesis setup — see
the ``e2e`` fixture in conftest. The designs under tests/designs cover:

- VHDL top (counter), with and without a generic override, generic chip
  and one real vendor flow (xilinx)
- VHDL top (wrapper) instantiating a Verilog unit (vand) — mixed
  language
- Verilog top (vtop) instantiating a VHDL unit (vsub) via vhdl_entities
  — mixed language, the other direction
- SystemVerilog-only top (svtop), no VHDL involved
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tsfpga_mcp.server as server

DESIGNS = Path(__file__).parent / "designs"
COUNTER = str(DESIGNS / "counter.vhd")
VSUB = str(DESIGNS / "vsub.vhd")
VTOP = str(DESIGNS / "vtop.v")
SVTOP = str(DESIGNS / "svtop.sv")
WRAPPER = str(DESIGNS / "wrapper.vhd")
VAND = str(DESIGNS / "vand.v")
LIB_TOP = str(DESIGNS / "libtop.vhd")
LIB_LEAF = str(DESIGNS / "lib_leaf.vhd")


@pytest.fixture(autouse=True)
def _reset_config(monkeypatch):
    monkeypatch.setattr(server, "_config", None)
    monkeypatch.setattr(server, "_capabilities", None)


@pytest.fixture(autouse=True)
def _inject_test_env(monkeypatch, config_env):
    """The server reads os.environ; point it at the test yosys/plugin/libs."""
    for key in (
        "TSFPGA_MCP_YOSYS",
        "TSFPGA_MCP_GHDL",
        "TSFPGA_MCP_GHDL_PLUGIN",
        "TSFPGA_MCP_GHDL_PREFIX",
        "TSFPGA_MCP_TIMEOUT",
    ):
        if key in config_env:
            monkeypatch.setenv(key, config_env[key])


async def test_vhdl_top_default_generic(e2e):
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(sources=[COUNTER], top="counter", chip="generic")
    )
    assert result.startswith("Synthesis OK"), result
    assert "Resources:" in result


async def test_vhdl_top_generic_override(e2e):
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(
            sources=[COUNTER],
            top="counter",
            chip="generic",
            generics={"WIDTH": "8"},
        )
    )
    assert result.startswith("Synthesis OK"), result


async def test_vhdl_top_xilinx_flow(e2e):
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(
            sources=[COUNTER], top="counter", chip="xilinx", family="xc7"
        )
    )
    assert result.startswith("Synthesis OK"), result
    assert "xilinx (xc7)" in result
    assert any(
        name.startswith(("FDR", "FDCE", "FD")) or name == "FFs"
        for name in _resource_names(result)
    )


def _resource_names(result: str) -> list[str]:
    lines = result.splitlines()
    start = lines.index("Resources:") + 1
    return [line.strip().split()[0] for line in lines[start:] if line.strip()]


async def test_vhdl_top_with_verilog_submodule(e2e):
    """Mixed language: VHDL top, Verilog submodule."""
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(sources=[WRAPPER, VAND], top="wrapper", chip="generic")
    )
    assert result.startswith("Synthesis OK"), result


async def test_verilog_top_with_vhdl_entity(e2e):
    """Mixed language: Verilog top, VHDL unit made available via vhdl_entities."""
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(
            sources=[VSUB, VTOP],
            top="vtop",
            chip="generic",
            vhdl_entities=["vsub"],
        )
    )
    assert result.startswith("Synthesis OK"), result


async def test_systemverilog_only_top(e2e):
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(sources=[SVTOP], top="svtop", chip="generic")
    )
    assert result.startswith("Synthesis OK"), result


async def test_cross_library_vhdl_reference(e2e):
    """Top uses 'library leaf_lib; entity leaf_lib.leaf' to cross into a
    sibling library — the tsfpga.module.get_modules() per-module-folder
    library convention. Fails to even analyze without 'libraries'."""
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(
            sources=[LIB_TOP],
            libraries={"leaf_lib": [LIB_LEAF]},
            top="libtop",
            chip="generic",
        )
    )
    assert result.startswith("Synthesis OK"), result


async def test_cross_library_vhdl_reference_fails_without_libraries(e2e):
    """Same design, but with 'leaf' flattened into the 'sources'/top
    library instead of its own — GHDL cannot find library 'leaf_lib'."""
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(
            sources=[LIB_TOP, LIB_LEAF], top="libtop", chip="generic"
        )
    )
    assert result.startswith("Synthesis FAILED"), result


async def test_family_rejected_for_generic_chip(e2e):
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(
            sources=[COUNTER], top="counter", chip="generic", family="xc7"
        )
    )
    assert result.startswith("Error:")
    assert "does not accept a family" in result


async def test_discard_ffinit_rejected_for_non_microchip(e2e):
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(
            sources=[COUNTER], top="counter", chip="generic", discard_ffinit=True
        )
    )
    assert result.startswith("Error:")
    assert "discard_ffinit" in result


async def test_synthesis_failure_reports_diagnostics(e2e):
    """A nonexistent top level must surface as FAILED with diagnostics."""
    result = await server.tsfpga_synthesize(
        server.SynthesizeInput(sources=[COUNTER], top="bogus_top", chip="generic")
    )
    assert result.startswith("Synthesis FAILED"), result
    assert "Diagnostics:" in result


async def test_status_tool(e2e):
    result = await server.tsfpga_status()
    assert "tsfpga-mcp" in result
    assert "(exists)" in result
    assert "synthesis flows:" in result


async def test_targets_tool(e2e):
    result = await server.tsfpga_targets()
    assert "Yosys" in result
    assert "synth_xilinx" in result
    assert "generic" in result
    assert "families" in result


async def test_inspect_tool(e2e):
    result = await server.tsfpga_inspect(server.InspectInput(sources=[COUNTER, VTOP]))
    assert "VHDL entity counter" in result
    assert "generic: WIDTH" in result
    assert "Verilog module vtop" in result
