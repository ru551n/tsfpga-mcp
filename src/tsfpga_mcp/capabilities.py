"""Runtime capability probing of the installed yosys.

The server is not pinned to a yosys version: on first use it asks the
configured yosys binary which `synth*` passes it provides and caches the
result. ``tsfpga_status`` and ``tsfpga_targets`` report what this yosys
actually supports; synthesizing for a chip whose flow is missing fails
with an actionable message instead of a cryptic one.
"""

from __future__ import annotations

import asyncio
import re

_PASS_LINE = re.compile(r"^\s{4}([a-z_][a-z0-9_]*)\s", re.M)
_FLOW_RE = re.compile(r"^(synth|synth_[a-z0-9_]+)$")


class Capabilities:
    """Probed yosys capabilities (probed once, then cached)."""

    def __init__(self, yosys: str) -> None:
        self.yosys = yosys
        self.version: str = "unknown"
        self.flows: frozenset[str] = frozenset()
        self.probe_error: str | None = None
        self._event = asyncio.Event()
        self._started = False
        self._task: asyncio.Task[None] | None = None

    @property
    def ready(self) -> bool:
        return self._event.is_set()

    async def ensure(self) -> None:
        """Probe if not yet done; concurrent callers share one probe."""
        if self._event.is_set():
            return
        if not self._started:
            self._started = True
            self._task = asyncio.ensure_future(self._probe_once())
        await self._event.wait()

    async def _probe_once(self) -> None:
        try:
            version, flows = await _probe(self.yosys)
            self.version = version
            self.flows = frozenset(flows)
        except (OSError, TimeoutError) as exc:
            self.version = "unavailable"
            self.probe_error = f"cannot run yosys ({self.yosys}): {exc}"
        finally:
            self._event.set()

    def has_flow(self, flow: str) -> bool:
        return flow in self.flows


async def _probe(yosys: str) -> tuple[str, list[str]]:
    out = await _run(yosys, ["-V"])
    lines = out.decode(errors="replace").strip().splitlines()
    version = lines[0] if lines else "unknown"
    help_out = await _run(yosys, ["-p", "help"])
    passes = _PASS_LINE.findall(help_out.decode(errors="replace"))
    flows = sorted(p for p in passes if _FLOW_RE.match(p))
    return version, flows


async def _run(argv0: str, args: list[str], timeout: float = 10.0) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        argv0,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"{argv0} timed out") from None
    return out


def render_targets(capabilities: Capabilities) -> str:
    """Render the chip -> flow -> families table with availability marks."""
    from .synth import CHIPS

    lines = [
        f"Yosys {capabilities.version} (binary: {capabilities.yosys})",
        f"Synthesis flows available: "
        f"{', '.join(sorted(capabilities.flows)) or '(none found)'}",
        "",
        "Chip targets (chip -> yosys flow, known families):",
    ]
    for name in sorted(CHIPS):
        spec = CHIPS[name]
        marker = "ok " if capabilities.has_flow(spec.flow) else "NO "
        fams = f", families: {', '.join(spec.families)}" if spec.families else ""
        lines.append(f"  [{marker}] {name:10} -> {spec.flow}{fams}")
        lines.append(f"           {spec.description}")
    return "\n".join(lines)
