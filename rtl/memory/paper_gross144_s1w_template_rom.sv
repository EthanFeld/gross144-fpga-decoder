// Packed immutable four-slot template store for the Paper Gross144 decoder.
// A synchronous descriptor-plus-hash row maps to native Gowin block-RAM.
// slices.  One read supplies all lane descriptors and residual-hash columns.
module paper_gross144_s1w_template_rom #(
    parameter integer ADDR_WIDTH = 7,
    parameter integer DEPTH = 117,
    parameter integer HASH_WIDTH = 6,
    parameter integer WORD_WIDTH = 4 * (23 + HASH_WIDTH),
    parameter IMAGE_FILE = "",
    parameter integer SLOT_IMAGE_MODE = 0,
    parameter SLOT0_FILE = "",
    parameter SLOT1_FILE = "",
    parameter SLOT2_FILE = "",
    parameter SLOT3_FILE = ""
) (
    input  logic clk,
    input  logic read_en,
    input  logic [ADDR_WIDTH-1:0] read_addr,
    output logic [WORD_WIDTH-1:0] read_data
);
    // A single wide row can be repacked by Gowin's RAM inference. Production
    // uses four independently loaded (23-bit descriptor + HASH_WIDTH-bit hash)
    // words. Legacy/full-row mode remains for RTL tests and non-Gowin targets.
    generate
        if (SLOT_IMAGE_MODE != 0) begin : g_slot_rom
            (* ram_style = "block" *) logic [22+HASH_WIDTH:0] slot0 [0:DEPTH-1];
            (* ram_style = "block" *) logic [22+HASH_WIDTH:0] slot1 [0:DEPTH-1];
            (* ram_style = "block" *) logic [22+HASH_WIDTH:0] slot2 [0:DEPTH-1];
            (* ram_style = "block" *) logic [22+HASH_WIDTH:0] slot3 [0:DEPTH-1];
            initial begin
                $readmemb(SLOT0_FILE, slot0);
                $readmemb(SLOT1_FILE, slot1);
                $readmemb(SLOT2_FILE, slot2);
                $readmemb(SLOT3_FILE, slot3);
            end
            always_ff @(posedge clk) begin
                if (read_en && read_addr < DEPTH) begin
                    read_data[0*23 +: 23] <= slot0[read_addr][22:0];
                    read_data[1*23 +: 23] <= slot1[read_addr][22:0];
                    read_data[2*23 +: 23] <= slot2[read_addr][22:0];
                    read_data[3*23 +: 23] <= slot3[read_addr][22:0];
                    read_data[4*23 + 0*HASH_WIDTH +: HASH_WIDTH] <= slot0[read_addr][22+HASH_WIDTH:23];
                    read_data[4*23 + 1*HASH_WIDTH +: HASH_WIDTH] <= slot1[read_addr][22+HASH_WIDTH:23];
                    read_data[4*23 + 2*HASH_WIDTH +: HASH_WIDTH] <= slot2[read_addr][22+HASH_WIDTH:23];
                    read_data[4*23 + 3*HASH_WIDTH +: HASH_WIDTH] <= slot3[read_addr][22+HASH_WIDTH:23];
                end
            end
        end else begin : g_full_row_rom
            (* ram_style = "distributed" *) logic [WORD_WIDTH-1:0] memory [0:DEPTH-1];
            initial begin
                if (IMAGE_FILE != "")
                    $readmemb(IMAGE_FILE, memory);
            end
            always_ff @(posedge clk)
                if (read_en && read_addr < DEPTH)
                    read_data <= memory[read_addr];
        end
    endgenerate
endmodule
