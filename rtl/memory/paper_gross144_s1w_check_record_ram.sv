// One-read/one-write store for all 936 compressed six-bit S1W check records.
//
// A record is min1[5:0], min2[5:0], argmin[5:0], signs[34:0] = 53 bits.
// Four native 16-bit Gowin DPBs hold the width slices at depth 1024. The
// four-lane controller prefetches four independent records serially while the
// current group executes, so this compact store never stalls the datapath.
module paper_gross144_s1w_check_record_ram #(
    parameter integer ADDR_WIDTH = 10,
    parameter integer RECORD_WIDTH = 53,
    parameter integer DEPTH = 936
) (
    input  logic clk,
    input  logic rst,
    input  logic read_en,
    input  logic [ADDR_WIDTH-1:0] read_addr,
    output logic read_valid,
    output logic [RECORD_WIDTH-1:0] read_data,
    input  logic write_en,
    input  logic [ADDR_WIDTH-1:0] write_addr,
    input  logic [RECORD_WIDTH-1:0] write_data
);
    localparam integer SLICES = (RECORD_WIDTH + 15) / 16;

`ifndef MITTEN_SIM
    wire [15:0] slice_read [0:SLICES-1];
    wire [SLICES*16-1:0] packed_read;
    wire [63:0] padded_write = {{(64-RECORD_WIDTH){1'b0}}, write_data};
    wire [13:0] read_address = {read_addr, 4'b0000};
    wire [13:0] write_address = {write_addr, 4'b0000} | 14'h0003;
    genvar slice;
    generate
        for (slice = 0; slice < SLICES; slice = slice + 1) begin : g_slice
            assign packed_read[slice*16 +: 16] = slice_read[slice];
            DPB #(
                // The decoder's read_valid is the one-cycle contract used by
                // the other native RAM wrappers.  Pipeline mode adds another
                // output-register stage, so the controller consumes the
                // previous record on hardware even though simulation still
                // sees the behavioral array value.  Bypass mode makes DOA
                // line up with that existing read_valid contract.
                .READ_MODE0(1'b0), .READ_MODE1(1'b0),
                .WRITE_MODE0(2'b01), .WRITE_MODE1(2'b01),
                .BIT_WIDTH_0(16), .BIT_WIDTH_1(16)
            ) u_dpb (
                .DOA(slice_read[slice]), .DOB(), .DIA(16'd0),
                .DIB(padded_write[slice*16 +: 16]),
                .BLKSELA(3'b000), .BLKSELB(3'b000),
                .ADA(read_address),
                .ADB(write_address),
                .WREA(1'b0), .WREB(write_en && write_addr < DEPTH),
                .CLKA(clk), .CLKB(clk), .CEA(read_en && read_addr < DEPTH),
                .CEB(write_en && write_addr < DEPTH),
                .OCEA(1'b1), .OCEB(1'b0),
                .RESETA(rst), .RESETB(rst)
            );
        end
    endgenerate
    always_comb begin
        read_data = packed_read[RECORD_WIDTH-1:0];
    end
`else
    logic [RECORD_WIDTH-1:0] memory [0:DEPTH-1];
    always_ff @(posedge clk)
        if (read_en && read_addr < DEPTH)
            read_data <= memory[read_addr];
    always_ff @(posedge clk)
        if (write_en && write_addr < DEPTH)
            memory[write_addr] <= write_data;
`endif

    always_ff @(posedge clk) begin
        if (rst)
            read_valid <= 1'b0;
        else
            read_valid <= read_en && read_addr < DEPTH;
    end
endmodule
