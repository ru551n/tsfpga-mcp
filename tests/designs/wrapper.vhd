library ieee;
use ieee.std_logic_1164.all;

--  VHDL top instantiating an unbound component bound to the Verilog
--  module vand.v (mixed-language, VHDL top -> Verilog submodule).
entity wrapper is
  port (
    a : in std_logic;
    b : in std_logic;
    y : out std_logic
  );
end entity wrapper;

architecture rtl of wrapper is
  component vand is
    port (
      a : in std_logic;
      b : in std_logic;
      y : out std_logic
    );
  end component vand;
begin
  u : vand
    port map (
      a => a,
      b => b,
      y => y
    );
end architecture rtl;
