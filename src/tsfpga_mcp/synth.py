"""Synthesis backend: drives ``tsfpga.yosys.project.YosysNetlistBuild``.

Per synthesis request the server stages the given source files into one
throwaway ``tsfpga`` module *per VHDL library* (a flat directory per
library, each scanned by ``BaseModule.get_synthesis_files``), wraps them
in a ``ModuleList`` (one ``BaseModule`` per library, named accordingly),
and hands it to the ``generic``/Xilinx/Intel/Microchip
``YosysNetlistBuild`` subclass matching the requested chip. Files given
via plain ``sources`` are staged into the single library named after
``top``; files given via ``libraries`` are staged into the library named
by their dict key. This mirrors how a real tsfpga project
(``tsfpga.module.get_modules()``) gives each module folder its own
library, and lets GHDL resolve cross-library ``library x; entity x.y``
clauses.

- ``create()`` analyzes the VHDL sources with GHDL.
- ``build()`` elaborates the top (``ghdl`` yosys command, reading any
  Verilog/SystemVerilog sources first so mixed-language designs bind
  correctly either direction), runs the chip's ``synth_*`` flow, and
  parses the resulting utilization report into per-resource counts
  (``BuildResult.synthesis_size``).

Only the resource counts are reported back — tsfpga's build does not
produce a port-level netlist dump (no ``write_json`` step).
"""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tsfpga.generics import BitVectorGenericValue, StringGenericValue
from tsfpga.module import BaseModule
from tsfpga.module_list import ModuleList
from tsfpga.yosys.project import (
    YosysIntelNetlistBuild,
    YosysMicrochipNetlistBuild,
    YosysNetlistBuild,
    YosysXilinxNetlistBuild,
)

from .config import Config
from .inspect import VERILOG_EXTENSIONS, VHDL_EXTENSIONS, inspect_sources

_ALL_EXTENSIONS = VHDL_EXTENSIONS | VERILOG_EXTENSIONS

_VHDL_INTEGER_TYPES = ("integer", "natural", "positive")
_VHDL_VECTOR_TYPES = (
    "std_logic_vector",
    "std_ulogic_vector",
    "unsigned",
    "signed",
    "std_logic",
    "std_ulogic",
    "bit_vector",
)


class SynthError(Exception):
    """A synthesis request could not even be attempted."""


@dataclass(frozen=True)
class ChipSpec:
    build_class: type[YosysNetlistBuild]
    flow: str
    description: str
    families: tuple[str, ...] = ()
    supports_family: bool = False


CHIPS: dict[str, ChipSpec] = {
    "generic": ChipSpec(
        build_class=YosysNetlistBuild,
        flow="synth",
        description=(
            "Vendor-independent RTL check (yosys `synth`). No aggregated "
            "resource counts are available for this flow, only raw "
            "primitive cell counts."
        ),
    ),
    "xilinx": ChipSpec(
        build_class=YosysXilinxNetlistBuild,
        flow="synth_xilinx",
        description=(
            "Xilinx primitives (LUTs, FDs, RAMB18/36, DSP48) via `synth_xilinx`."
        ),
        families=("xc6s", "xc7", "xcu", "xcup"),
        supports_family=True,
    ),
    "intel": ChipSpec(
        build_class=YosysIntelNetlistBuild,
        flow="synth_intel",
        description=(
            "Intel/Altera MAX10, Cyclone IV, Cyclone IV E and Cyclone 10 "
            "LP primitives via `synth_intel` (ALM-based Cyclone V / "
            "Cyclone 10 GX devices are not covered)."
        ),
        families=("max10", "cycloneiv", "cycloneive", "cyclone10lp"),
        supports_family=True,
    ),
    "microchip": ChipSpec(
        build_class=YosysMicrochipNetlistBuild,
        flow="synth_microchip",
        description="Microchip PolarFire primitives via `synth_microchip`.",
        families=("polarfire",),
        supports_family=True,
    ),
}


def chip_spec(chip: str) -> ChipSpec:
    try:
        return CHIPS[chip]
    except KeyError:
        known = ", ".join(sorted(CHIPS))
        raise SynthError(f"Unknown chip {chip!r}; known chips: {known}") from None


def _validate_family(chip: str, spec: ChipSpec, family: str | None) -> None:
    """Raise ``SynthError`` for a family that the chip cannot accept, or
    that isn't one of its known device families (a typo'd family would
    otherwise proceed silently into a deep, confusing yosys failure)."""
    if family is None:
        return
    if not spec.supports_family:
        raise SynthError(f"chip {chip!r} does not accept a family.")
    if family and spec.families and family not in spec.families:
        known = ", ".join(spec.families)
        raise SynthError(
            f"Unknown family {family!r} for chip {chip!r}; known families: {known}."
        )


@dataclass
class SynthResult:
    success: bool
    output: str
    resources: dict[str, int] = field(default_factory=dict)
    elapsed: float = 0.0


