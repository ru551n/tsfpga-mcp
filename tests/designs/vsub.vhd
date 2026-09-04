library ieee;
use ieee.std_logic_1164.all;

--  Small 2-input AND; instantiated from the Verilog top (mixed-language).
entity vsub is
  port (
    a : in std_logic;
    b : in std_logic;
    y : out std_logic
  );
end entity vsub;

architecture rtl of vsub is
begin
  y <= a and b;
end architecture rtl;