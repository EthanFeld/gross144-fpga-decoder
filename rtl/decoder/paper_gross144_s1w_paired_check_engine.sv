// Lockstep pair of exact four-bank Paper Gross144 S1W check engines.
//
// The paired controller feeds spatial checks c and c+36.  Their template
// beats have equal shape, while the compiler proof guarantees their physical
// posterior addresses are distinct.  This wrapper therefore exposes one
// gather/emit/done handshake for two independent min-sum records.  The outer
// controller can map each four-lane output onto one port of every dual-port
// posterior bank, halving check-update time without changing decoder maths.
module paper_gross144_s1w_paired_check_engine #(
    parameter integer MAX_DEGREE = 35,
    parameter integer POSTERIOR_WIDTH = 11,
    parameter integer BANKS = 4,
    parameter integer ORBIT_WIDTH = 7,
    parameter integer MESSAGE_MAGNITUDE_BITS = 5,
    parameter integer CORRECTION_SHIFT = 3,
    parameter integer RUNTIME_CONFIG = 0,
    parameter integer FAST_PATH = 0
) (
    input  logic clk,
    input  logic rst,
    input  logic batch_start_valid,
    output logic batch_start_ready,
    input  logic [2:0] config_correction_shift,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] config_message_max,

    input  logic [5:0] lane0_degree,
    input  logic lane0_syndrome_bit,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] lane0_old_min1,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] lane0_old_min2,
    input  logic [5:0] lane0_old_argmin,
    input  logic scatter_mode,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] lane0_scatter_old_min1,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] lane0_scatter_old_min2,
    input  logic [5:0] lane0_scatter_old_argmin,
    input  logic [MAX_DEGREE-1:0] lane0_scatter_old_signs,
    input  logic [5:0] lane1_degree,
    input  logic lane1_syndrome_bit,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] lane1_old_min1,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] lane1_old_min2,
    input  logic [5:0] lane1_old_argmin,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] lane1_scatter_old_min1,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] lane1_scatter_old_min2,
    input  logic [5:0] lane1_scatter_old_argmin,
    input  logic [MAX_DEGREE-1:0] lane1_scatter_old_signs,

    input  logic [BANKS-1:0] lane0_gather_valid,
    input  logic [BANKS*6-1:0] lane0_gather_edge_indices,
    input  logic signed [BANKS*POSTERIOR_WIDTH-1:0] lane0_gather_posteriors,
    input  logic [BANKS-1:0] lane0_gather_old_signs,
    input  logic [BANKS*ORBIT_WIDTH-1:0] lane0_gather_orbits,
    input  logic [BANKS*7-1:0] lane0_gather_anchors,
    input  logic [BANKS-1:0] lane1_gather_valid,
    input  logic [BANKS*6-1:0] lane1_gather_edge_indices,
    input  logic signed [BANKS*POSTERIOR_WIDTH-1:0] lane1_gather_posteriors,
    input  logic [BANKS-1:0] lane1_gather_old_signs,
    input  logic [BANKS*ORBIT_WIDTH-1:0] lane1_gather_orbits,
    input  logic [BANKS*7-1:0] lane1_gather_anchors,
    output logic batch_gather_ready,

    output logic batch_emit_valid,
    input  logic batch_emit_ready,
    input  logic [BANKS-1:0] lane0_emit_old_signs,
    input  logic [BANKS-1:0] lane0_external_emit_valid_mask,
    input  logic [BANKS*6-1:0] lane0_external_emit_edge_indices,
    input  logic [BANKS*ORBIT_WIDTH-1:0] lane0_external_emit_orbits,
    input  logic [BANKS*7-1:0] lane0_external_emit_anchors,
    output logic [BANKS-1:0] lane0_out_valid_mask,
    output logic [BANKS*6-1:0] lane0_out_edge_indices,
    output logic [BANKS*ORBIT_WIDTH-1:0] lane0_out_orbits,
    output logic [BANKS*7-1:0] lane0_out_anchors,
    output logic signed [BANKS*POSTERIOR_WIDTH-1:0] lane0_out_posteriors,
    output logic [BANKS-1:0] lane0_out_new_signs,
    output logic [BANKS-1:0] lane0_out_hard_sign_flips,
    input  logic [BANKS-1:0] lane1_emit_old_signs,
    input  logic [BANKS-1:0] lane1_external_emit_valid_mask,
    input  logic [BANKS*6-1:0] lane1_external_emit_edge_indices,
    input  logic [BANKS*ORBIT_WIDTH-1:0] lane1_external_emit_orbits,
    input  logic [BANKS*7-1:0] lane1_external_emit_anchors,
    output logic [BANKS-1:0] lane1_out_valid_mask,
    output logic [BANKS*6-1:0] lane1_out_edge_indices,
    output logic [BANKS*ORBIT_WIDTH-1:0] lane1_out_orbits,
    output logic [BANKS*7-1:0] lane1_out_anchors,
    output logic signed [BANKS*POSTERIOR_WIDTH-1:0] lane1_out_posteriors,
    output logic [BANKS-1:0] lane1_out_new_signs,
    output logic [BANKS-1:0] lane1_out_hard_sign_flips,
    output logic batch_emit_last,

    output logic batch_done_valid,
    input  logic batch_done_ready,
    output logic [MESSAGE_MAGNITUDE_BITS-1:0] lane0_new_min1,
    output logic [MESSAGE_MAGNITUDE_BITS-1:0] lane0_new_min2,
    output logic [5:0] lane0_new_argmin,
    output logic [MESSAGE_MAGNITUDE_BITS-1:0] lane1_new_min1,
    output logic [MESSAGE_MAGNITUDE_BITS-1:0] lane1_new_min2,
    output logic [5:0] lane1_new_argmin,
    output logic image_error,
    output logic lockstep_error,
    output logic busy
);
    logic lane0_start_ready, lane1_start_ready;
    logic lane0_gather_ready, lane1_gather_ready;
    logic lane0_out_valid, lane1_out_valid;
    logic lane0_done_valid, lane1_done_valid;
    logic lane0_out_last, lane1_out_last;
    logic lane0_image_error, lane1_image_error;
    logic lane0_busy, lane1_busy;

    component_check_four_bank_engine #(
        .MAX_DEGREE(MAX_DEGREE), .POSTERIOR_WIDTH(POSTERIOR_WIDTH), .BANKS(BANKS),
        .ORBIT_WIDTH(ORBIT_WIDTH), .MESSAGE_MAGNITUDE_BITS(MESSAGE_MAGNITUDE_BITS),
        .CORRECTION_SHIFT(CORRECTION_SHIFT), .RUNTIME_CONFIG(RUNTIME_CONFIG),
        .EXTERNAL_EMIT_DESCRIPTOR(1), .FAST_PATH(FAST_PATH)
    ) u_lane0 (
        .clk(clk), .rst(rst), .start_valid(batch_start_valid), .start_ready(lane0_start_ready),
        .start_degree(lane0_degree), .syndrome_bit(lane0_syndrome_bit),
        .old_min1(lane0_old_min1), .old_min2(lane0_old_min2), .old_argmin(lane0_old_argmin),
        .scatter_mode(scatter_mode),
        .scatter_old_min1(lane0_scatter_old_min1),
        .scatter_old_min2(lane0_scatter_old_min2),
        .scatter_old_argmin(lane0_scatter_old_argmin),
        .scatter_old_signs(lane0_scatter_old_signs),
        .config_correction_shift(config_correction_shift),
        .config_message_max(config_message_max),
        .gather_valid(lane0_gather_valid), .gather_ready(lane0_gather_ready),
        .gather_edge_indices(lane0_gather_edge_indices), .gather_posteriors(lane0_gather_posteriors),
        .gather_old_signs(lane0_gather_old_signs), .gather_orbits(lane0_gather_orbits),
        .gather_anchors(lane0_gather_anchors), .out_valid(lane0_out_valid),
        .out_ready(batch_emit_ready && lane0_out_valid && lane1_out_valid),
        .emit_old_signs(lane0_emit_old_signs),
        .external_emit_valid_mask(lane0_external_emit_valid_mask),
        .external_emit_edge_indices(lane0_external_emit_edge_indices),
        .external_emit_orbits(lane0_external_emit_orbits),
        .external_emit_anchors(lane0_external_emit_anchors),
        .out_valid_mask(lane0_out_valid_mask),
        .out_edge_indices(lane0_out_edge_indices), .out_orbits(lane0_out_orbits),
        .out_anchors(lane0_out_anchors), .out_posteriors(lane0_out_posteriors),
        .out_new_signs(lane0_out_new_signs),
        .out_hard_sign_flips(lane0_out_hard_sign_flips), .out_last(lane0_out_last),
        .done_valid(lane0_done_valid),
        .done_ready(batch_done_ready && lane0_done_valid && lane1_done_valid),
        .new_min1(lane0_new_min1), .new_min2(lane0_new_min2), .new_argmin(lane0_new_argmin),
        .image_error(lane0_image_error), .busy(lane0_busy)
    );

    component_check_four_bank_engine #(
        .MAX_DEGREE(MAX_DEGREE), .POSTERIOR_WIDTH(POSTERIOR_WIDTH), .BANKS(BANKS),
        .ORBIT_WIDTH(ORBIT_WIDTH), .MESSAGE_MAGNITUDE_BITS(MESSAGE_MAGNITUDE_BITS),
        .CORRECTION_SHIFT(CORRECTION_SHIFT), .RUNTIME_CONFIG(RUNTIME_CONFIG),
        .EXTERNAL_EMIT_DESCRIPTOR(1), .FAST_PATH(FAST_PATH)
    ) u_lane1 (
        .clk(clk), .rst(rst), .start_valid(batch_start_valid), .start_ready(lane1_start_ready),
        .start_degree(lane1_degree), .syndrome_bit(lane1_syndrome_bit),
        .old_min1(lane1_old_min1), .old_min2(lane1_old_min2), .old_argmin(lane1_old_argmin),
        .scatter_mode(scatter_mode),
        .scatter_old_min1(lane1_scatter_old_min1),
        .scatter_old_min2(lane1_scatter_old_min2),
        .scatter_old_argmin(lane1_scatter_old_argmin),
        .scatter_old_signs(lane1_scatter_old_signs),
        .config_correction_shift(config_correction_shift),
        .config_message_max(config_message_max),
        .gather_valid(lane1_gather_valid), .gather_ready(lane1_gather_ready),
        .gather_edge_indices(lane1_gather_edge_indices), .gather_posteriors(lane1_gather_posteriors),
        .gather_old_signs(lane1_gather_old_signs), .gather_orbits(lane1_gather_orbits),
        .gather_anchors(lane1_gather_anchors), .out_valid(lane1_out_valid),
        .out_ready(batch_emit_ready && lane0_out_valid && lane1_out_valid),
        .emit_old_signs(lane1_emit_old_signs),
        .external_emit_valid_mask(lane1_external_emit_valid_mask),
        .external_emit_edge_indices(lane1_external_emit_edge_indices),
        .external_emit_orbits(lane1_external_emit_orbits),
        .external_emit_anchors(lane1_external_emit_anchors),
        .out_valid_mask(lane1_out_valid_mask),
        .out_edge_indices(lane1_out_edge_indices), .out_orbits(lane1_out_orbits),
        .out_anchors(lane1_out_anchors), .out_posteriors(lane1_out_posteriors),
        .out_new_signs(lane1_out_new_signs),
        .out_hard_sign_flips(lane1_out_hard_sign_flips), .out_last(lane1_out_last),
        .done_valid(lane1_done_valid),
        .done_ready(batch_done_ready && lane0_done_valid && lane1_done_valid),
        .new_min1(lane1_new_min1), .new_min2(lane1_new_min2), .new_argmin(lane1_new_argmin),
        .image_error(lane1_image_error), .busy(lane1_busy)
    );

    always_comb begin
        batch_start_ready = lane0_start_ready && lane1_start_ready;
        batch_gather_ready = lane0_gather_ready && lane1_gather_ready;
        batch_emit_valid = lane0_out_valid && lane1_out_valid;
        batch_emit_last = lane0_out_last && lane1_out_last;
        batch_done_valid = lane0_done_valid && lane1_done_valid;
        // Pair shape is invariant under c -> c+36. If either state machine
        // diverges, forbid partial externally-visible progress immediately.
        lockstep_error = (lane0_gather_ready != lane1_gather_ready) ||
                         (lane0_out_valid != lane1_out_valid) ||
                         (lane0_out_last != lane1_out_last) ||
                         (lane0_done_valid != lane1_done_valid);
        image_error = lane0_image_error || lane1_image_error || lockstep_error;
        busy = lane0_busy || lane1_busy;
    end
endmodule
