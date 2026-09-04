// 2-input AND gate; instantiated (unbound) from the VHDL top wrapper.vhd
// (mixed-language, VHDL top -> Verilog submodule).
module vand (
  input a,
  input b,
  output y
);
  assign y = a & b;
endmodule
