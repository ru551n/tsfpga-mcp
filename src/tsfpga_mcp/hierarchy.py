"""Hierarchy backend: GHDL elaboration only, no technology mapping.

``synth.py``'s ``synthesize()`` gets a fully generics-resolved, GHDL-
elaborated instance hierarchy "for free" as an intermediate step inside
its Yosys script — the ``ghdl <entity>`` command line(s) built by
``YosysNetlistBuild._get_ghdl_commands()`` — but then immediately runs the
chip's ``synth``/``synth_xilinx``/``synth_intel``/``synth_microchip`` flow
(with ``-flatten``) on top of it, and only reports the resulting
aggregate resource counts. That technology-mapping step is the expensive
part, and it destroys the very module/instance structure this module
wants: ``_get_synth_command()`` deliberately flattens the design so that
the utilization report reflects primitive counts for the whole design,
not a hierarchy.

This module reuses the same source-staging and ``YosysNetlistBuild``
setup, but stops right after ``build.create()`` (the cheap ``ghdl -a``
analysis step, unchanged) and then runs its own minimal Yosys script:
just the ``ghdl`` elaborate command(s) (generics baked in, exactly as
``synthesize()`` would run them) plus ``hierarchy -check`` and a plain
``stat`` (no ``-flatten``, no ``synth*`` techmap). ``stat``'s per-module
report already lists each elaborated module's own direct submodule
instances (by type and count) — this is genuinely the same computation
``synthesize()`` performs and discards, just captured before the
flattening synth pass would erase the structure. There is no dedicated
"print the instance tree" Yosys command (older tooling folklore
notwithstanding — this Yosys build's ``ls`` has no ``-tree`` option), so
``stat`` is the closest stable, already-battle-tested (this codebase's
own ``_get_size`` already parses its output) building block for the job.

Only VHDL entities get their own distinctly-named elaborated module per
generic specialization (GHDL mangles cross-library entity/architecture
names, e.g. ``leaf_Brtl_Lleaf_lib``) — this is the "generics-resolved,
generate-blocks-expanded" part: each concrete elaboration is a distinct
RTLIL module, not a generic template.
"""

from __future__ import annotations

import io
import shutil
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from tsfpga.generics import BitVectorGenericValue, StringGenericValue
from tsfpga.module import BaseModule
from tsfpga.module_list import ModuleList
from tsfpga.yosys.common import run_yosys, to_yosys_path
from tsfpga.yosys.project import YosysNetlistBuild

from .config import Config
from .synth import (
    SynthError,
    _classify,
    _error_context_lines,
    _missing_library_hint,
    _resolve_executable,
    _stage_libraries,
    _typed_generics,
)


@dataclass
class HierarchyResult:
    success: bool
    output: str
    elapsed: float = 0.0


