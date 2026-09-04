library ieee;
use ieee.std_logic_1164.all;

--  Leaf entity, meant to be staged into a library named "leaf_lib" (not
--  the top's own library) to exercise cross-library instantiation.
entity leaf is
  port (
    a : in std_logic;
    y : out std_logic
  );
end entity leaf;

architecture rtl of leaf is
begin
  y <= not a;
end architecture rtl;
