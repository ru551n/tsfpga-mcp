library ieee;
use ieee.std_logic_1164.all;

--  Top instantiating "leaf" from a sibling library via
--  "library <name>; entity <name>.<entity>", the same pattern used by
--  tsfpga's per-module-folder library convention (tsfpga.module.get_modules()).
library leaf_lib;

entity libtop is
  port (
    a : in std_logic;
    y : out std_logic
  );
end entity libtop;

architecture rtl of libtop is
begin
  u_leaf : entity leaf_lib.leaf
    port map (
      a => a,
      y => y
    );
end architecture rtl;
