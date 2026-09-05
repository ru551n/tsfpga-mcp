# tsfpga-mcp project-mode e2e fixture. Not part of any real project.

from __future__ import annotations

import os
from pathlib import Path

from tsfpga.module import BaseModule, get_modules
from tsfpga.yosys.project import YosysNetlistBuild


class Module(BaseModule):
    def get_build_projects(self) -> list[YosysNetlistBuild]:
        """
        One netlist build project for 'counter.vhd', so
        tsfpga_project_list_builds/tsfpga_project_build have something real
        to drive. Yosys/GHDL locations are taken from the same
        TSFPGA_MCP_* env vars the ad-hoc tsfpga_synthesize tool uses, so
        the fixture works wherever tsfpga-mcp's own e2e tests already work.
        """
        modules = get_modules(
            modules_folder=self.path.parent, names_include={self.name}
        )
        ghdl_plugin_path = os.environ.get("TSFPGA_MCP_GHDL_PLUGIN")
        ghdl_prefix = os.environ.get("TSFPGA_MCP_GHDL_PREFIX")

        return [
            YosysNetlistBuild(
                name="counter",
                modules=modules,
                top="counter",
                ghdl_plugin_path=Path(ghdl_plugin_path) if ghdl_plugin_path else None,
                ghdl_prefix=Path(ghdl_prefix) if ghdl_prefix else None,
                defined_at=Path(__file__),
            )
        ]
