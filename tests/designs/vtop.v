// Verilog top instantiating the VHDL vsub (mixed-language e2e test).
module vtop #(
  parameter WIDTH = 4
)(
  input clk,
  input [WIDTH-1:0] d,
  output y
);
  wire s;
  vsub u (
    .a(d[0]),
    .b(d[1]),
    .y(s)
  );
  assign y = s;
endmodule