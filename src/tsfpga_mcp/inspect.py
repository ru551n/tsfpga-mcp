"""Static inspection of VHDL and Verilog sources.

Discovers the synthesizable units a design offers — VHDL entities with
their architectures and generic defaults, Verilog modules with their
parameter defaults — so the caller can decide which top level,
architecture and generic values to synthesize, and can ask the user when
the answer is not unique. No synthesis tools are needed here; the sources
are only read and scanned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

VHDL_EXTENSIONS = {".vhd", ".vhdl"}
VERILOG_EXTENSIONS = {".v", ".sv"}


@dataclass
class VhdlEntity:
    name: str
    file: str
    architectures: list[str] = field(default_factory=list)
    # (name, type, default) — default empty when no := given.
    generics: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class VerilogModule:
    name: str
    file: str
    # (name, default).
    parameters: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Inspection:
    entities: list[VhdlEntity] = field(default_factory=list)
    modules: list[VerilogModule] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


_ENTITY_RE = re.compile(r"^\s*entity\s+([A-Za-z_]\w*)\s+is\b", re.I | re.M)
_ARCH_RE = re.compile(
    r"^\s*architecture\s+([A-Za-z_]\w*)\s+of\s+([A-Za-z_]\w*)\s+is\b", re.I | re.M
)
_MODULE_RE = re.compile(
    r"^\s*module\s+([A-Za-z_]\w*)\s*(?:#\s*\(([^)]*)\))?\s*\(", re.I | re.M
)
_PARAM_RE = re.compile(r"^\s*parameter\s+([A-Za-z_]\w*)\s*=\s*([^,)\n]+)", re.I | re.M)
_GENERIC_ENTRY_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*:\s*([\w.]+(?:\s*\([^)]*\))?)(?:\s*:=\s*(.+?))?\s*$"
)


def _generic_block(text: str, pos: int) -> str:
    """The generic (...) list of the entity declared at ``pos``, or ''."""
    end = re.search(r"\bport\s*\(|\bend\s+entity\b", text[pos:], re.I)
    window = text[pos : pos + end.start()] if end else text[pos : pos + 4000]
    m = re.search(r"\bgeneric\s*\(", window, re.I)
    if not m:
        return ""
    body = window[m.end() :]
    depth, i = 1, 0
    while i < len(body) and depth:
        if body[i] == "(":
            depth += 1
        elif body[i] == ")":
            depth -= 1
        i += 1
    return body[: i - 1]


def _parse_generics(block: str) -> list[tuple[str, str, str]]:
    generics: list[tuple[str, str, str]] = []
    for chunk in block.split(";"):
        m = _GENERIC_ENTRY_RE.match(chunk)
        if m:
            generics.append((m.group(1), m.group(2), (m.group(3) or "").strip()))
    return generics


def inspect_sources(sources: list[str]) -> Inspection:
    """Scan ``sources`` and collect the units they declare.

    Read errors per file are collected in ``Inspection.errors`` instead of
    raising, so one bad file does not hide the others.
    """
    result = Inspection()
    seen: set[str] = set()
    for src in sources:
        ext = Path(src).suffix.lower()
        if ext not in VHDL_EXTENSIONS | VERILOG_EXTENSIONS:
            result.errors.append(
                f"{src}: unsupported extension (expected .vhd/.vhdl/.v/.sv)"
            )
            continue
        try:
            text = Path(src).read_text(errors="replace")
        except OSError as exc:
            result.errors.append(f"{src}: {exc.strerror}")
            continue
        if ext in VHDL_EXTENSIONS:
            for m in _ENTITY_RE.finditer(text):
                entity = VhdlEntity(name=m.group(1), file=src)
                entity.generics = _parse_generics(_generic_block(text, m.end()))
                result.entities.append(entity)
                seen.add(m.group(1).lower())
            for m in _ARCH_RE.finditer(text):
                arch, arch_entity = m.group(1), m.group(2)
                for e in result.entities:
                    if e.name.lower() != arch_entity.lower():
                        continue
                    if arch not in e.architectures:
                        e.architectures.append(arch)
        else:
            for m in _MODULE_RE.finditer(text):
                mod = VerilogModule(name=m.group(1), file=src)
                if m.group(2):
                    mod.parameters = [
                        (pm.group(1), pm.group(2).strip())
                        for pm in _PARAM_RE.finditer(m.group(2))
                    ]
                result.modules.append(mod)
    return result


def render_inspection(inspection: Inspection) -> str:
    """Render the inspection the way an agent should read it: units that
    can be synthesized, plus what is ambiguous (ask the user)."""
    lines: list[str] = []
    for e in inspection.entities:
        lines.append(f"VHDL entity {e.name} ({e.file})")
        archs = ", ".join(e.architectures) or "(none found)"
        lines.append(f"  architectures: {archs}")
        if e.generics:
            for name, gtype, default in e.generics:
                d = f" := {default}" if default else ""
                lines.append(f"  generic: {name} : {gtype}{d}")
    for m in inspection.modules:
        lines.append(f"Verilog module {m.name} ({m.file})")
        if m.parameters:
            for name, default in m.parameters:
                lines.append(f"  parameter: {name} = {default}")
    if not lines:
        lines.append("No synthesizable units found in the given sources.")
    lines.append("")
    lines.append("Notes:")
    notes: list[str] = []
    for e in inspection.entities:
        if not e.architectures:
            notes.append(
                f"{e.name}: no architecture found — pick another top or "
                "add the architecture file"
            )
        elif len(e.architectures) > 1:
            archs = ", ".join(e.architectures)
            notes.append(
                f"{e.name}: {len(e.architectures)} architectures "
                f"({archs}) — ask which one to synthesize"
            )
    both = {e.name.lower() for e in inspection.entities} & {
        m.name.lower() for m in inspection.modules
    }
    if both:
        names = ", ".join(sorted(both))
        notes.append(
            f"declared as both VHDL and Verilog: {names} — ask which "
            "language the top is"
        )
    if notes:
        lines.extend(f"- {n}" for n in notes)
    else:
        lines.append("- no ambiguities")
    if inspection.errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"- {e}" for e in inspection.errors)
    return "\n".join(lines)
