"""Unit tests for the synth.py helpers (no yosys/ghdl required)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tsfpga.generics import BitVectorGenericValue, StringGenericValue

from tsfpga_mcp.synth import (
    CHIPS,
    SynthError,
    _classify,
    _generic_types_for_top,
    _stage_sources,
    _typed_generic,
    _typed_generics,
    build_failure,
    build_success,
    chip_spec,
)

DESIGNS = Path(__file__).parent / "designs"
COUNTER = str(DESIGNS / "counter.vhd")
VTOP = str(DESIGNS / "vtop.v")


class TestChipSpec:
    def test_known_chips(self):
        assert set(CHIPS) == {"generic", "xilinx", "intel", "microchip"}

    def test_unknown_chip_rejected(self):
        with pytest.raises(SynthError, match="Unknown chip"):
            chip_spec("ice40")

    def test_generic_does_not_support_family(self):
        assert chip_spec("generic").supports_family is False

    def test_xilinx_supports_family(self):
        assert chip_spec("xilinx").supports_family is True


class TestClassify:
    def test_rejects_unsupported_extension(self, tmp_path):
        bad = tmp_path / "foo.txt"
        bad.write_text("x")
        with pytest.raises(SynthError, match="Unsupported source extension"):
            _classify([str(bad)])

    def test_rejects_missing_file(self, tmp_path):
        with pytest.raises(SynthError, match="not found"):
            _classify([str(tmp_path / "nope.vhd")])

    def test_accepts_known_extensions(self):
        _classify([COUNTER, VTOP])


class TestStageSources:
    def test_flattens_into_module_dir(self, tmp_path):
        module_dir = tmp_path / "modules" / "counter"
        _stage_sources(module_dir, [COUNTER])
        assert (module_dir / "counter.vhd").read_text() == Path(COUNTER).read_text()

    def test_duplicate_basename_rejected(self, tmp_path):
        other = tmp_path / "src_a" / "counter.vhd"
        other.parent.mkdir()
        other.write_text("-- different file, same name")
        module_dir = tmp_path / "modules" / "top"
        with pytest.raises(SynthError, match="Duplicate source file name"):
            _stage_sources(module_dir, [COUNTER, str(other)])

    def test_same_path_twice_is_not_a_duplicate(self, tmp_path):
        module_dir = tmp_path / "modules" / "counter"
        _stage_sources(module_dir, [COUNTER, COUNTER])


class TestGenericTypes:
    def test_finds_generics_of_named_entity(self):
        types = _generic_types_for_top([COUNTER], "counter")
        assert types == {"width": "positive"}

    def test_case_insensitive_top_match(self):
        types = _generic_types_for_top([COUNTER], "COUNTER")
        assert types == {"width": "positive"}

    def test_no_match_returns_empty(self):
        assert _generic_types_for_top([COUNTER], "nope") == {}


class TestTypedGeneric:
    def test_boolean(self):
        assert _typed_generic("g", "true", "boolean") is True
        assert _typed_generic("g", "FALSE", "boolean") is False

    def test_invalid_boolean_rejected(self):
        with pytest.raises(SynthError, match="boolean"):
            _typed_generic("g", "1", "boolean")

    @pytest.mark.parametrize("vhdl_type", ["integer", "natural", "positive"])
    def test_integer_types(self, vhdl_type):
        assert _typed_generic("g", "8", vhdl_type) == 8

    def test_invalid_integer_rejected(self):
        with pytest.raises(SynthError, match="not an integer"):
            _typed_generic("g", "abc", "integer")

    def test_real(self):
        assert _typed_generic("g", "1.5", "real") == 1.5

    def test_vector_type(self):
        value = _typed_generic("g", "1010", "std_logic_vector(3 downto 0)")
        assert isinstance(value, BitVectorGenericValue)
        assert value.value == "1010"

    def test_string_type(self):
        value = _typed_generic("g", "hello", "string")
        assert isinstance(value, StringGenericValue)
        assert value.value == "hello"

    def test_unsupported_type_rejected(self):
        with pytest.raises(SynthError, match="unsupported VHDL type"):
            _typed_generic("g", "1", "some_record_type")


class TestTypedGenerics:
    def test_converts_known_generic(self):
        typed = _typed_generics([COUNTER], "counter", {"WIDTH": "8"})
        assert typed == {"WIDTH": 8}

    def test_non_vhdl_top_rejected(self):
        with pytest.raises(SynthError, match="not a VHDL entity"):
            _typed_generics([VTOP], "vtop", {"WIDTH": "4"})

    def test_unknown_generic_name_rejected(self):
        with pytest.raises(SynthError, match="not found on entity"):
            _typed_generics([COUNTER], "counter", {"NOPE": "1"})


class TestRendering:
    def test_build_success_lists_resources(self):
        text = build_success(
            top="counter",
            chip="xilinx",
            family="xc7",
            resources={"Total LUTs": 4, "FFs": 4},
            elapsed=0.4,
        )
        assert "Synthesis OK: top `counter` -> xilinx (xc7) in 0.4s." in text
        assert "Total LUTs" in text
        assert "FFs" in text

    def test_build_success_with_no_resources(self):
        text = build_success(
            top="counter", chip="generic", family=None, resources={}, elapsed=0.1
        )
        assert "no resource counts reported" in text

    def test_build_failure_shows_diagnostics(self):
        text = build_failure("line1\nERROR: bad entity\n", 0.2)
        assert text.startswith("Synthesis FAILED (0.2s).")
        assert "ERROR: bad entity" in text
