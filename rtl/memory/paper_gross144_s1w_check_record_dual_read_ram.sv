// Two-bank compressed check-record store with one read port per bank.
//
// Relay check/scatter needs the previous record and the freshly reduced
// record concurrently.  Keeping each bank on its own single-read DPB makes
// that access pattern deterministic without duplicating the record bits.
// Writes are directed to either bank and may overlap either read.
module paper_gross144_s1w_check_record_dual_read_ram #(
    parameter integer ADDR_WIDTH = 10,
    parameter integer RECORD_WIDTH = 53,
    parameter integer DEPTH = 936
) (
    input  logic clk,
    input  logic rst,
    input  logic old_read_en,
    input  logic old_bank_select,
    input  logic [ADDR_WIDTH-1:0] old_read_addr,
    output logic old_read_valid,
    output logic [RECORD_WIDTH-1:0] old_read_data,
    input  logic new_read_en,
    input  logic new_bank_select,
    input  logic [ADDR_WIDTH-1:0] new_read_addr,
    output logic new_read_valid,
    output logic [RECORD_WIDTH-1:0] new_read_data,
    input  logic write_en,
    input  logic write_bank,
    input  logic [ADDR_WIDTH-1:0] write_addr,
    input  logic [RECORD_WIDTH-1:0] write_data
);
    logic old_bank_reg, new_bank_reg;
    logic phys0_valid, phys1_valid;
    logic [RECORD_WIDTH-1:0] phys0_data, phys1_data;

    paper_gross144_s1w_check_record_ram #(
        .ADDR_WIDTH(ADDR_WIDTH), .RECORD_WIDTH(RECORD_WIDTH), .DEPTH(DEPTH)
    ) u_old_bank (
        .clk(clk), .rst(rst),
        .read_en((old_read_en && !old_bank_select) ||
                 (new_read_en && !new_bank_select)),
        .read_addr(old_bank_select ? new_read_addr : old_read_addr),
        .read_valid(phys0_valid), .read_data(phys0_data),
        .write_en(write_en && !write_bank), .write_addr(write_addr),
        .write_data(write_data)
    );

    paper_gross144_s1w_check_record_ram #(
        .ADDR_WIDTH(ADDR_WIDTH), .RECORD_WIDTH(RECORD_WIDTH), .DEPTH(DEPTH)
    ) u_new_bank (
        .clk(clk), .rst(rst),
        .read_en((old_read_en && old_bank_select) ||
                 (new_read_en && new_bank_select)),
        .read_addr(old_bank_select ? old_read_addr : new_read_addr),
        .read_valid(phys1_valid), .read_data(phys1_data),
        .write_en(write_en && write_bank), .write_addr(write_addr),
        .write_data(write_data)
    );

    // The bank tags follow the synchronous DPB read by one clock. The
    // controller guarantees old_bank_select != new_bank_select whenever
    // both ports are enabled.
    always_ff @(posedge clk) begin
        if (rst) begin
            old_bank_reg <= 1'b0;
            new_bank_reg <= 1'b1;
        end else begin
            if (old_read_en)
                old_bank_reg <= old_bank_select;
            if (new_read_en)
                new_bank_reg <= new_bank_select;
        end
    end

    always_comb begin
        old_read_valid = old_bank_reg ? phys1_valid : phys0_valid;
        old_read_data = old_bank_reg ? phys1_data : phys0_data;
        new_read_valid = new_bank_reg ? phys1_valid : phys0_valid;
        new_read_data = new_bank_reg ? phys1_data : phys0_data;
    end
endmodule
