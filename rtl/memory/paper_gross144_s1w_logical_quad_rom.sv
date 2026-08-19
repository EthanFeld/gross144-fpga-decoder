// Four logical masks for one compact posterior word from each physical bank.
// The exporter folds pattern ID, orbit colour, and local coordinate into one
// address, replacing four asynchronous 504-way lookups with one BRAM read.
module paper_gross144_s1w_logical_quad_rom #(
    parameter integer DEPTH = 504,
    parameter integer WORD_WIDTH = 48,
    parameter IMAGE_FILE = "",
    parameter IMAGE_FILE0 = "",
    parameter IMAGE_FILE1 = "",
    parameter IMAGE_FILE2 = "",
    parameter IMAGE_FILE3 = ""
) (
    input  logic clk,
    input  logic read0_en,
    input  logic [8:0] read0_addr,
    output logic [WORD_WIDTH-1:0] read0_data,
    input  logic read1_en,
    input  logic [8:0] read1_addr,
    output logic [WORD_WIDTH-1:0] read1_data
);
    (* ram_style = "block" *) logic [WORD_WIDTH-1:0] memory [0:DEPTH-1];
    (* ram_style = "block" *) logic [11:0] memory0 [0:DEPTH-1];
    (* ram_style = "block" *) logic [11:0] memory1 [0:DEPTH-1];
    (* ram_style = "block" *) logic [11:0] memory2 [0:DEPTH-1];
    (* ram_style = "block" *) logic [11:0] memory3 [0:DEPTH-1];
    initial begin
        if (IMAGE_FILE0 != "") begin
            // Four native-width images avoid vendor-specific bit ordering
            // when a 48-bit inferred ROM is split across Gowin DPBs.
            $readmemb(IMAGE_FILE0, memory0);
            $readmemb(IMAGE_FILE1, memory1);
            $readmemb(IMAGE_FILE2, memory2);
            $readmemb(IMAGE_FILE3, memory3);
        end else if (IMAGE_FILE != "") begin
            $readmemb(IMAGE_FILE, memory);
        end
    end
    // Two synchronous reads map to the two native ports of the same Gowin
    // block memory; no ROM replication is required.
    always_ff @(posedge clk) begin
        if (read0_en && read0_addr < DEPTH) begin
            if (IMAGE_FILE0 != "")
                read0_data <= {memory3[read0_addr], memory2[read0_addr],
                               memory1[read0_addr], memory0[read0_addr]};
            else
                read0_data <= memory[read0_addr];
        end
        if (read1_en && read1_addr < DEPTH) begin
            if (IMAGE_FILE0 != "")
                read1_data <= {memory3[read1_addr], memory2[read1_addr],
                               memory1[read1_addr], memory0[read1_addr]};
            else
                read1_data <= memory[read1_addr];
        end
    end
endmodule
