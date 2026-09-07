"""Tests for tsfpga_mcp.build_diagnostics (Vivado ERROR/CRITICAL WARNING triage)."""

from __future__ import annotations

from tsfpga_mcp.build_diagnostics import build_diagnostics


def test_no_diagnostic_lines_returns_none():
    assert build_diagnostics("just some\nnormal output\nno issues here") is None


def test_lowercase_error_mentions_are_not_matched():
    # Vivado's own diagnostic lines always use the literal 'ERROR:'/
    # 'CRITICAL WARNING:' tokens; incidental narrative mentions of "error"
    # should not trigger triage (that would defeat the point).
    assert build_diagnostics("no errors were encountered during synthesis") is None


def test_surfaces_error_line_with_context():
    output = "\n".join(
        [f"line {i}" for i in range(5)]
        + ["ERROR: [Synth 8-439] something bad happened"]
        + [f"line {i}" for i in range(5, 10)]
    )
    result = build_diagnostics(output, context=1)
    assert result is not None
    assert "1 ERROR(s), 0 CRITICAL WARNING(s) found:" in result
    assert "ERROR: [Synth 8-439] something bad happened" in result
    assert "line 4" in result
    assert "line 5" in result
    assert "line 0" not in result


def test_counts_errors_and_critical_warnings_separately():
    output = (
        "ERROR: [Foo 1-1] a\n"
        "CRITICAL WARNING: [Bar 2-2] b\n"
        "CRITICAL WARNING: [Bar 2-3] c\n"
    )
    result = build_diagnostics(output)
    assert result is not None
    assert "1 ERROR(s), 2 CRITICAL WARNING(s) found:" in result


def test_non_adjacent_matches_get_ellipsis_separator():
    output = "\n".join(
        ["ERROR: first"] + [f"noise {i}" for i in range(20)] + ["ERROR: second"]
    )
    result = build_diagnostics(output, context=1)
    assert result is not None
    assert "..." in result


def test_truncates_when_over_max_lines():
    output = "\n".join(f"ERROR: line {i}" for i in range(50))
    result = build_diagnostics(output, context=0, max_lines=10)
    assert result is not None
    assert "truncated" in result
    assert "ERROR: line 49" in result
