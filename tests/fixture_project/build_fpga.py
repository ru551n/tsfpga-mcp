#!/usr/bin/env python
"""tsfpga-mcp project-mode e2e fixture's build script.

Mirrors tsfpga's own tsfpga/examples/build_fpga.py, but with a single
throwaway module ('counter') instead of the real example modules. Driven
by tsfpga_project_list_builds/tsfpga_project_build the same way a human
would run it from a terminal.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tsfpga.build_project_list import BuildProjectList, get_build_projects
from tsfpga.examples.build_fpga_utils import arguments, setup_and_run
from tsfpga.module import get_modules

THIS_DIR = Path(__file__).parent.resolve()


def main() -> None:
    args = arguments(default_temp_dir=THIS_DIR / "tsfpga_mcp_out")
    modules = get_modules(modules_folder=THIS_DIR / "modules")
    project_list = BuildProjectList(
        projects=get_build_projects(
            modules=modules,
            project_filters=args.project_filters,
            include_netlist_not_full_builds=args.netlist_builds,
        ),
        no_color=args.no_color,
    )

    sys.exit(
        setup_and_run(
            modules=modules,
            project_list=project_list,
            args=args,
            collect_artifacts_function=None,
        )
    )


if __name__ == "__main__":
    main()
