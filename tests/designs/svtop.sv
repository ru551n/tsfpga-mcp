// SystemVerilog module for the -sv read flag.
module svtop #(
  parameter W = 3
)(
  input logic clk,
  input logic [W-1:0] d,
  output logic y
);
  logic [W-1:0] r;
  always_ff @(posedge clk) begin
    r <= d;
  end
  assign y = &r;
endmodule