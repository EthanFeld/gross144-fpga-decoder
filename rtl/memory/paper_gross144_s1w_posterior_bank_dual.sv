// Two-port posterior store for paired Paper Gross144 S1 checks.
//
// The posterior is signed 11-bit data. The native 16-bit DPB byte-enable
// packing was board-sensitive in the four-lane image, so use explicit low and
// high byte planes instead. Each plane uses two 2K x 8 DPBs, giving two
// independent read/write ports without relying on 16-bit byte enables.
module paper_gross144_s1w_posterior_bank_dual #(
    parameter integer ADDR_WIDTH = 12,
    parameter integer DATA_WIDTH = 11,
    parameter integer DEPTH = 2196
) (
    input logic clk,
    input logic rst,
    input logic port0_read_en,
    input logic port0_write_en,
    input logic [ADDR_WIDTH-1:0] port0_addr,
    input logic [ADDR_WIDTH-1:0] port0_write_addr,
    input logic signed [DATA_WIDTH-1:0] port0_write_data,
    output logic signed [DATA_WIDTH-1:0] port0_read_data,
    input logic port1_read_en,
    input logic port1_write_en,
    input logic [ADDR_WIDTH-1:0] port1_addr,
    input logic [ADDR_WIDTH-1:0] port1_write_addr,
    input logic signed [DATA_WIDTH-1:0] port1_write_data,
    output logic signed [DATA_WIDTH-1:0] port1_read_data,
    output logic write_conflict,
    output logic port0_write_commit,
    output logic port1_write_commit
);
    assign write_conflict = port0_write_en && port1_write_en &&
                            (port0_write_addr == port1_write_addr);

