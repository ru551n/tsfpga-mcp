"""Tests for tsfpga_mcp.artifacts (bitstream/artifact path discovery)."""

from __future__ import annotations

from pathlib import Path

from tsfpga_mcp.artifacts import find_artifacts, render_artifacts


def test_no_matching_line_returns_empty():
    assert find_artifacts("Synthesizing Vivado project in /tmp/foo/project") == []


def test_finds_artifacts_that_exist_on_disk(tmp_path: Path):
    output_path = tmp_path / "counter"
    output_path.mkdir()
    (output_path / "counter.bit").write_bytes(b"bits")
    (output_path / "counter.bin").write_bytes(b"bin")

    build_output = (
        f"Building Vivado project in {tmp_path}/counter/project, "
        f"placing artifacts in {output_path}\n"
        "... implementation output ..."
    )
    result = find_artifacts(build_output)

    assert len(result) == 1
    assert result[0].project == "counter"
    assert result[0].output_path == output_path
    assert set(result[0].files) == {
        output_path / "counter.bit",
        output_path / "counter.bin",
    }


def test_missing_files_reported_empty(tmp_path: Path):
    output_path = tmp_path / "counter"
    output_path.mkdir()
    build_output = (
        f"Building Vivado project in {tmp_path}/counter/project, "
        f"placing artifacts in {output_path}\n"
    )
    result = find_artifacts(build_output)
    assert len(result) == 1
    assert result[0].files == []


def test_xsa_included_when_present(tmp_path: Path):
    output_path = tmp_path / "counter"
    output_path.mkdir()
    (output_path / "counter.bit").write_bytes(b"bits")
    (output_path / "counter.bin").write_bytes(b"bin")
    (output_path / "counter.xsa").write_bytes(b"xsa")
    build_output = (
        f"Building Vivado project in {tmp_path}/counter/project, "
        f"placing artifacts in {output_path}\n"
    )
    result = find_artifacts(build_output)
    assert output_path / "counter.xsa" in result[0].files


def test_multiple_projects_in_one_build(tmp_path: Path):
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    out1.mkdir()
    out2.mkdir()
    (out1 / "a.bit").write_bytes(b"1")
    (out2 / "b.bit").write_bytes(b"1")
    build_output = (
        f"Building Vivado project in {tmp_path}/a/project, "
        f"placing artifacts in {out1}\n"
        f"Building Vivado project in {tmp_path}/b/project, "
        f"placing artifacts in {out2}\n"
    )
    result = find_artifacts(build_output)
    assert {r.project for r in result} == {"a", "b"}


def test_render_artifacts_empty():
    assert render_artifacts([]) == ""


def test_render_artifacts_lists_files_and_missing(tmp_path: Path):
    output_path = tmp_path / "counter"
    output_path.mkdir()
    build_output = (
        f"Building Vivado project in {tmp_path}/counter/project, "
        f"placing artifacts in {output_path}\n"
    )
    rendered = render_artifacts(find_artifacts(build_output))
    assert "Artifacts:" in rendered
    assert "none found" in rendered
