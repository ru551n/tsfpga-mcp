"""Error/critical-warning triage for a failed top-level (Vivado) build.

Vivado build logs are long and mix real failures with a lot of benign
informational/warning noise (place & route progress, timing-driven
optimization iterations, ...), so a blind tail can bury or entirely
scroll past the actual cause. Vivado's own diagnostic lines always start
with its fixed severity tokens ``ERROR:``/``CRITICAL WARNING:`` (as
opposed to lowercase "error" appearing incidentally in narrative text),
so surfacing exactly those lines, plus a little context, finds the real
cause reliably without the false positives a generic substring search
would pick up.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_VIVADO_DIAGNOSTIC_RE = re.compile(r"^\s*(ERROR|CRITICAL WARNING):")


def _context_lines(
    lines: Sequence[str], indices: Sequence[int], context: int
) -> list[str]:
    keep: set[int] = set()
    for idx in indices:
        keep.update(range(max(0, idx - context), min(len(lines), idx + context + 1)))
    result: list[str] = []
    previous: int | None = None
    for idx in sorted(keep):
        if previous is not None and idx != previous + 1:
            result.append("...")
        result.append(lines[idx])
        previous = idx
    return result


def build_diagnostics(
    output: str, context: int = 2, max_lines: int = 120
) -> str | None:
    """Lines around every Vivado ERROR/CRITICAL WARNING, in order.

    Returns None when the output contains no such line at all (e.g. the
    failure was in this server's own subprocess plumbing, or a netlist
    build's GHDL/Yosys output, neither of which uses Vivado's severity
    convention), so the caller can fall back to a plain tail.
    """
    lines = output.splitlines()
    idxs = [i for i, line in enumerate(lines) if _VIVADO_DIAGNOSTIC_RE.match(line)]
    if not idxs:
        return None

    selected = _context_lines(lines, idxs, context)
    truncated = len(selected) > max_lines
    if truncated:
        selected = selected[-max_lines:]

    error_count = sum(1 for i in idxs if lines[i].lstrip().startswith("ERROR:"))
    warning_count = len(idxs) - error_count
    header = f"{error_count} ERROR(s), {warning_count} CRITICAL WARNING(s) found:"
    body = "\n".join(selected)
    if truncated:
        body = f"… [truncated: showing last {max_lines} of the matched lines]\n{body}"
    return f"{header}\n{body}"