def _classify(sources: Sequence[str]) -> None:
    bad = [s for s in sources if Path(s).suffix.lower() not in _ALL_EXTENSIONS]
    if bad:
        raise SynthError(
            "Unsupported source extension (expected .vhd/.vhdl/.v/.sv): "
            + ", ".join(bad)
        )
    missing = [s for s in sources if not Path(s).is_file()]
    if missing:
        raise SynthError("Source file(s) not found: " + ", ".join(missing))


def _stage_libraries(
    modules_root: Path, libraries: Mapping[str, Sequence[str]]
) -> dict[str, Path]:
    """Copy each library's sources (from wherever they live) into its own
    flat directory under ``modules_root``, one per library.

    tsfpga's ``BaseModule`` scans a module's own directory (non-recursive)
    for source files, so every file for a given library ends up directly
    under that library's directory regardless of its original location.
    Base names must therefore be unique within a library, but may repeat
    across different libraries (each gets its own directory/GHDL library).
    """
    library_dirs: dict[str, Path] = {}
    for library, lib_sources in libraries.items():
        if not lib_sources:
            continue
        module_dir = modules_root / library
        module_dir.mkdir(parents=True, exist_ok=True)
        seen: dict[str, str] = {}
        for src in lib_sources:
            path = Path(src)
            name = path.name
            if name in seen and seen[name] != str(path):
                raise SynthError(
                    f"Duplicate source file name {name!r} in library "
                    f"{library!r} ({seen[name]} vs {path}); each library "
                    "is staged in its own flat directory, so base names "
                    "must be unique within a library (they may repeat "
                    "across different libraries)."
                )
            seen[name] = str(path)
            shutil.copy2(path, module_dir / name)
        library_dirs[library] = module_dir
    return library_dirs


def _generic_types_for_top(sources: Sequence[str], top: str) -> dict[str, str]:
    """VHDL generic name (lowercased) -> declared type, for ``top``."""
    inspection = inspect_sources(list(sources))
    types: dict[str, str] = {}
    for entity in inspection.entities:
        if entity.name.lower() != top.lower():
            continue
        for name, vhdl_type, _default in entity.generics:
            types.setdefault(name.lower(), vhdl_type)
    return types


def _typed_generic(name: str, raw: str, vhdl_type: str) -> Any:
    base_type = vhdl_type.split("(", 1)[0].strip().lower()
    if base_type == "boolean":
        if raw.strip().lower() not in ("true", "false"):
            raise SynthError(
                f"Generic {name!r} is boolean; value must be 'true' or "
                f"'false', got {raw!r}."
            )
        return raw.strip().lower() == "true"
    if base_type in _VHDL_INTEGER_TYPES:
        try:
            return int(raw)
        except ValueError:
            raise SynthError(
                f"Generic {name!r} is {vhdl_type}; not an integer: {raw!r}"
            ) from None
    if base_type == "real":
        try:
            return float(raw)
        except ValueError:
            raise SynthError(
                f"Generic {name!r} is real; not a float: {raw!r}"
            ) from None
    if base_type in _VHDL_VECTOR_TYPES:
        return BitVectorGenericValue(raw)
    if base_type == "string":
        return StringGenericValue(raw)
    raise SynthError(
        f"Generic {name!r} has unsupported VHDL type {vhdl_type!r}; "
        "supported: boolean, integer/natural/positive, real, "
        "std_logic_vector/unsigned/signed/std_logic/bit_vector, string."
    )


def _typed_generics(
    sources: Sequence[str], top: str, generics: Mapping[str, str]
) -> dict[str, Any]:
    vhdl_generic_types = _generic_types_for_top(sources, top)
    if not vhdl_generic_types:
        raise SynthError(
            f"Generics given but {top!r} is not a VHDL entity in the "
            "given sources; tsfpga only supports generic overrides for a "
            "VHDL top level (call tsfpga_inspect to check)."
        )
    typed: dict[str, Any] = {}
    for name, raw in generics.items():
        vhdl_type = vhdl_generic_types.get(name.lower())
        if vhdl_type is None:
            known = ", ".join(sorted(vhdl_generic_types)) or "(none)"
            raise SynthError(
                f"Generic {name!r} not found on entity {top!r}; declared "
                f"generics: {known}."
            )
        typed[name] = _typed_generic(name, raw, vhdl_type)
    return typed


def _resolve_executable(value: str, exe_name: str) -> Path:
    candidate = Path(value)
    if candidate.is_file():
        return candidate.resolve()
    found = shutil.which(value)
    if found is None:
        raise SynthError(f"Cannot find {exe_name} executable {value!r} on PATH.")
    return Path(found).resolve()


