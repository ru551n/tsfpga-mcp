"""Unit tests for the hierarchy.py rendering helpers (no yosys/ghdl required)."""

from __future__ import annotations

from tsfpga_mcp.hierarchy import hierarchy_failure, hierarchy_success


class TestRendering:
    def test_hierarchy_success_shows_tree(self):
        text = hierarchy_success(top="wrapper", elapsed=0.4, tree="=== wrapper ===\n")
        assert text.startswith("Hierarchy OK: top `wrapper` elaborated in 0.4s")
        assert "no synthesis run" in text
        assert "=== wrapper ===" in text

    def test_hierarchy_success_no_output_captured(self):
        text = hierarchy_success(top="wrapper", elapsed=0.1, tree="")
        assert "no hierarchy output captured" in text

    def test_hierarchy_failure_shows_diagnostics(self):
        text = hierarchy_failure("line1\nERROR: bad entity\n", 0.2)
        assert text.startswith("Hierarchy elaboration FAILED (0.2s).")
        assert "ERROR: bad entity" in text

    def test_hierarchy_failure_no_output_captured(self):
        text = hierarchy_failure("", 0.1)
        assert "(no output captured)" in text

    def test_hierarchy_failure_adds_missing_library_hint(self):
        output = 'top.vhd:7:9:error: cannot find resource library "leaf_lib"'
        text = hierarchy_failure(output, 0.1)
        assert "'libraries' parameter" in text
        assert "'leaf_lib'" in text
