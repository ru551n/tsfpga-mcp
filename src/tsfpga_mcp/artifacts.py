"""Bitstream/artifact path discovery for a completed top-level (Vivado) build.

tsfpga's own build flow prints ``Building Vivado project in <project_path>,
placing artifacts in <output_path>`` exactly when a build ran through
implementation (see ``VivadoProject.build`` in ``tsfpga/vivado/
project.py``) — never for synthesis-only builds, which produce no
bitstream. Parsing that line out of the build's own stdout, rather than
re-deriving tsfpga's output-path convention independently, stays correct
even for a project whose build script passes a custom ``--output-path``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ARTIFACTS_LINE_RE = re.compile(
    r"^Building Vivado project in .+, placing artifacts in (?P<output_path>.+)$",
    re.MULTILINE,
)
# tsfpga always writes '.bit'/'.bin'; '.xsa' only exists for projects that
# opt into exporting a hardware platform (not all do).
_ARTIFACT_SUFFIXES = (".bit", ".bin", ".xsa")


@dataclass(frozen=True)
class ProjectArtifacts:
    project: str
    output_path: Path
    files: list[Path]


def find_artifacts(build_output: str) -> list[ProjectArtifacts]:
    """Artifact files for every project a build's stdout mentions building.

    Only matches projects that reached the artifact-placing stage (i.e.
    ran through implementation, not synth_only); reports whichever of
    '.bit'/'.bin'/'.xsa' actually exist on disk (a failed or interrupted
    build may leave some or all of them missing).
    """
    results = []
    seen: set[Path] = set()
    for match in _ARTIFACTS_LINE_RE.finditer(build_output):
        output_path = Path(match.group("output_path").strip())
        if output_path in seen:
            continue
        seen.add(output_path)
        project = output_path.name
        files = [
            output_path / f"{project}{suffix}"
            for suffix in _ARTIFACT_SUFFIXES
            if (output_path / f"{project}{suffix}").is_file()
        ]
        results.append(
            ProjectArtifacts(project=project, output_path=output_path, files=files)
        )
    return results


def render_artifacts(artifacts: list[ProjectArtifacts]) -> str:
    if not artifacts:
        return ""
    lines = ["Artifacts:"]
    for a in artifacts:
        if not a.files:
            lines.append(
                f"  {a.project}: none found in {a.output_path} (build may "
                "have failed before writing them)"
            )
            continue
        lines.extend(f"  {f}" for f in a.files)
    return "\n".join(lines)
