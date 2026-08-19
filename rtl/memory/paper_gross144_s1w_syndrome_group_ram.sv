// Packed detector syndrome: 13 time slices x 18 disjoint four-check groups.
module paper_gross144_s1w_syndrome_group_ram #(
    parameter integer DEPTH = 234
) (
    input  logic clk,
    input  logic read_en,
    input  logic [7:0] read_addr,
    output logic [3:0] read_data,
    input  logic write_en,
    input  logic [7:0] write_addr,
    input  logic [3:0] write_data
);
`ifndef MITTEN_SIM
    wire [15:0] dpb_read;
    wire [13:0] read_address = {2'b00, read_addr, 4'b0000};
    wire [13:0] write_address = {2'b00, write_addr, 4'b0000} | 14'h0003;
    DPB #(
        // The controller presents the current verify group address directly
        // while the replay response is registered. Pipeline mode adds an
        // extra native-DPB delay on the board and leaves the previous group
        // on DOA at the final response; bypass mode matches that tag timing.
        .READ_MODE0(1'b0), .READ_MODE1(1'b0),
        .WRITE_MODE0(2'b00), .WRITE_MODE1(2'b01),
        .BIT_WIDTH_0(16), .BIT_WIDTH_1(16)
    ) u_dpb (
        .DOA(dpb_read), .DOB(), .DIA(16'd0), .DIB({12'd0, write_data}),
        .BLKSELA(3'b000), .BLKSELB(3'b000),
        .ADA(read_address), .ADB(write_address),
        .WREA(1'b0), .WREB(write_en && write_addr < DEPTH),
        .CLKA(clk), .CLKB(clk), .CEA(read_en && read_addr < DEPTH),
        .CEB(write_en && write_addr < DEPTH), .OCEA(1'b1), .OCEB(1'b0),
        .RESETA(1'b0), .RESETB(1'b0)
    );
    always_comb read_data = dpb_read[3:0];
`else
    logic [3:0] memory [0:DEPTH-1];
    always_ff @(posedge clk) begin
        if (read_en && read_addr < DEPTH)
            read_data <= memory[read_addr];
        if (write_en && write_addr < DEPTH)
            memory[write_addr] <= write_data;
    end
`endif
endmodule