def hierarchy(
    config: Config,
    sources: Sequence[str],
    top: str,
    vhdl_entities: Sequence[str],
    generics: Mapping[str, str],
    vhdl_standard: str,
    libraries: Mapping[str, Sequence[str]] | None = None,
) -> HierarchyResult:
    """Elaborate one design with GHDL and return its instance hierarchy.

    Blocking — call via ``asyncio.to_thread``. Mirrors
    ``synth.synthesize()``'s source-staging and error-handling
    conventions, but never runs a ``synth*`` techmap pass.
    """
    all_libraries: dict[str, list[str]] = {
        name: list(files) for name, files in (libraries or {}).items()
    }
    if sources:
        all_libraries[top] = list(sources) + all_libraries.get(top, [])

    flat_sources = [src for files in all_libraries.values() for src in files]
    if not flat_sources:
        raise SynthError(
            "No source files given (both 'sources' and 'libraries' are empty)."
        )

    _classify(flat_sources)

    typed_generics = _typed_generics(flat_sources, top, generics) if generics else None

    tmp_root = Path(tempfile.mkdtemp(prefix="tsfpga-mcp-"))
    start = time.monotonic()
    try:
        library_dirs = _stage_libraries(tmp_root / "modules", all_libraries)
        modules = ModuleList()
        for library, module_dir in library_dirs.items():
            modules.append(BaseModule(path=module_dir, library_name=library))

        build = YosysNetlistBuild(
            name=top,
            modules=modules,
            top=top,
            vhdl_entities=list(vhdl_entities) or None,
            generics=typed_generics,
            vhdl_standard=vhdl_standard,
            yosys_path=_resolve_executable(config.yosys, "yosys"),
            ghdl_path=_resolve_executable(config.ghdl, "ghdl"),
            ghdl_plugin_path=config.plugin,
            ghdl_prefix=Path(config.ghdl_prefix) if config.ghdl_prefix else None,
        )

        project_path = tmp_root / "project"
        output_path = tmp_root / "output"
        output_path.mkdir(parents=True, exist_ok=True)
        report_file = output_path / f"{top}_hierarchy.txt"

        captured = io.StringIO()
        with redirect_stdout(captured):
            created = False
            elaborated = False
            try:
                created = build.create(project_path=project_path)
                if created:
                    elaborated = _elaborate_hierarchy(
                        build=build,
                        project_path=project_path,
                        output_path=output_path,
                        report_file=report_file,
                        all_generics=typed_generics or {},
                    )
            except (ValueError, FileNotFoundError) as exc:
                print(f"ERROR: {exc}")
                created, elaborated = False, False

        elapsed = time.monotonic() - start
        output = captured.getvalue()
        if not created or not elaborated:
            return HierarchyResult(success=False, output=output, elapsed=elapsed)

        tree = report_file.read_text() if report_file.is_file() else output
        return HierarchyResult(success=True, output=tree, elapsed=elapsed)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _elaborate_hierarchy(
    build: YosysNetlistBuild,
    project_path: Path,
    output_path: Path,
    report_file: Path,
    all_generics: dict[str, bool | float | StringGenericValue | BitVectorGenericValue],
) -> bool:
    """Build and run the minimal "elaborate + hierarchy check + stat"
    Yosys script (no ``synth*`` techmap), mirroring exactly how
    ``YosysNetlistBuild.build()`` itself assembles and runs its own
    script (same read_verilog/ghdl command builders, same
    ``run_yosys()`` call convention) — see ``project.py``'s ``build()``
    and ``_get_yosys_script()``.
    """
    workdir = build._get_ghdl_workdir(project_path=project_path)

    commands: list[str] = []
    read_verilog_command = build._get_read_verilog_command()
    if read_verilog_command is not None:
        commands.append(read_verilog_command)

    commands += build._get_ghdl_commands(
        workdir=workdir, all_generics=all_generics
    )
    commands += [
        f"hierarchy -check -top {build.top}",
        f'tee -o "{to_yosys_path(report_file)}" stat',
    ]
    script = "\n".join(commands) + "\n"

    script_file = build.project_file(project_path=output_path)
    script_file.write_text(script)

    if not run_yosys(
        yosys_path=build._yosys_path,
        ghdl_plugin_path=build._ghdl_plugin_path,
        script_file=script_file,
        cwd=output_path,
        ghdl_path=build._ghdl_path,
        ghdl_prefix=build._ghdl_prefix,
    ):
        print(f'ERROR: Yosys hierarchy elaboration failed for "{build.name}".')
        return False
    return True


def hierarchy_success(top: str, elapsed: float, tree: str) -> str:
    lines = [
        f"Hierarchy OK: top `{top}` elaborated in {elapsed:.1f}s (no synthesis run).",
        "",
        "Hierarchy:",
        tree.strip() or "(no hierarchy output captured)",
    ]
    return "\n".join(lines)


def hierarchy_failure(output: str, elapsed: float) -> str:
    lines = output.strip().splitlines()
    selected = _error_context_lines(lines) if lines else None
    if selected is None:
        selected = lines[-40:]
    diagnostics = "\n".join(selected) or "(no output captured)"
    result = [
        f"Hierarchy elaboration FAILED ({elapsed:.1f}s).",
        "",
        "Diagnostics:",
        diagnostics,
    ]
    hint = _missing_library_hint(output)
    if hint is not None:
        result += ["", hint]
    return "\n".join(result)
