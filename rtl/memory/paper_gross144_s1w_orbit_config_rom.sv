// Per-orbit fixed priors, bank colour, and logical-pattern ID.
module paper_gross144_s1w_orbit_config_rom #(
    parameter integer DEPTH = 122,
    parameter integer WORD_WIDTH = 60,
    parameter IMAGE_FILE = ""
) (
    input  logic clk,
    input  logic read_en,
    input  logic [6:0] read_addr,
    output logic [WORD_WIDTH-1:0] read_data
);
    (* ram_style = "block" *) logic [WORD_WIDTH-1:0] memory [0:DEPTH-1];
    initial begin
        if (IMAGE_FILE != "")
            $readmemb(IMAGE_FILE, memory);
    end
    always_ff @(posedge clk)
        if (read_en && read_addr < DEPTH)
            read_data <= memory[read_addr];
endmodule
