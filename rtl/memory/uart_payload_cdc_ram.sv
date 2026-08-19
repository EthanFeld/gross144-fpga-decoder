// One-frame dual-clock UART payload buffer.
//
// The UART parser writes each received byte on clk27.  It toggles the request
// only after CRC acceptance, so the decoder can synchronously read the stable
// frame on its independent core clock.  One Gowin BSRAM replaces two 936-bit
// fabric shift/register banks in the production endpoint.
module uart_payload_cdc_ram #(
    parameter integer ADDR_WIDTH = 7,
    parameter integer DEPTH = 117
) (
    input  logic write_clk,
    input  logic write_rst,
    input  logic write_en,
    input  logic [ADDR_WIDTH-1:0] write_addr,
    input  logic [7:0] write_data,
    input  logic read_clk,
    input  logic read_rst,
    input  logic read_en,
    input  logic [ADDR_WIDTH-1:0] read_addr,
    output logic [7:0] read_data
);
    // Small transport snapshot intentionally stays in fabric. Earlier DPB
    // versions simulated correctly but were board-sensitive: Gowin's native
    // 16-bit byte-enable/address semantics made payload writes unreliable.
    // This is only 936 bits; stable after CRC/request toggle, so an async read
    // is safe across the two-clock handoff and removes one RAM bubble.
    logic [7:0] memory [0:DEPTH-1];
    always_ff @(posedge write_clk) begin
        if (write_en && write_addr < DEPTH)
            memory[write_addr] <= write_data;
    end
    always_comb begin
        if (read_addr < DEPTH)
            read_data = memory[read_addr];
        else
            read_data = 8'd0;
    end
endmodule
