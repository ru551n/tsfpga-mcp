library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

--  Up-counter with generic width; used as the top level of the
--  tsfpga-mcp project-mode e2e fixture's netlist build project.
entity counter is
  generic (
    WIDTH : positive := 4
  );
  port (
    clk : in std_logic;
    outc : out std_logic_vector(WIDTH - 1 downto 0)
  );
end entity counter;

architecture rtl of counter is
  signal c : unsigned(WIDTH - 1 downto 0) := (others => '0');
begin
  process (clk)
  begin
    if rising_edge(clk) then
      c <= c + 1;
    end if;
  end process;
  outc <= std_logic_vector(c);
end architecture rtl;
