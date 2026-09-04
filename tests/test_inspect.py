"""Unit tests for static source inspection (no yosys required)."""

from __future__ import annotations

from pathlib import Path

from tsfpga_mcp.inspect import inspect_sources, render_inspection

DESIGNS = Path(__file__).parent / "designs"
COUNTER = str(DESIGNS / "counter.vhd")
VSUB = str(DESIGNS / "vsub.vhd")
VTOP = str(DESIGNS / "vtop.v")
SVTOP = str(DESIGNS / "svtop.sv")


def test_vhdl_entity_architecture_and_generic():
    insp = inspect_sources([COUNTER])
    assert len(insp.entities) == 1
    e = insp.entities[0]
    assert e.name == "counter"
    assert e.architectures == ["rtl"]
    # Generic with a default, type positive.
    assert ("WIDTH", "positive", "4") in e.generics


def test_vhdl_multiple_architectures(tmp_path):
    src = """
    library ieee;
    entity foo is
      port (a : in std_logic);
    end entity;
    architecture one of foo is
    begin
    end architecture;
    architecture two of foo is
    begin
    end architecture;
    """
    p = tmp_path / "foo.vhd"
    p.write_text(src)
    insp = inspect_sources([str(p)])
    assert insp.entities[0].name == "foo"
    assert set(insp.entities[0].architectures) == {"one", "two"}


def test_verilog_module_parameters():
    insp = inspect_sources([VTOP])
    assert len(insp.modules) == 1
    m = insp.modules[0]
    assert m.name == "vtop"
    assert ("WIDTH", "4") in m.parameters


def test_mixed_sources():
    insp = inspect_sources([COUNTER, VTOP])
    assert [e.name for e in insp.entities] == ["counter"]
    assert [m.name for m in insp.modules] == ["vtop"]


def test_systemverilog_module():
    insp = inspect_sources([SVTOP])
    assert insp.modules[0].name == "svtop"


def test_read_error_collected_not_raised():
    insp = inspect_sources(["/nonexistent/nope.vhd"])
    assert insp.entities == []
    assert len(insp.errors) == 1
    assert "nope.vhd" in insp.errors[0]


def test_unsupported_extension():
    insp = inspect_sources(["foo.txt"])
    assert any("unsupported" in err for err in insp.errors)


def test_render_lists_units_and_notes():
    insp = inspect_sources([COUNTER, VTOP])
    text = render_inspection(insp)
    assert "VHDL entity counter" in text
    assert "architectures: rtl" in text
    assert "generic: WIDTH" in text
    assert "Verilog module vtop" in text
    assert "parameter: WIDTH = 4" in text


def test_render_flags_multiple_architectures(tmp_path):
    src = """
    entity bar is
      port (a : in std_logic);
    end entity;
    architecture x of bar is
    begin
    end architecture;
    architecture y of bar is
    begin
    end architecture;
    """
    p = tmp_path / "bar.vhd"
    p.write_text(src)
    text = render_inspection(inspect_sources([str(p)]))
    assert "ask which one to synthesize" in text


def test_render_flags_missing_architecture(tmp_path):
    src = "entity bare is\n  port (a : in std_logic);\nend entity;\n"
    p = tmp_path / "bare.vhd"
    p.write_text(src)
    text = render_inspection(inspect_sources([str(p)]))
    assert "no architecture found" in text