def synthesize(
    config: Config,
    sources: Sequence[str],
    top: str,
    chip: str,
    family: str | None,
    vhdl_entities: Sequence[str],
    generics: Mapping[str, str],
    vhdl_standard: str,
    discard_ffinit: bool,
    libraries: Mapping[str, Sequence[str]] | None = None,
) -> SynthResult:
    """Run one synthesis. Blocking — call via ``asyncio.to_thread``."""
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
    spec = chip_spec(chip)

    _validate_family(chip, spec, family)
    build_kwargs: dict[str, Any] = {}
    if family is not None:
        build_kwargs["family"] = family
    if discard_ffinit:
        if chip != "microchip":
            raise SynthError("discard_ffinit is only supported for chip='microchip'.")
        build_kwargs["discard_ffinit"] = True

    typed_generics = _typed_generics(flat_sources, top, generics) if generics else None

    tmp_root = Path(tempfile.mkdtemp(prefix="tsfpga-mcp-"))
    start = time.monotonic()
    try:
        library_dirs = _stage_libraries(tmp_root / "modules", all_libraries)
        modules = ModuleList()
        for library, module_dir in library_dirs.items():
            modules.append(BaseModule(path=module_dir, library_name=library))

        build = spec.build_class(
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
            **build_kwargs,
        )

        project_path = tmp_root / "project"
        output_path = tmp_root / "output"
        captured = io.StringIO()
        with redirect_stdout(captured):
            try:
                created = build.create(project_path=project_path)
                result = (
                    build.build(project_path=project_path, output_path=output_path)
                    if created
                    else None
                )
            except (ValueError, FileNotFoundError) as exc:
                print(f"ERROR: {exc}")
                created, result = False, None

        elapsed = time.monotonic() - start
        output = captured.getvalue()
        if not created or result is None or not result.success:
            return SynthResult(success=False, output=output, elapsed=elapsed)
        return SynthResult(
            success=True,
            output=output,
            resources=dict(result.synthesis_size or {}),
            elapsed=elapsed,
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _format_resources(resources: Mapping[str, int]) -> str:
    if not resources:
        return "  (no resource counts reported)"
    width = max(len(name) for name in resources)
    return "\n".join(f"  {name:<{width}}  {count}" for name, count in resources.items())


def build_success(
    top: str,
    chip: str,
    family: str | None,
    resources: Mapping[str, int],
    elapsed: float,
) -> str:
    target = chip if family is None else f"{chip} ({family})"
    lines = [
        f"Synthesis OK: top `{top}` -> {target} in {elapsed:.1f}s.",
        "",
        "Resources:",
        _format_resources(resources),
    ]
    return "\n".join(lines)


def _error_context_lines(lines: Sequence[str], context: int = 2) -> list[str] | None:
    """Lines around every "error" occurrence, in order, de-duplicated.

    A blind tail can bury the actual root-cause error under repeated
    benign warnings/notes that happen to follow it (e.g. GHDL emitting the
    same "note: found RAM" block once per elaborated instance). Surfacing
    every "error"-containing line (plus a little context) instead makes
    sure the real cause is always visible. Returns None when the output
    contains no line mentioning "error" at all (e.g. a bare exception),
    so the caller can fall back to a plain tail.
    """
    error_idxs = [i for i, line in enumerate(lines) if "error" in line.lower()]
    if not error_idxs:
        return None

    keep: set[int] = set()
    for idx in error_idxs:
        keep.update(range(max(0, idx - context), min(len(lines), idx + context + 1)))

    result: list[str] = []
    previous: int | None = None
    for idx in sorted(keep):
        if previous is not None and idx != previous + 1:
            result.append("...")
        result.append(lines[idx])
        previous = idx
    return result


_MISSING_LIBRARY_RE = re.compile(
    r"""cannot\ find\ resource\ library\ "([^"]+)"
      | failed\ to\ find\ library\ '([^']+)'""",
    re.VERBOSE,
)


def _missing_library_hint(output: str) -> str | None:
    """A one-line nudge when GHDL failed because a referenced VHDL library
    was never analyzed at all (as opposed to a real syntax/semantic error).

    This is the single most common first-time mistake with this tool: a
    top level that cross-references a sibling library (tsfpga's own
    per-module-folder convention, ``library <name>; entity <name>.<x>``)
    but whose source was passed flat via ``sources`` instead of under its
    own ``libraries`` entry. GHDL's own error ("cannot find resource
    library"/"failed to find library") doesn't mention this tool's
    ``libraries`` parameter, so point at it explicitly instead of leaving
    the caller to rediscover the fix from GHDL wording alone.
    """
    names = dict.fromkeys(
        next(g for g in match.groups() if g is not None)
        for match in _MISSING_LIBRARY_RE.finditer(output)
    )
    if not names:
        return None
    quoted = ", ".join(f"'{name}'" for name in names)
    return (
        f"Hint: GHDL could not find librar{'y' if len(names) == 1 else 'ies'} "
        f"{quoted} — if the design's sources span more than one VHDL "
        "library (e.g. a top level using 'library <name>; entity "
        "<name>.<entity>' to cross into a sibling library, as produced by "
        "tsfpga's own per-module-folder convention), pass that library's "
        "source files via the 'libraries' parameter instead of (or in "
        "addition to) 'sources'."
    )


def build_failure(output: str, elapsed: float) -> str:
    lines = output.strip().splitlines()
    selected = _error_context_lines(lines) if lines else None
    if selected is None:
        selected = lines[-40:]
    diagnostics = "\n".join(selected) or "(no output captured)"
    result = [f"Synthesis FAILED ({elapsed:.1f}s).", "", "Diagnostics:", diagnostics]
    hint = _missing_library_hint(output)
    if hint is not None:
        result += ["", hint]
    return "\n".join(result)
