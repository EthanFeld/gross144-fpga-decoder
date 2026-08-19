// Counter-only schedule/profile ROM for the fixed p=0.2% Gross144 S1W image.
// Every emitted group is one compiler-proven variable-disjoint 4-clique.
module paper_gross144_s1w_four_lane_schedule #(
    parameter integer BASIS_ID = 0,
    parameter integer FAST_MAX_SWEEPS = 10
) (
    input  logic [2:0] profile,
    input  logic [5:0] sweep,
    input  logic [3:0] time_ordinal,
    input  logic [4:0] group_ordinal,
    output logic [3:0] time_index,
    output logic [4:0] group_index,
    output logic [27:0] coordinates,
    output logic [6:0] coordinate0,
    output logic [6:0] coordinate1,
    output logic [6:0] coordinate2,
    output logic [6:0] coordinate3,
    output logic reverse,
    output logic [5:0] max_sweeps,
    output logic [2:0] prior_scale,
    output logic [2:0] correction_shift,
    output logic [5:0] message_max,
    output logic profile_invalid
);
    integer start_time, signed_time;

    function automatic [6:0] group_coordinate(
        input logic [4:0] group_id,
        input logic [1:0] lane
    );
        begin
            case (group_id)
            0:  case(lane) 0:group_coordinate=0; 1:group_coordinate=36; 2:group_coordinate=2;  default:group_coordinate=38; endcase
                1:  case(lane) 0:group_coordinate=1; 1:group_coordinate=37; 2:group_coordinate=35; default:group_coordinate=71; endcase
                2:  case(lane) 0:group_coordinate=3; 1:group_coordinate=39; 2:group_coordinate=5;  default:group_coordinate=41; endcase
                3:  case(lane) 0:group_coordinate=4; 1:group_coordinate=40; 2:group_coordinate=6;  default:group_coordinate=42; endcase
                4:  case(lane) 0:group_coordinate=7; 1:group_coordinate=43; 2:group_coordinate=9;  default:group_coordinate=45; endcase
                5:  case(lane) 0:group_coordinate=8; 1:group_coordinate=44; 2:group_coordinate=10; default:group_coordinate=46; endcase
                6:  case(lane) 0:group_coordinate=11;1:group_coordinate=47; 2:group_coordinate=13; default:group_coordinate=49; endcase
                7:  case(lane) 0:group_coordinate=12;1:group_coordinate=48; 2:group_coordinate=14; default:group_coordinate=50; endcase
                8:  case(lane) 0:group_coordinate=15;1:group_coordinate=51; 2:group_coordinate=17; default:group_coordinate=53; endcase
                9:  case(lane) 0:group_coordinate=16;1:group_coordinate=52; 2:group_coordinate=18; default:group_coordinate=54; endcase
                10: case(lane) 0:group_coordinate=19;1:group_coordinate=55; 2:group_coordinate=21; default:group_coordinate=57; endcase
                11: case(lane) 0:group_coordinate=20;1:group_coordinate=56; 2:group_coordinate=22; default:group_coordinate=58; endcase
                12: case(lane) 0:group_coordinate=23;1:group_coordinate=59; 2:group_coordinate=25; default:group_coordinate=61; endcase
                13: case(lane) 0:group_coordinate=24;1:group_coordinate=60; 2:group_coordinate=26; default:group_coordinate=62; endcase
                14: case(lane) 0:group_coordinate=27;1:group_coordinate=63; 2:group_coordinate=29; default:group_coordinate=65; endcase
                15: case(lane) 0:group_coordinate=28;1:group_coordinate=64; 2:group_coordinate=30; default:group_coordinate=66; endcase
                16: case(lane) 0:group_coordinate=31;1:group_coordinate=67; 2:group_coordinate=33; default:group_coordinate=69; endcase
                17: case(lane) 0:group_coordinate=32;1:group_coordinate=68; 2:group_coordinate=34; default:group_coordinate=70; endcase
                default: group_coordinate = 0;
            endcase
        end
    endfunction

    function automatic [6:0] standard_group_coordinate(
        input logic [4:0] group_id,
        input logic [1:0] lane
    );
        begin
            case (group_id)
                0:  case(lane) 0:standard_group_coordinate=0;  1:standard_group_coordinate=3;  2:standard_group_coordinate=37; default:standard_group_coordinate=40; endcase
                1:  case(lane) 0:standard_group_coordinate=1;  1:standard_group_coordinate=4;  2:standard_group_coordinate=38; default:standard_group_coordinate=41; endcase
                2:  case(lane) 0:standard_group_coordinate=2;  1:standard_group_coordinate=5;  2:standard_group_coordinate=36; default:standard_group_coordinate=39; endcase
                3:  case(lane) 0:standard_group_coordinate=6;  1:standard_group_coordinate=9;  2:standard_group_coordinate=43; default:standard_group_coordinate=46; endcase
                4:  case(lane) 0:standard_group_coordinate=7;  1:standard_group_coordinate=10; 2:standard_group_coordinate=44; default:standard_group_coordinate=47; endcase
                5:  case(lane) 0:standard_group_coordinate=8;  1:standard_group_coordinate=11; 2:standard_group_coordinate=42; default:standard_group_coordinate=45; endcase
                6:  case(lane) 0:standard_group_coordinate=12; 1:standard_group_coordinate=15; 2:standard_group_coordinate=49; default:standard_group_coordinate=52; endcase
                7:  case(lane) 0:standard_group_coordinate=13; 1:standard_group_coordinate=16; 2:standard_group_coordinate=50; default:standard_group_coordinate=53; endcase
                8:  case(lane) 0:standard_group_coordinate=14; 1:standard_group_coordinate=17; 2:standard_group_coordinate=48; default:standard_group_coordinate=51; endcase
                9:  case(lane) 0:standard_group_coordinate=18; 1:standard_group_coordinate=21; 2:standard_group_coordinate=55; default:standard_group_coordinate=58; endcase
                10: case(lane) 0:standard_group_coordinate=19; 1:standard_group_coordinate=22; 2:standard_group_coordinate=56; default:standard_group_coordinate=59; endcase
                11: case(lane) 0:standard_group_coordinate=20; 1:standard_group_coordinate=23; 2:standard_group_coordinate=54; default:standard_group_coordinate=57; endcase
                12: case(lane) 0:standard_group_coordinate=24; 1:standard_group_coordinate=27; 2:standard_group_coordinate=61; default:standard_group_coordinate=64; endcase
                13: case(lane) 0:standard_group_coordinate=25; 1:standard_group_coordinate=28; 2:standard_group_coordinate=62; default:standard_group_coordinate=65; endcase
                14: case(lane) 0:standard_group_coordinate=26; 1:standard_group_coordinate=29; 2:standard_group_coordinate=60; default:standard_group_coordinate=63; endcase
                15: case(lane) 0:standard_group_coordinate=30; 1:standard_group_coordinate=33; 2:standard_group_coordinate=67; default:standard_group_coordinate=70; endcase
                16: case(lane) 0:standard_group_coordinate=31; 1:standard_group_coordinate=34; 2:standard_group_coordinate=68; default:standard_group_coordinate=71; endcase
                17: case(lane) 0:standard_group_coordinate=32; 1:standard_group_coordinate=35; 2:standard_group_coordinate=66; default:standard_group_coordinate=69; endcase
                default: standard_group_coordinate = 0;
            endcase
        end
    endfunction

    // The stride-3 rescue reaches 3*31=93.  The old reducer only handled
    // values below 64; that silently produced an invalid time origin once
    // the rescue budget was widened.  Keep this as a bounded subtractor so
    // the schedule remains ROM/counter-only and synthesizes without %.
    function automatic [3:0] modulo_13(input logic [8:0] value);
        begin
            if (value >= 9'd91)
                modulo_13 = value - 9'd91;
            else if (value >= 9'd78)
                modulo_13 = value - 9'd78;
            else if (value >= 9'd65)
                modulo_13 = value - 9'd65;
            else if (value >= 9'd52)
                modulo_13 = value - 9'd52;
            else if (value >= 9'd39)
                modulo_13 = value - 9'd39;
            else if (value >= 9'd26)
                modulo_13 = value - 9'd26;
            else if (value >= 9'd13)
                modulo_13 = value - 9'd13;
            else
                modulo_13 = value[3:0];
        end
    endfunction

    always_comb begin
        profile_invalid = profile > 7 || time_ordinal > 12 || group_ordinal > 17;
        // Profile 0 is bounded by the two production wrappers (10 or 16).
        // Keeping the source value at 16 avoids synthesizing a wider rescue
        // compare into the common mapper/control cone while preserving the
        // full fast16 budget.
        max_sweeps = 16;
        prior_scale = 4;
        correction_shift = 3;
        message_max = 31;
        // Primary schedule is four_lane_pair_alternating_reverse.  The first
        // sweep is reverse, the second forward, matching the software oracle
        // and the exported RTL vectors for both basis images.  Holding X in
        // reverse order on every sweep made otherwise-valid shots reach the
        // terminal profile and appear as 100% FPGA defers.
        reverse = !sweep[0];
        start_time = 0;
        signed_time = 0;
        case (profile)
            0: begin
                // Primary profile stays at the routed/board-qualified
                // normalization. Alternative shifts changed convergence
                // corpus-wise but did not improve board mean latency.
                correction_shift = 3;
            end
            1: begin // C: pair-alternating
                max_sweeps = 30; prior_scale = 3; correction_shift = 4;
                message_max = 63; reverse = sweep[0];
            end
            2: begin // D28: pair-alternating-reverse
                max_sweeps = 28; prior_scale = 3; correction_shift = 4;
                message_max = 63; reverse = !sweep[0];
            end
            3: begin // E3: cyclic-alternating, stride three time slices
                // The prior E profile left two measured p=.002 tail cases
                // at the terminal defer.  E3 changes only its fixed-point
                // operating point and cyclic stride; both cases converge in
                // 24/28 sweeps in the software oracle and remain five-bit.
                max_sweeps = 32; prior_scale = 2; correction_shift = 3;
                message_max = 31; reverse = sweep[0];
                start_time = modulo_13({3'd0, sweep} +
                                       ({3'd0, sweep} << 1));
            end
            4: begin // G: cyclic-alternating-reverse, stride two time slices
                max_sweeps = 28; prior_scale = 2; correction_shift = 4;
                message_max = 63; reverse = !sweep[0];
                start_time = modulo_13({sweep, 1'b0});
            end
            5: begin // H: pair-alternating-reverse
                max_sweeps = 28; prior_scale = 2; correction_shift = 4;
                message_max = 63; reverse = !sweep[0];
            end
            6: begin
                max_sweeps = 16; message_max = 31;
                if (BASIS_ID == 0) begin // X FL073: four-lane reverse
                    prior_scale = 2; correction_shift = 3; reverse = 1'b1;
                end else begin // Z FL121: four-lane alternating
                    prior_scale = 1; correction_shift = 3; reverse = sweep[0];
                end
            end
            7: begin
                // FL232/FL161 rescue operating points from the frozen
                // four-lane search.  The previous X shift=5 / Z scale=2
                // settings caused the observed profile-7 terminal defers;
                // both images use the full six-bit message range and 32
                // sweeps, matching the selected safe rescue profiles.
                max_sweeps = 32; message_max = 63;
                if (BASIS_ID == 0) begin // X FL232: four-lane alternating-reverse
                    prior_scale = 5; correction_shift = 2; reverse = !sweep[0];
                end else begin // Z FL161: four-lane alternating
                    prior_scale = 4; correction_shift = 3; reverse = sweep[0];
                end
            end
            default: begin profile_invalid = 1'b1; end
        endcase

        // Fast FPGA wrappers may deliberately use only the primary profile
        // for common-case work.  A miss is handed to the host telescope; the
        // full software portfolio remains available with the default 10.
        if (profile == 0 && FAST_MAX_SWEEPS > 0 &&
            FAST_MAX_SWEEPS < max_sweeps)
            max_sweeps = FAST_MAX_SWEEPS;

        if (profile == 3 || profile == 4) begin
            signed_time = reverse ? start_time - time_ordinal : start_time + time_ordinal;
            if (signed_time < 0)
                signed_time = signed_time + 13;
            else if (signed_time >= 13)
                signed_time = signed_time - 13;
            time_index = signed_time[3:0];
        end else
            time_index = reverse ? 4'd12 - time_ordinal : time_ordinal;
        group_index = reverse ? 5'd17 - group_ordinal : group_ordinal;
        if (profile >= 6) begin
            coordinate0 = standard_group_coordinate(group_index, 2'd0);
            coordinate1 = standard_group_coordinate(group_index, 2'd1);
            coordinate2 = standard_group_coordinate(group_index, 2'd2);
            coordinate3 = standard_group_coordinate(group_index, 2'd3);
        end else begin
            coordinate0 = group_coordinate(group_index, 2'd0);
            coordinate1 = group_coordinate(group_index, 2'd1);
            coordinate2 = group_coordinate(group_index, 2'd2);
            coordinate3 = group_coordinate(group_index, 2'd3);
        end
        coordinates = {coordinate3, coordinate2, coordinate1, coordinate0};
    end
endmodule
