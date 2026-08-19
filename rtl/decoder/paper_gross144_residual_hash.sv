// Exact-safe 32-bit residual-syndrome replay gate.
//
// The compiler supplies one equivariant column-base word per variable orbit.
// A posterior hard-sign transition xors the translated column into the
// current correction syndrome hash. The target hash is loaded directly from
// detector bits. `possible_zero` is never an acceptance certificate: the
// outer controller must perform a complete exact syndrome replay whenever it
// is asserted. Thus hash collisions affect latency only, never correctness.
module paper_gross144_residual_hash #(
    parameter integer LANES = 4,
    parameter integer SLOTS = 4,
    parameter integer ORBIT_WIDTH = 7,
    parameter integer HASH_WIDTH = 6
) (
    input  logic clk,
    input  logic rst,
    input  logic clear_target,
    input  logic clear_correction,
    input  logic target_load_valid,
    input  logic target_load_bit,
    input  logic [HASH_WIDTH-1:0] target_load_word,
    input  logic emit_valid,
    input  logic [LANES*SLOTS-1:0] emit_masks,
    input  logic [LANES*SLOTS-1:0] emit_hard_sign_flips,
    input  logic [LANES*SLOTS*HASH_WIDTH-1:0] emit_column_words,
    output logic [HASH_WIDTH-1:0] target_hash,
    output logic [HASH_WIDTH-1:0] correction_hash,
    output logic [HASH_WIDTH-1:0] residual_hash,
    output logic possible_zero
);
    integer bit_index;
    logic [HASH_WIDTH-1:0] emit_delta;

    always_comb begin
        emit_delta = '0;
        for (bit_index = 0; bit_index < LANES*SLOTS; bit_index = bit_index + 1)
            if (emit_masks[bit_index] && emit_hard_sign_flips[bit_index])
                emit_delta = emit_delta ^
                             emit_column_words[bit_index*HASH_WIDTH +: HASH_WIDTH];
        residual_hash = target_hash ^ correction_hash;
        possible_zero = (residual_hash == '0);
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            target_hash <= '0;
            correction_hash <= '0;
        end else begin
            if (clear_target)
                target_hash <= '0;
            else if (target_load_valid && target_load_bit)
                target_hash <= target_hash ^ target_load_word;
            if (clear_correction)
                correction_hash <= '0;
            else if (emit_valid)
                correction_hash <= correction_hash ^ emit_delta;
        end
    end
endmodule
