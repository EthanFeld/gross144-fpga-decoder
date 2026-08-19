// Four-bank streamed normalized min-sum CHECK_VAR primitive.
//
// This is the bandwidth-scaled companion to component_check_stream_engine.
// The compiler supplies four-bank conflict-free template beats, so each
// gather/emit handshake contains up to four unrelated check edges.  Edge
// indices remain explicit: compressed min1/min2/argmin/sign state therefore
// keeps exactly the same meaning as the scalar FPGA decoder even though the
// component RAM is accessed in bank order.
module component_check_four_bank_engine #(
    parameter integer MAX_DEGREE = 41,
    parameter integer POSTERIOR_WIDTH = 11,
    parameter integer BANKS = 4,
    parameter integer ORBIT_WIDTH = 7,
    // Defaults retain the legacy component-image record format. Paper S1W
    // selects five bits and shift three (alpha = 7/8).
    parameter integer MESSAGE_MAGNITUDE_BITS = 4,
    parameter integer CORRECTION_SHIFT = 2,
    // The qualifying Paper controller reuses one six-bit engine across
    // profiles with different normalization/clipping settings. Legacy users
    // retain compile-time constants and may leave the config ports open.
    parameter integer RUNTIME_CONFIG = 0,
    parameter integer EXTERNAL_EMIT_DESCRIPTOR = 0,
    // Fast production images receive compiler-proven conflict-free, unique
    // template beats.  They can remove the defensive duplicate/error cone
    // from the gather-state decision; the default engine retains it.
    parameter integer FAST_PATH = 0,
    // Relay reference builds may use signed 12-bit posteriors. Legacy S1W
    // keeps its signed 11-bit default and therefore its existing timing.
    parameter integer POSTERIOR_MAX = (1 << (POSTERIOR_WIDTH-1)) - 1,
    parameter integer POSTERIOR_MIN = -(1 << (POSTERIOR_WIDTH-1))
) (
    input  logic clk,
    input  logic rst,

    input  logic start_valid,
    output logic start_ready,
    input  logic [5:0] start_degree,
    input  logic syndrome_bit,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] old_min1,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] old_min2,
    input  logic [5:0] old_argmin,
    input  logic scatter_mode,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] scatter_old_min1,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] scatter_old_min2,
    input  logic [5:0] scatter_old_argmin,
    input  logic [MAX_DEGREE-1:0] scatter_old_signs,
    input  logic [2:0] config_correction_shift,
    input  logic [MESSAGE_MAGNITUDE_BITS-1:0] config_message_max,

    // Each gather beat is a compiler-proven conflict-free posterior-RAM
    // access set.  `gather_edge_indices` identify positions in the stable
    // per-check compressed-sign record, not global variable IDs.
    input  logic [BANKS-1:0] gather_valid,
    output logic gather_ready,
    input  logic [BANKS*6-1:0] gather_edge_indices,
    input  logic signed [BANKS*POSTERIOR_WIDTH-1:0] gather_posteriors,
    input  logic [BANKS-1:0] gather_old_signs,
    // Opaque topology context follows its sparse source beat through the
    // engine. It avoids a four-port 35-entry edge-to-orbit mux at emit.
    input  logic [BANKS*ORBIT_WIDTH-1:0] gather_orbits,
    input  logic [BANKS*7-1:0] gather_anchors,

    // The output beat uses the same banked order received during gather.
    // `emit_old_signs` must contain the old sign for each asserted output
    // lane and remain stable until the output handshake completes.
    output logic out_valid,
    input  logic out_ready,
    input  logic [BANKS-1:0] emit_old_signs,
    input  logic [BANKS-1:0] external_emit_valid_mask,
    input  logic [BANKS*6-1:0] external_emit_edge_indices,
    input  logic [BANKS*ORBIT_WIDTH-1:0] external_emit_orbits,
    input  logic [BANKS*7-1:0] external_emit_anchors,
    output logic [BANKS-1:0] out_valid_mask,
    output logic [BANKS*6-1:0] out_edge_indices,
    output logic [BANKS*ORBIT_WIDTH-1:0] out_orbits,
    output logic [BANKS*7-1:0] out_anchors,
    output logic signed [BANKS*POSTERIOR_WIDTH-1:0] out_posteriors,
    output logic [BANKS-1:0] out_new_signs,
    output logic [BANKS-1:0] out_hard_sign_flips,
    output logic out_last,

    output logic done_valid,
    input  logic done_ready,
    output logic [MESSAGE_MAGNITUDE_BITS-1:0] new_min1,
    output logic [MESSAGE_MAGNITUDE_BITS-1:0] new_min2,
    output logic [5:0] new_argmin,
    output logic image_error,
    output logic busy
);
    localparam logic [2:0] S_IDLE   = 3'd0;
    localparam logic [2:0] S_GATHER = 3'd1;
    localparam logic [2:0] S_EMIT   = 3'd2;
    localparam logic [2:0] S_DONE   = 3'd3;
    localparam logic [2:0] S_REDUCE = 3'd4;
    localparam logic [2:0] S_PREPARE = 3'd5;
    localparam logic [2:0] S_FINALIZE = 3'd6;
    localparam integer MESSAGE_MAX = (1 << MESSAGE_MAGNITUDE_BITS) - 1;
    localparam integer MAX_BEATS = (MAX_DEGREE + BANKS - 1) / BANKS;

    logic [2:0] state;
    logic [5:0] degree_reg, gathered_count_reg, gathered_beat_count_reg,
                emitted_beat_reg;
    logic syndrome_reg, image_error_reg;
    logic [MESSAGE_MAGNITUDE_BITS-1:0] old_min1_reg, old_min2_reg;
    logic [5:0] old_argmin_reg;
    logic scatter_mode_reg;
    logic [MESSAGE_MAGNITUDE_BITS-1:0] scatter_old_min1_reg, scatter_old_min2_reg;
    logic [5:0] scatter_old_argmin_reg;
    logic [MAX_DEGREE-1:0] scatter_old_signs_reg;
    logic [2:0] correction_shift_reg;
    logic [MESSAGE_MAGNITUDE_BITS-1:0] message_max_reg;
    logic [MAX_DEGREE-1:0] gathered_edges_reg;
    // The certified emit stream repeats gather beat/slot order exactly.
    // Store one packed row per beat instead of indexing 35 individual words
    // by edge at emit; this removes four large asynchronous read muxes.
    logic signed [BANKS*POSTERIOR_WIDTH-1:0] posterior_beat_mem [0:MAX_BEATS-1];
    // Keep each gather mask with its edge order.  Flattening sparse beats can
    // make one output word straddle two bank-conflict-free source beats.
    logic [BANKS-1:0] emit_valid_mem [0:MAX_DEGREE-1];
    logic [5:0] emit_edge_mem [0:MAX_DEGREE-1][0:BANKS-1];
    logic [ORBIT_WIDTH-1:0] emit_orbit_mem [0:MAX_DEGREE-1][0:BANKS-1];
    logic [6:0] emit_anchor_mem [0:MAX_DEGREE-1][0:BANKS-1];

    logic [POSTERIOR_WIDTH-1:0] raw_min1_reg, raw_min2_reg;
    logic [5:0] reduce_argmin_reg;
    logic reduce_parity_reg;
    logic [MESSAGE_MAGNITUDE_BITS-1:0] new_min1_reg, new_min2_reg;
    logic [5:0] new_argmin_reg;

    // Break the controller-template-to-reducer path at a sparse-beat
    // boundary.  The controller has several template/RAM cycles between
    // beats, so this reduction phase is normally hidden by its next gather.
    logic [BANKS-1:0] staged_valid, staged_old_signs;
    logic [BANKS*6-1:0] staged_edge_indices;
    logic signed [BANKS*POSTERIOR_WIDTH-1:0] staged_posteriors;
    logic [BANKS*ORBIT_WIDTH-1:0] staged_orbits;
    logic [BANKS*7-1:0] staged_anchors;
    logic [BANKS-1:0] prepared_valid;
    logic signed [BANKS*POSTERIOR_WIDTH-1:0] prepared_posterior_row;
    logic [5:0] prepared_index [0:BANKS-1];
    logic signed [POSTERIOR_WIDTH-1:0] prepared_value [0:BANKS-1];
    logic signed [POSTERIOR_WIDTH-1:0] prepared_extrinsic [0:BANKS-1];
    logic [POSTERIOR_WIDTH-1:0] prepared_magnitude [0:BANKS-1];
    logic [ORBIT_WIDTH-1:0] prepared_orbit [0:BANKS-1];
    logic [6:0] prepared_anchor [0:BANKS-1];
    // A second beat register separates extrinsic/magnitude formation from the
    // four-way minimum tournament.  It preserves one-beat-per-clock gather
    // throughput while removing the old-message -> magnitude -> tournament
    // path that limited the routed design to roughly 31 MHz.
    logic [BANKS-1:0] fold_valid;
    logic signed [BANKS*POSTERIOR_WIDTH-1:0] fold_posterior_row;
    logic [5:0] fold_index [0:BANKS-1];
    logic signed [POSTERIOR_WIDTH-1:0] fold_value [0:BANKS-1];
    logic signed [POSTERIOR_WIDTH-1:0] fold_extrinsic [0:BANKS-1];
    logic [ORBIT_WIDTH-1:0] fold_orbit [0:BANKS-1];
    logic [6:0] fold_anchor [0:BANKS-1];
    // Top-two values for one banked beat are registered beside the prepared
    // posterior data. This removes the four-input tournament from the
    // accumulated-minimum feedback path without adding a pipeline cycle.
    logic candidate_valid_reg, candidate_has2_reg;
    logic [POSTERIOR_WIDTH-1:0] candidate_min1_reg, candidate_min2_reg;
    logic [5:0] candidate_argmin1_reg;
    logic candidate_valid, candidate_has2;
    logic [POSTERIOR_WIDTH-1:0] candidate_min1, candidate_min2;
    logic [5:0] candidate_argmin1;
    logic pair01_valid, pair01_has2, pair23_valid, pair23_has2;
    logic [POSTERIOR_WIDTH-1:0] pair01_min, pair01_second, pair23_min, pair23_second;
    logic [5:0] pair01_min_index, pair23_min_index;
    logic [BANKS-1:0] selected_emit_valid;
    logic [BANKS*6-1:0] selected_emit_edges;
    logic [BANKS*ORBIT_WIDTH-1:0] selected_emit_orbits;
    logic [BANKS*7-1:0] selected_emit_anchors;

    logic [5:0] gather_index [0:BANKS-1];
    logic signed [POSTERIOR_WIDTH-1:0] gather_value [0:BANKS-1];
    logic signed [POSTERIOR_WIDTH-1:0] gather_extrinsic [0:BANKS-1];
    logic [POSTERIOR_WIDTH-1:0] gather_magnitude [0:BANKS-1];
    logic signed [MESSAGE_MAGNITUDE_BITS:0] gather_old_message [0:BANKS-1];

    logic [MAX_DEGREE-1:0] next_gathered_edges;
    logic [5:0] next_gathered_count;
    logic [POSTERIOR_WIDTH-1:0] next_min1, next_min2;
    logic [5:0] next_argmin;
    logic next_parity, gather_error;

    integer gather_lane, reduce_lane, emit_lane, seq_reduce_lane, pack_lane,
            candidate_lane;

    always_comb begin
        prepared_posterior_row = '0;
        for (pack_lane = 0; pack_lane < BANKS; pack_lane = pack_lane + 1)
            if (prepared_valid[pack_lane])
                prepared_posterior_row[pack_lane*POSTERIOR_WIDTH +: POSTERIOR_WIDTH] =
                    prepared_value[pack_lane];
    end

    function automatic signed [POSTERIOR_WIDTH-1:0] saturate_posterior(
        input logic signed [POSTERIOR_WIDTH+1:0] source
    );
        begin
            if ($signed(source) > POSTERIOR_MAX)
                saturate_posterior = POSTERIOR_MAX;
            else if ($signed(source) < POSTERIOR_MIN)
                saturate_posterior = POSTERIOR_MIN;
            else
                saturate_posterior = source[POSTERIOR_WIDTH-1:0];
        end
    endfunction

    function automatic [POSTERIOR_WIDTH-1:0] magnitude_of(
        input logic signed [POSTERIOR_WIDTH-1:0] sample
    );
        begin
            magnitude_of = (sample < 0) ? -sample : sample;
        end
    endfunction

    function automatic [MESSAGE_MAGNITUDE_BITS-1:0] normalize_magnitude(
        input logic [POSTERIOR_WIDTH-1:0] raw,
        input logic [2:0] correction_shift,
        input logic [MESSAGE_MAGNITUDE_BITS-1:0] magnitude_max
    );
        logic [POSTERIOR_WIDTH-1:0] normalized;
        begin
            // Normalize the raw magnitude first, then clip into the record.
            // Clipping first is not bit-exact at S1W's saturation boundary.
            normalized = raw - (raw >> correction_shift);
            normalize_magnitude = (normalized > magnitude_max) ? magnitude_max :
                                  normalized[MESSAGE_MAGNITUDE_BITS-1:0];
        end
    endfunction

    function automatic signed [MESSAGE_MAGNITUDE_BITS:0] old_message_for_edge(
        input logic [5:0] index,
        input logic [5:0] argmin,
        input logic [MESSAGE_MAGNITUDE_BITS-1:0] min1,
        input logic [MESSAGE_MAGNITUDE_BITS-1:0] min2,
        input logic sign_bit
    );
        logic signed [MESSAGE_MAGNITUDE_BITS:0] message;
        begin
            message = $signed({1'b0, (index == argmin) ? min2 : min1});
            if (sign_bit && (message != 0))
                message = -message;
            old_message_for_edge = message;
        end
    endfunction

    function automatic [2:0] valid_lanes_before(
        input logic [BANKS-1:0] values,
        input integer position
    );
        integer count, index;
        begin
            count = 0;
            for (index = 0; index < BANKS; index = index + 1)
                if (index < position && values[index])
                    count = count + 1;
            valid_lanes_before = count[2:0];
        end
    endfunction

    // A stable min uses the edge position to break equal-magnitude ties.  The
    // four-bank reducer uses this primitive as a two-level tournament rather
    // than cascading every lane through the prior minimum in one cycle.
    function automatic min_key_less;
        input logic [POSTERIOR_WIDTH-1:0] magnitude_a;
        input logic [5:0] index_a;
        input logic [POSTERIOR_WIDTH-1:0] magnitude_b;
        input logic [5:0] index_b;
        begin
            min_key_less = (magnitude_a < magnitude_b) ||
                           ((magnitude_a == magnitude_b) && (index_a < index_b));
        end
    endfunction

    always @* begin : gather_unpack_comb
        logic [5:0] working_index;
        logic signed [POSTERIOR_WIDTH-1:0] working_value;
        logic signed [MESSAGE_MAGNITUDE_BITS:0] working_old_message;
        logic signed [POSTERIOR_WIDTH-1:0] working_extrinsic;
        for (gather_lane = 0; gather_lane < BANKS; gather_lane = gather_lane + 1) begin
            working_index = staged_edge_indices[gather_lane*6 +: 6];
            working_value = staged_posteriors[gather_lane*POSTERIOR_WIDTH +:
                                              POSTERIOR_WIDTH];
            working_old_message = old_message_for_edge(
                working_index, old_argmin_reg, old_min1_reg, old_min2_reg,
                staged_old_signs[gather_lane]
            );
            working_extrinsic = saturate_posterior($signed(working_value) - working_old_message);
            gather_index[gather_lane] = working_index;
            gather_value[gather_lane] = working_value;
            gather_old_message[gather_lane] = working_old_message;
            gather_extrinsic[gather_lane] = working_extrinsic;
            gather_magnitude[gather_lane] = magnitude_of(working_extrinsic);
        end
    end

    // Banked-beat top-two reducer. Use two explicit two-entry tournaments,
    // then one cross-pair tournament. The former insertion loop synthesized
    // as a serial four-entry chain: prepared_index -> min2 crossed the
    // critical path at ~30.5 ns on GW2AR-18. This balanced network keeps the
    // exact lexicographic magnitude/index tie rule while limiting the data
    // depth to two comparator levels. A second-minimum index is not retained:
    // it is never consumed, and equal second magnitudes are equivalent.
    always @* begin : pair_tournament_comb
        pair01_valid = prepared_valid[0] || prepared_valid[1];
        pair01_has2 = prepared_valid[0] && prepared_valid[1];
        pair01_min = '1;
        pair01_second = '1;
        pair01_min_index = '1;
        if (prepared_valid[0] &&
            (!prepared_valid[1] || min_key_less(
                prepared_magnitude[0], prepared_index[0],
                prepared_magnitude[1], prepared_index[1]))) begin
            pair01_min = prepared_magnitude[0];
            pair01_min_index = prepared_index[0];
            pair01_second = prepared_magnitude[1];
        end else if (prepared_valid[1]) begin
            pair01_min = prepared_magnitude[1];
            pair01_min_index = prepared_index[1];
            pair01_second = prepared_magnitude[0];
        end

        pair23_valid = prepared_valid[2] || prepared_valid[3];
        pair23_has2 = prepared_valid[2] && prepared_valid[3];
        pair23_min = '1;
        pair23_second = '1;
        pair23_min_index = '1;
        if (prepared_valid[2] &&
            (!prepared_valid[3] || min_key_less(
                prepared_magnitude[2], prepared_index[2],
                prepared_magnitude[3], prepared_index[3]))) begin
            pair23_min = prepared_magnitude[2];
            pair23_min_index = prepared_index[2];
            pair23_second = prepared_magnitude[3];
        end else if (prepared_valid[3]) begin
            pair23_min = prepared_magnitude[3];
            pair23_min_index = prepared_index[3];
            pair23_second = prepared_magnitude[2];
        end
    end

    always @* begin : candidate_comb
        logic select_pair01;

        candidate_valid = pair01_valid || pair23_valid;
        candidate_has2 = pair01_has2 || pair23_has2 ||
                         (pair01_valid && pair23_valid);
        candidate_min1 = '1;
        candidate_min2 = '1;
        candidate_argmin1 = '1;
        select_pair01 = pair01_valid &&
            (!pair23_valid || min_key_less(
                pair01_min, pair01_min_index,
                pair23_min, pair23_min_index));
        if (select_pair01) begin
            candidate_min1 = pair01_min;
            candidate_argmin1 = pair01_min_index;
            if (pair01_has2 &&
                (!pair23_valid || (pair01_second < pair23_min))) begin
                candidate_min2 = pair01_second;
            end else begin
                candidate_min2 = pair23_min;
            end
        end else if (pair23_valid) begin
            candidate_min1 = pair23_min;
            candidate_argmin1 = pair23_min_index;
            if (pair23_has2 &&
                (!pair01_valid || (pair23_second < pair01_min))) begin
                candidate_min2 = pair23_second;
            end else begin
                candidate_min2 = pair01_min;
            end
        end
    end

    always @* begin : reduce_comb
        logic [MAX_DEGREE-1:0] working_gathered_edges;
        logic [5:0] working_gathered_count;
        logic [POSTERIOR_WIDTH-1:0] working_min1, working_min2;
        logic [5:0] working_argmin;
        logic working_parity, working_error;
        logic working_has1, working_has2;

        working_gathered_edges = gathered_edges_reg;
        working_gathered_count = gathered_count_reg;
        working_min1 = raw_min1_reg;
        working_min2 = raw_min2_reg;
        working_argmin = reduce_argmin_reg;
        working_parity = reduce_parity_reg;
        working_error = 1'b0;
        working_has1 = (gathered_count_reg != 0);
        working_has2 = (gathered_count_reg > 1);
        if (state == S_GATHER && |fold_valid) begin
            for (reduce_lane = 0; reduce_lane < BANKS; reduce_lane = reduce_lane + 1) begin
                if (fold_valid[reduce_lane]) begin
                    // Keep the accumulator write data independent of the
                    // run-time degree.  An out-of-degree descriptor still
                    // raises the proof guard and terminates before emit, so
                    // the speculative accumulator update is unobservable.
                    // This prevents `degree_reg` from driving the CE/D cones
                    // of every gathered-edge and minimum register.
                    if (fold_index[reduce_lane] >= MAX_DEGREE)
                        working_error = 1'b1;
                    else begin
                        if ((fold_index[reduce_lane] >= degree_reg) ||
                            working_gathered_edges[fold_index[reduce_lane]])
                            working_error = 1'b1;
                        if (!working_gathered_edges[fold_index[reduce_lane]]) begin
                            working_gathered_edges[fold_index[reduce_lane]] = 1'b1;
                            working_gathered_count = working_gathered_count + 1'b1;
                            working_parity = working_parity ^
                                fold_extrinsic[reduce_lane][POSTERIOR_WIDTH-1];
                        end

                    end
                end
            end

            // Merge the registered beat top-two summary into the check-wide
            // accumulator.  The candidate pipeline preserves the original
            // lane-order tie behavior.  Keep the second-minimum magnitude
            // path independent of argmin: min2 has no stored index, so an
            // equal-magnitude tie only affects min1's argmin. This avoids
            // dragging reduce_argmin through the wide raw_min2 feedback mux.
            if (candidate_valid_reg) begin
                if (!working_has1) begin
                    working_min1 = candidate_min1_reg;
                    working_argmin = candidate_argmin1_reg;
                    working_has1 = 1'b1;
                    if (candidate_has2_reg) begin
                        working_min2 = candidate_min2_reg;
                        working_has2 = 1'b1;
                    end
                end else begin
                    // Magnitude-only top-two update. The <= case is
                    // intentional: two equal minima produce a valid min2
                    // even when the argmin tie selects the prior edge.
                    if (candidate_min1_reg <= working_min1) begin
                        working_min2 = working_min1;
                        working_has2 = 1'b1;
                        if ((candidate_min1_reg < working_min1) ||
                            (candidate_argmin1_reg < working_argmin)) begin
                            working_min1 = candidate_min1_reg;
                            working_argmin = candidate_argmin1_reg;
                        end
                    end else if (!working_has2 ||
                                 (candidate_min1_reg < working_min2)) begin
                        working_min2 = candidate_min1_reg;
                        working_has2 = 1'b1;
                    end
                end

                if (candidate_has2_reg &&
                    (!working_has2 || (candidate_min2_reg < working_min2))) begin
                    working_min2 = candidate_min2_reg;
                    working_has2 = 1'b1;
                end
            end
        end
        next_gathered_edges = working_gathered_edges;
        next_gathered_count = working_gathered_count;
        next_min1 = working_min1;
        next_min2 = working_min2;
        next_argmin = working_argmin;
        next_parity = working_parity;
        gather_error = working_error;
    end

    always @* begin
        logic [5:0] edge_index;
        logic signed [MESSAGE_MAGNITUDE_BITS:0] old_message, new_message;
        logic signed [POSTERIOR_WIDTH-1:0] extrinsic;
        logic signed [POSTERIOR_WIDTH+1:0] sum;
        logic signed [POSTERIOR_WIDTH-1:0] updated_posterior;
        logic [MESSAGE_MAGNITUDE_BITS-1:0] magnitude;
        logic sign_bit;

        edge_index = '0;
        old_message = '0;
        new_message = '0;
        extrinsic = '0;
        sum = '0;
        magnitude = '0;
        sign_bit = 1'b0;
        updated_posterior = '0;

        // A following independent check may be accepted on the final emit
        // handshake. This removes the DONE/IDLE bubbles between controller
        // groups without changing the current output beat.
        start_ready = (state == S_IDLE) ||
                      ((state == S_EMIT) && !image_error_reg && out_ready &&
                       ((emitted_beat_reg + 1'b1) >= gathered_beat_count_reg));
        gather_ready = (state == S_GATHER) && !image_error_reg;
        out_valid = (state == S_EMIT) && !image_error_reg;
        done_valid = (state == S_DONE);
        busy = (state != S_IDLE);
        image_error = image_error_reg;
        new_min1 = image_error_reg ? '0 : new_min1_reg;
        new_min2 = image_error_reg ? '0 : new_min2_reg;
        new_argmin = image_error_reg ? 6'd0 : new_argmin_reg;
        out_valid_mask = '0;
        out_edge_indices = '0;
        out_orbits = '0;
        out_anchors = '0;
        out_posteriors = '0;
        out_new_signs = '0;
        out_hard_sign_flips = '0;
        selected_emit_valid = external_emit_valid_mask;
        selected_emit_edges = external_emit_edge_indices;
        selected_emit_orbits = external_emit_orbits;
        selected_emit_anchors = external_emit_anchors;
        if (!EXTERNAL_EMIT_DESCRIPTOR) begin
            selected_emit_valid = emit_valid_mem[emitted_beat_reg];
            for (emit_lane = 0; emit_lane < BANKS; emit_lane = emit_lane + 1) begin
                selected_emit_edges[emit_lane*6 +: 6] =
                    emit_edge_mem[emitted_beat_reg][emit_lane];
                selected_emit_orbits[emit_lane*ORBIT_WIDTH +: ORBIT_WIDTH] =
                    emit_orbit_mem[emitted_beat_reg][emit_lane];
                selected_emit_anchors[emit_lane*7 +: 7] =
                    emit_anchor_mem[emitted_beat_reg][emit_lane];
            end
        end
        out_last = (state == S_EMIT) &&
                   ((emitted_beat_reg + 1'b1) >= gathered_beat_count_reg);

        if (state == S_EMIT && !image_error_reg) begin
            for (emit_lane = 0; emit_lane < BANKS; emit_lane = emit_lane + 1) begin
                if (selected_emit_valid[emit_lane]) begin
                    edge_index = selected_emit_edges[emit_lane*6 +: 6];
                    sum = $signed(posterior_beat_mem[emitted_beat_reg]
                        [emit_lane*POSTERIOR_WIDTH +: POSTERIOR_WIDTH]);
                    if (scatter_mode_reg) begin
                        // Flooding Relay scatter: P_new = P_old - old c2v
                        // + newly computed c2v. The posterior bank is held
                        // stable during the preceding check phase.
                        old_message = old_message_for_edge(
                            edge_index, scatter_old_argmin_reg,
                            scatter_old_min1_reg, scatter_old_min2_reg,
                            scatter_old_signs_reg[edge_index]
                        );
                        new_message = old_message_for_edge(
                            edge_index, old_argmin_reg, old_min1_reg, old_min2_reg,
                            emit_old_signs[emit_lane]
                        );
                        sign_bit = emit_old_signs[emit_lane];
                    end else begin
                        old_message = old_message_for_edge(
                            edge_index, old_argmin_reg, old_min1_reg, old_min2_reg,
                            emit_old_signs[emit_lane]
                        );
                        extrinsic = saturate_posterior(
                            $signed(posterior_beat_mem[emitted_beat_reg]
                                [emit_lane*POSTERIOR_WIDTH +: POSTERIOR_WIDTH]) -
                            old_message
                        );
                        magnitude = (edge_index == reduce_argmin_reg) ? new_min2_reg : new_min1_reg;
                        sign_bit = syndrome_reg ^ reduce_parity_reg ^ (extrinsic < 0);
                        new_message = $signed({1'b0, magnitude});
                        if (sign_bit && (new_message != 0))
                            new_message = -new_message;
                    end
                    sum = sum - old_message;
                    sum = sum + new_message;
                    updated_posterior = saturate_posterior(sum);
                    out_valid_mask[emit_lane] = 1'b1;
                    out_edge_indices[emit_lane*6 +: 6] = edge_index;
                    out_orbits[emit_lane*ORBIT_WIDTH +: ORBIT_WIDTH] =
                        selected_emit_orbits[emit_lane*ORBIT_WIDTH +: ORBIT_WIDTH];
                    out_anchors[emit_lane*7 +: 7] =
                        selected_emit_anchors[emit_lane*7 +: 7];
                    out_posteriors[emit_lane*POSTERIOR_WIDTH +: POSTERIOR_WIDTH] =
                        updated_posterior;
                    out_new_signs[emit_lane] = sign_bit;
                    out_hard_sign_flips[emit_lane] =
                        posterior_beat_mem[emitted_beat_reg]
                            [emit_lane*POSTERIOR_WIDTH + POSTERIOR_WIDTH-1] ^
                        updated_posterior[POSTERIOR_WIDTH-1];
                end
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            degree_reg <= '0;
            gathered_count_reg <= '0;
            gathered_beat_count_reg <= '0;
            emitted_beat_reg <= '0;
            syndrome_reg <= 1'b0;
            image_error_reg <= 1'b0;
            old_min1_reg <= '0;
            old_min2_reg <= '0;
            old_argmin_reg <= '0;
            scatter_mode_reg <= 1'b0;
            scatter_old_min1_reg <= '0;
            scatter_old_min2_reg <= '0;
            scatter_old_argmin_reg <= '0;
            scatter_old_signs_reg <= '0;
            correction_shift_reg <= CORRECTION_SHIFT[2:0];
            message_max_reg <= MESSAGE_MAX[MESSAGE_MAGNITUDE_BITS-1:0];
            gathered_edges_reg <= '0;
            raw_min1_reg <= '0;
            raw_min2_reg <= '0;
            reduce_argmin_reg <= '0;
            reduce_parity_reg <= 1'b0;
            new_min1_reg <= '0;
            new_min2_reg <= '0;
            new_argmin_reg <= '0;
            staged_valid <= '0;
            staged_old_signs <= '0;
            staged_edge_indices <= '0;
            staged_posteriors <= '0;
            staged_orbits <= '0;
            staged_anchors <= '0;
            prepared_valid <= '0;
            fold_valid <= '0;
            fold_posterior_row <= '0;
            candidate_valid_reg <= 1'b0;
            candidate_has2_reg <= 1'b0;
            candidate_min1_reg <= '0;
            candidate_min2_reg <= '0;
            candidate_argmin1_reg <= '0;
            for (seq_reduce_lane = 0; seq_reduce_lane < BANKS; seq_reduce_lane = seq_reduce_lane + 1) begin
                prepared_index[seq_reduce_lane] <= '0;
                prepared_value[seq_reduce_lane] <= '0;
                prepared_extrinsic[seq_reduce_lane] <= '0;
                prepared_magnitude[seq_reduce_lane] <= '0;
                prepared_orbit[seq_reduce_lane] <= '0;
                prepared_anchor[seq_reduce_lane] <= '0;
                fold_index[seq_reduce_lane] <= '0;
                fold_value[seq_reduce_lane] <= '0;
                fold_extrinsic[seq_reduce_lane] <= '0;
                fold_orbit[seq_reduce_lane] <= '0;
                fold_anchor[seq_reduce_lane] <= '0;
            end
        end else begin
            case (state)
                S_IDLE: if (start_valid && start_ready) begin
                    degree_reg <= start_degree;
                    syndrome_reg <= syndrome_bit;
                    old_min1_reg <= old_min1;
                    old_min2_reg <= old_min2;
                    old_argmin_reg <= old_argmin;
                    scatter_mode_reg <= scatter_mode;
                    scatter_old_min1_reg <= scatter_old_min1;
                    scatter_old_min2_reg <= scatter_old_min2;
                    scatter_old_argmin_reg <= scatter_old_argmin;
                    scatter_old_signs_reg <= scatter_old_signs;
                    correction_shift_reg <= RUNTIME_CONFIG ?
                        config_correction_shift : CORRECTION_SHIFT[2:0];
                    message_max_reg <= RUNTIME_CONFIG ?
                        config_message_max : MESSAGE_MAX[MESSAGE_MAGNITUDE_BITS-1:0];
                    image_error_reg <= (start_degree < 2) || (start_degree > MAX_DEGREE);
                    gathered_count_reg <= '0;
                    gathered_beat_count_reg <= '0;
                    emitted_beat_reg <= '0;
                    gathered_edges_reg <= '0;
                    raw_min1_reg <= {POSTERIOR_WIDTH{1'b1}};
                    raw_min2_reg <= {POSTERIOR_WIDTH{1'b1}};
                    reduce_argmin_reg <= '0;
                    reduce_parity_reg <= 1'b0;
                    new_min1_reg <= '0;
                    new_min2_reg <= '0;
                    new_argmin_reg <= '0;
                    staged_valid <= '0;
                    prepared_valid <= '0;
                    fold_valid <= '0;
                    candidate_valid_reg <= 1'b0;
                    candidate_has2_reg <= 1'b0;
                    state <= ((start_degree < 2) || (start_degree > MAX_DEGREE)) ? S_DONE : S_GATHER;
                end

                // Streaming three-stage reducer.  Capture, prepare, and
                // reduce operate on three distinct beats each clock, so the
                // old critical paths remain registered while gather_ready can
                // accept a new banked beat every cycle.
                S_GATHER: begin
                    // Stage 3: fold the registered tournament beat into the
                    // check accumulator and retain it for later scatter.
                    if (|fold_valid) begin
                        // Always retain the accepted pipeline row. Structural
                        // validation below can still terminate the image, but
                        // it must not sit on the row-memory write-enable path.
                        // Invalid images never emit, so the speculative write
                        // is architecturally invisible and removes the last
                        // near-critical degree/duplicate-check -> RAM CE cone.
                        posterior_beat_mem[gathered_beat_count_reg] <=
                            fold_posterior_row;
                        // Commit fold state speculatively. Invalid images are
                        // stopped before emit, making these writes invisible
                        // while removing degree/error gates from wide CEs.
                        gathered_edges_reg <= next_gathered_edges;
                        gathered_count_reg <= next_gathered_count;
                        raw_min1_reg <= next_min1;
                        raw_min2_reg <= next_min2;
                        reduce_argmin_reg <= next_argmin;
                        reduce_parity_reg <= next_parity;
                        gathered_beat_count_reg <= gathered_beat_count_reg + 1'b1;
                        if ((!FAST_PATH && gather_error) ||
                            (next_gathered_count > degree_reg)) begin
                            image_error_reg <= 1'b1;
                            state <= S_DONE;
                        end else begin
                            for (seq_reduce_lane = 0; seq_reduce_lane < BANKS; seq_reduce_lane = seq_reduce_lane + 1) begin
                                if (fold_valid[seq_reduce_lane]) begin
                                    if (!EXTERNAL_EMIT_DESCRIPTOR) begin
                                        emit_edge_mem[gathered_beat_count_reg][seq_reduce_lane] <=
                                            fold_index[seq_reduce_lane];
                                        emit_orbit_mem[gathered_beat_count_reg][seq_reduce_lane] <=
                                            fold_orbit[seq_reduce_lane];
                                        emit_anchor_mem[gathered_beat_count_reg][seq_reduce_lane] <=
                                            fold_anchor[seq_reduce_lane];
                                        emit_valid_mem[gathered_beat_count_reg][seq_reduce_lane] <= 1'b1;
                                    end
                                end else if (!EXTERNAL_EMIT_DESCRIPTOR)
                                    emit_valid_mem[gathered_beat_count_reg][seq_reduce_lane] <= 1'b0;
                            end
                            if (next_gathered_count == degree_reg) begin
                                // Normalize in a separate state. The old
                                // same-edge normalize/clamp sat directly on
                                // the raw-min accumulator feedback path and
                                // added ~6 ns to the routed critical path.
                                // Raw minima are already committed above;
                                // this extra state costs one cycle/check and
                                // preserves exact values and tie selection.
                                state <= S_FINALIZE;
                            end
                        end
                    end

                    // Stage 2: register the prior stage's tournament result
                    // beside the same prepared beat.
                    fold_valid <= prepared_valid;
                    fold_posterior_row <= prepared_posterior_row;
                    candidate_valid_reg <= candidate_valid;
                    candidate_has2_reg <= candidate_has2;
                    candidate_min1_reg <= candidate_min1;
                    candidate_min2_reg <= candidate_min2;
                    candidate_argmin1_reg <= candidate_argmin1;
                    for (seq_reduce_lane = 0; seq_reduce_lane < BANKS; seq_reduce_lane = seq_reduce_lane + 1) begin
                        fold_index[seq_reduce_lane] <= prepared_index[seq_reduce_lane];
                        fold_value[seq_reduce_lane] <= prepared_value[seq_reduce_lane];
                        fold_extrinsic[seq_reduce_lane] <= prepared_extrinsic[seq_reduce_lane];
                        fold_orbit[seq_reduce_lane] <= prepared_orbit[seq_reduce_lane];
                        fold_anchor[seq_reduce_lane] <= prepared_anchor[seq_reduce_lane];
                    end

                    // Stage 1: calculate the extrinsic quantities for the
                    // beat captured during the preceding clock.
                    prepared_valid <= staged_valid;
                    for (seq_reduce_lane = 0; seq_reduce_lane < BANKS; seq_reduce_lane = seq_reduce_lane + 1) begin
                        prepared_index[seq_reduce_lane] <= gather_index[seq_reduce_lane];
                        prepared_value[seq_reduce_lane] <= gather_value[seq_reduce_lane];
                        prepared_extrinsic[seq_reduce_lane] <= gather_extrinsic[seq_reduce_lane];
                        prepared_magnitude[seq_reduce_lane] <= gather_magnitude[seq_reduce_lane];
                        prepared_orbit[seq_reduce_lane] <=
                            staged_orbits[seq_reduce_lane*ORBIT_WIDTH +: ORBIT_WIDTH];
                        prepared_anchor[seq_reduce_lane] <= staged_anchors[seq_reduce_lane*7 +: 7];
                    end

                    // Stage 0: capture the incoming conflict-free posterior
                    // beat. A zero mask is a harmless pipeline bubble.
                    staged_valid <= gather_valid;
                    if (gather_ready && |gather_valid) begin
                        staged_old_signs <= gather_old_signs;
                        staged_edge_indices <= gather_edge_indices;
                        staged_posteriors <= gather_posteriors;
                        staged_orbits <= gather_orbits;
                        staged_anchors <= gather_anchors;
                    end
                end

                S_EMIT: if (out_valid && out_ready) begin
                    if ((emitted_beat_reg + 1'b1) >= gathered_beat_count_reg) begin
                        emitted_beat_reg <= '0;
                        if (start_valid) begin
                            degree_reg <= start_degree;
                            syndrome_reg <= syndrome_bit;
                            old_min1_reg <= old_min1;
                            old_min2_reg <= old_min2;
                            old_argmin_reg <= old_argmin;
                            scatter_mode_reg <= scatter_mode;
                            scatter_old_min1_reg <= scatter_old_min1;
                            scatter_old_min2_reg <= scatter_old_min2;
                            scatter_old_argmin_reg <= scatter_old_argmin;
                            scatter_old_signs_reg <= scatter_old_signs;
                            correction_shift_reg <= RUNTIME_CONFIG ?
                                config_correction_shift : CORRECTION_SHIFT[2:0];
                            message_max_reg <= RUNTIME_CONFIG ?
                                config_message_max : MESSAGE_MAX[MESSAGE_MAGNITUDE_BITS-1:0];
                            image_error_reg <= (start_degree < 2) ||
                                               (start_degree > MAX_DEGREE);
                            gathered_count_reg <= '0;
                            gathered_beat_count_reg <= '0;
                            gathered_edges_reg <= '0;
                            raw_min1_reg <= {POSTERIOR_WIDTH{1'b1}};
                            raw_min2_reg <= {POSTERIOR_WIDTH{1'b1}};
                            reduce_argmin_reg <= '0;
                            reduce_parity_reg <= 1'b0;
                            new_min1_reg <= '0;
                            new_min2_reg <= '0;
                            new_argmin_reg <= '0;
                            staged_valid <= '0;
                            prepared_valid <= '0;
                            fold_valid <= '0;
                            candidate_valid_reg <= 1'b0;
                            candidate_has2_reg <= 1'b0;
                            state <= ((start_degree < 2) ||
                                      (start_degree > MAX_DEGREE)) ? S_DONE : S_GATHER;
                        end else
                            state <= S_DONE;
                    end else begin
                        emitted_beat_reg <= emitted_beat_reg + 1'b1;
                    end
                end

                S_FINALIZE: begin
                    new_min1_reg <= normalize_magnitude(
                        raw_min1_reg, correction_shift_reg, message_max_reg);
                    new_min2_reg <= normalize_magnitude(
                        raw_min2_reg, correction_shift_reg, message_max_reg);
                    new_argmin_reg <= reduce_argmin_reg;
                    emitted_beat_reg <= '0;
                    state <= S_EMIT;
                end

                S_DONE: if (done_valid && done_ready) begin
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