`ifndef MITTEN_SIM
    // Use the native 16-bit DPB organization used by the original working
    // posterior store.  One DPB provides two independent ports; three
    // 1024-word segments cover all 2,196 logical entries.  In 16-bit mode
    // Gowin selects the low/high bytes with address bits 0/1, so a logical
    // row is addressed at row<<4 and writes use row<<4 | 3.
    localparam integer WORDS_PER_DPB = 1024;
    localparam integer SEGMENT_COUNT = (DEPTH + WORDS_PER_DPB - 1) / WORDS_PER_DPB;
    wire port0_selected [0:SEGMENT_COUNT-1];
    wire port1_selected [0:SEGMENT_COUNT-1];
    wire port0_write_selected [0:SEGMENT_COUNT-1];
    wire port1_write_selected [0:SEGMENT_COUNT-1];
    wire [15:0] dpb_doa [0:SEGMENT_COUNT-1];
    wire [15:0] dpb_dob [0:SEGMENT_COUNT-1];
    wire [15:0] port0_dpb_data = {{(16-DATA_WIDTH){1'b0}}, port0_write_data};
    wire [15:0] port1_dpb_data = {{(16-DATA_WIDTH){1'b0}}, port1_write_data};
    genvar segment;
    generate
        for (segment = 0; segment < SEGMENT_COUNT; segment = segment + 1) begin : g_dpb
            localparam integer BASE = segment * WORDS_PER_DPB;
            assign port0_selected[segment] =
                (port0_read_en && (port0_addr >= BASE) &&
                 (port0_addr < BASE + WORDS_PER_DPB)) ||
                (port0_write_en && (port0_write_addr >= BASE) &&
                 (port0_write_addr < BASE + WORDS_PER_DPB));
            assign port1_selected[segment] =
                (port1_read_en && (port1_addr >= BASE) &&
                 (port1_addr < BASE + WORDS_PER_DPB)) ||
                (port1_write_en && (port1_write_addr >= BASE) &&
                 (port1_write_addr < BASE + WORDS_PER_DPB));
            assign port0_write_selected[segment] =
                port0_write_en && (port0_write_addr >= BASE) &&
                (port0_write_addr < BASE + WORDS_PER_DPB) && !write_conflict;
            assign port1_write_selected[segment] =
                port1_write_en && (port1_write_addr >= BASE) &&
                (port1_write_addr < BASE + WORDS_PER_DPB) && !write_conflict;

            wire [13:0] port0_read_address = {port0_addr[9:0], 4'b0000};
            wire [13:0] port1_read_address = {port1_addr[9:0], 4'b0000};
            wire [13:0] port0_write_address =
                {port0_write_addr[9:0], 4'b0000} | 14'h0003;
            wire [13:0] port1_write_address =
                {port1_write_addr[9:0], 4'b0000} | 14'h0003;

            DPB #(
                .READ_MODE0(1'b0), .READ_MODE1(1'b0),
                // Both ports are used for either gather reads or emit/init
                // writes.  Gowin's 01 mode is the working write-through
                // configuration; 00 silently loses a subset of writes on
                // the routed GW2AR DPB even though RTL simulation updates.
                .WRITE_MODE0(2'b01), .WRITE_MODE1(2'b01),
                .BIT_WIDTH_0(16), .BIT_WIDTH_1(16)
            ) u_dpb (
                .DOA(dpb_doa[segment]), .DOB(dpb_dob[segment]),
                .DIA(port0_dpb_data), .DIB(port1_dpb_data),
                .BLKSELA(3'b000), .BLKSELB(3'b000),
                .ADA(port0_write_selected[segment] ? port0_write_address : port0_read_address),
                .ADB(port1_write_selected[segment] ? port1_write_address : port1_read_address),
                .WREA(port0_write_selected[segment]),
                .WREB(port1_write_selected[segment]),
                .CLKA(clk), .CLKB(clk),
                .CEA(port0_selected[segment]), .CEB(port1_selected[segment]),
                .OCEA(1'b1), .OCEB(1'b1),
                .RESETA(rst), .RESETB(rst)
            );
        end
    endgenerate

    // Export the exact post-gating write strobes used to drive the native
    // DPB.  This is also useful for board qualification: the controller's
    // request strobe alone does not prove that a segmented physical bank
    // accepted the write.
    always_comb begin
        port0_write_commit = 1'b0;
        port1_write_commit = 1'b0;
        for (integer commit_segment = 0; commit_segment < SEGMENT_COUNT;
             commit_segment = commit_segment + 1) begin
            port0_write_commit |= port0_write_selected[commit_segment];
            port1_write_commit |= port1_write_selected[commit_segment];
        end
    end
    logic [1:0] port0_segment_q, port1_segment_q;
    always_ff @(posedge clk) begin
        if (rst) begin
            // The first S1W gather addresses are in the upper 2K segment.
            // Starting there avoids a board-only stale segment select during
            // the first native-DPB read; subsequent reads update this latch
            // from the actual request address.
            port0_segment_q <= 2'd2;
            port1_segment_q <= 2'd2;
        end else begin
            if (port0_read_en)
                port0_segment_q <= port0_addr[11:10];
            if (port1_read_en)
                port1_segment_q <= port1_addr[11:10];
        end
    end

    always_comb begin
        port0_read_data = '0;
        port1_read_data = '0;
        for (integer read_segment = 0; read_segment < SEGMENT_COUNT;
             read_segment = read_segment + 1) begin
            if (port0_segment_q == read_segment[1:0])
                port0_read_data = $signed(dpb_doa[read_segment][DATA_WIDTH-1:0]);
            if (port1_segment_q == read_segment[1:0])
                port1_read_data = $signed(dpb_dob[read_segment][DATA_WIDTH-1:0]);
        end
    end
`else
    logic signed [DATA_WIDTH-1:0] memory [0:DEPTH-1];
    always_ff @(posedge clk) begin
        if (port0_write_en && !write_conflict && port0_write_addr < DEPTH)
            memory[port0_write_addr] <= port0_write_data;
        else if (port0_read_en && port0_addr < DEPTH)
            port0_read_data <= memory[port0_addr];
        if (port1_write_en && !write_conflict && port1_write_addr < DEPTH)
            memory[port1_write_addr] <= port1_write_data;
        else if (port1_read_en && port1_addr < DEPTH)
            port1_read_data <= memory[port1_addr];
    end
    always_comb begin
        port0_write_commit = port0_write_en && !write_conflict &&
                             (port0_write_addr < DEPTH);
        port1_write_commit = port1_write_en && !write_conflict &&
                             (port1_write_addr < DEPTH);
    end
`endif
endmodule
