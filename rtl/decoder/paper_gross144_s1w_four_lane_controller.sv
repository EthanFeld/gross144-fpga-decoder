// Complete four-lane, detector-only Paper Gross144 S1W decoder.
//
// The controller executes four compiler-proven disjoint checks at once over
// two coherent copies of four dual-port posterior banks. It retries only on
// exact syndrome failure using the frozen P/C/D/E/F/G/H profile portfolio.
// The FPGA production image uses a 20-bit projection made from three complete
// equivariant rotation blocks.  It is a rejection/fast-path gate; the
// host owns the rare bounded handoffs and the exact verifier remains available
// for certified/debug builds.
module paper_gross144_s1w_four_lane_controller #(
    parameter integer BASIS_ID = 0,
    parameter META_FILE = "build/generated/paper_gross144_s1w_x_p002/meta.memb",
    parameter TEMPLATE_FILE = "build/generated/paper_gross144_s1w_x_p002/template_rows.memb",
    parameter COLORS_FILE = "build/generated/paper_gross144_s1w_x_p002/colors.memb",
    parameter PRIOR2_FILE = "build/generated/paper_gross144_s1w_x_p002/prior_orbits_scale2.memb",
    parameter PRIOR3_FILE = "build/generated/paper_gross144_s1w_x_p002/prior_orbits_scale3.memb",
    parameter PRIOR4_FILE = "build/generated/paper_gross144_s1w_x_p002/prior_orbits_scale4.memb",
    parameter HASH_TIME_FILE = "build/generated/paper_gross144_s1w_x_p002/hash_time_bases.memb",
    parameter ORBIT_CONFIG_FILE = "build/generated/paper_gross144_s1w_x_p002/orbit_config.memb",
    parameter LOGICAL_QUADS_FILE = "build/generated/paper_gross144_s1w_x_p002/logical_quads.memb",
    parameter LOGICAL_QUADS_BANK0_FILE = "",
    parameter LOGICAL_QUADS_BANK1_FILE = "",
    parameter LOGICAL_QUADS_BANK2_FILE = "",
    parameter LOGICAL_QUADS_BANK3_FILE = "",
    parameter integer TEMPLATE_SLOT_IMAGE_MODE = 0,
    parameter integer HASH_WIDTH = 6,
    parameter TEMPLATE_SLOT0_FILE = "",
    parameter TEMPLATE_SLOT1_FILE = "",
    parameter TEMPLATE_SLOT2_FILE = "",
    parameter TEMPLATE_SLOT3_FILE = "",
    // Verification/debug mode. Production layered wrappers set this true;
    // leaving it false retains the historical hash rejection filter.
    parameter integer FORCE_EXACT_REPLAY = 0,
    // Optional flooding Relay path. Default 0 preserves the shipped layered
    // S1W controller; Relay builds use the dual record banks below.
    parameter integer RELAY_MODE = 0,
    // Maximum profile attempted before a bounded defer.  The full software
    // portfolio uses 7; the FPGA fast/common path uses 0 and sends misses to
    // the host telescope instead of paying the entire rare-tail portfolio.
    parameter integer MAX_PROFILE = 7,
    // Handoff mode retains profile IDs for diagnostics but bounds terminal
    // rescue profiles to one sweep; the resident CPU owns those cases.
    // Maximum primary-profile sweeps in the bounded FPGA fast path.
    parameter integer FAST_MAX_SWEEPS = 10,
    // Common-path handoff keeps live mapping on schedule coordinates. The
    // certified top also enters streamed exact replay; verify reads select
    // their registered verify coordinates below.
    parameter integer FAST_HANDOFF = 0,
    // Native DPB read data is valid one edge after issue. Keep a selectable
    // tag pipeline for board bring-up; depth 1 removes avoidable verify/run
    // bubbles once the target RAM mode is confirmed.
    parameter integer READ_RESPONSE_STAGES = 1,
    // Separate logical-read settle depth for native-DPB board bring-up.
    parameter integer LOGICAL_READ_RESPONSE_STAGES = 1,
    // Emit-time parity is a fast reject/diagnostic. It is not final syndrome
    // proof because edge-update signs can repeat variables. Certified images
    // use FORCE_EXACT_REPLAY for acceptance.
    parameter integer INLINE_EXACT_CHECK = 1,
    // Exact replay is the acceptance oracle. Replaying every sweep is
    // redundant: retain candidate state, verify every Nth sweep, and always
    // verify the terminal sweep. N=1 preserves the legacy cadence.
    parameter integer EXACT_VERIFY_INTERVAL = 1,
    // Optional speed experiment: accept a hash-zero candidate without the
    // 936-check replay.  Disabled in the certified image; hash collisions
    // are otherwise latency-only because exact replay remains authoritative.
    parameter integer HASH_ONLY_ACCEPT = 0,
    // A terminal-sweep hash/parity pass is not a proof of the full detector
    // word.  Defer it to the exact host tail instead of returning a false
    // logical class.  Earlier passes still take the normal fast path.
    parameter integer RISK_DEFER_FINAL_SWEEP = 0,
    // Optional next-group record prefetch. The certified image keeps this
    // off until the extra record copies/buffer timing is closed on hardware.
    parameter integer PREFETCH_RECORDS = 0,
    // The compiler proves port conflict freedom. Keep the physical conflict
    // detector available for bring-up, but allow the timing image to remove
    // its wide DPB-address fanout from the core clock path.
    parameter integer ENABLE_MEMORY_GUARD = 1,
    parameter integer ENABLE_DEBUG = 1,
    // Primary Paper S1W uses five-bit signed message magnitudes. Rescue
    // profiles may override this to six bits in a separately built image.
    parameter integer MESSAGE_MAGNITUDE_BITS = 6
) (
    input  logic clk,
    input  logic rst,
    input  logic shot_clear,
    input  logic syndrome_write_valid,
    input  logic [3:0] syndrome_write_time,
    input  logic [4:0] syndrome_write_group,
    input  logic [3:0] syndrome_write_bits,
    input  logic start_valid,
    output logic start_ready,
    output logic done_valid,
    input  logic done_ready,
    output logic success,
    output logic deferred,
    output logic [11:0] logical_class,
    output logic [31:0] cycle_count,
    output logic [15:0] sweeps_used,
    output logic [2:0] profile_used,
    output logic error,
    output logic busy,
    output logic [12:0] debug_state,
    output logic [3:0] debug_guards,
    output logic [7:0] debug_map_detail,
    output logic [7:0] debug_fault_conflict_banks,
    output logic [23:0] debug_fault_addr0,
    output logic [23:0] debug_fault_addr1,
    output logic [3:0] debug_fault_time,
    output logic [4:0] debug_fault_group,
    output logic [3:0] debug_fault_beat,
    output logic [12:0] debug_invalid_state,
    output logic [22:0] debug_fault_descriptor0,
    output logic [22:0] debug_fault_descriptor1,
    output logic [31:0] debug_fault_banks,
    output logic [31:0] debug_gather_digest,
    output logic [31:0] debug_early_gathers,
    output logic [31:0] debug_early_raw0,
    output logic [31:0] debug_early_raw1,
    output logic [15:0] debug_early_response_banks,
    output logic [7:0] debug_early_response_port1,
    output logic [31:0] debug_early_map_banks,
    output logic [27:0] debug_early_map_coordinates,
    output logic [15:0] debug_gather_checkpoint0,
    output logic [15:0] debug_gather_checkpoint1,
    output logic [31:0] debug_verify_digest,
    output logic [3:0] debug_verify_parity,
    output logic [3:0] debug_verify_syndrome,
    output logic [7:0] debug_verify_address,
    output logic [7:0] debug_gather_count
);
    localparam integer CHECKS = 936;
    localparam integer ORBITS = 122;
    localparam integer BANKS = 4;
    localparam integer TEMPLATE_WORDS = 117;
    localparam integer MAX_BEATS = 9;
    localparam integer RECORD_ARGMIN_LSB = 2 * MESSAGE_MAGNITUDE_BITS;
    localparam integer RECORD_SIGN_LSB = RECORD_ARGMIN_LSB + 6;
    localparam integer RECORD_WIDTH = 35 + RECORD_SIGN_LSB;

    // Explicit one-hot state removes the binary decode cone that otherwise
    // fans into every port of all 24 posterior DPBs.  At high utilization the
    // routed binary decode, not decoder arithmetic, was the limiting path.
    localparam logic [12:0] S_IDLE          = 13'b0000000000001;
    localparam logic [12:0] S_INIT          = 13'b0000000000010;
    localparam logic [12:0] S_RECORD_LOAD   = 13'b0000000000100;
    localparam logic [12:0] S_START         = 13'b0000000001000;
    localparam logic [12:0] S_RUN           = 13'b0000000010000;
    localparam logic [12:0] S_SWEEP_DECIDE  = 13'b0000000100000;
    localparam logic [12:0] S_VERIFY_SETUP  = 13'b0000001000000;
    localparam logic [12:0] S_VERIFY_RUN    = 13'b0000010000000;
    localparam logic [12:0] S_LOGICAL_SETUP = 13'b0000100000000;
    localparam logic [12:0] S_LOGICAL_RUN   = 13'b0001000000000;
    localparam logic [12:0] S_DONE          = 13'b0010000000000;
    localparam logic [12:0] S_ERROR         = 13'b0100000000000;
    localparam logic [13:0] S_GROUP_SETUP   = 14'b01000000000000;
    localparam logic [13:0] S_TARGET_REMAP  = 14'b10000000000000;

    (* syn_encoding = "onehot", fsm_encoding = "one-hot" *) logic [13:0] state;

    (* ram_style = "distributed" *) logic [9:0] meta_rom [0:12];
    (* ram_style = "distributed" *) logic [HASH_WIDTH-1:0] hash_time_rom [0:12];

    initial begin
        $readmemb(META_FILE, meta_rom);
        $readmemb(HASH_TIME_FILE, hash_time_rom);
    end

    function automatic [4:0] divide_by_6(input logic [6:0] value);
        logic [12:0] times_43;
        begin
            times_43 = ({6'd0, value} << 5) + ({6'd0, value} << 3) +
                       ({6'd0, value} << 1) + value;
            divide_by_6 = times_43 >> 8;
        end
    endfunction

    function automatic [2:0] coordinate_y(input logic [6:0] value);
        logic [4:0] quotient;
        begin
            quotient = divide_by_6(value);
            coordinate_y = value - ((quotient << 2) + (quotient << 1));
        end
    endfunction

    function automatic [4:0] component_coordinate_x(input logic [6:0] value);
        begin
            // Keep the live inline mapper on the same shallow constant
            // divide used by the standalone address module.  The old
            // comparator ladder was replicated for each explicit lane/slot.
            component_coordinate_x = divide_by_6(value);
        end
    endfunction

    function automatic [1:0] component_bank_inline(
        input logic [1:0] color,
        input logic [6:0] anchor,
        input logic [6:0] check
    );
        logic [5:0] translated_x;
        begin
            translated_x = {1'b0, component_coordinate_x(anchor)} +
                           {1'b0, component_coordinate_x(check)};
            if (translated_x >= 6'd12)
                translated_x = translated_x - 6'd12;
            component_bank_inline = {1'b0, color} +
                                    {1'b0, translated_x[1:0]};
        end
    endfunction

    function automatic [11:0] component_address_inline(
        input logic [6:0] orbit,
        input logic [1:0] color,
        input logic [6:0] anchor,
        input logic [6:0] check
    );
        logic [4:0] anchor_x, check_x;
        logic [3:0] anchor_y, check_y;
        logic [5:0] translated_x;
        logic [4:0] translated_y;
        logic [2:0] residue;
        logic [5:0] x_delta;
        logic [3:0] x_group;
        logic [1:0] bank_value;
        logic [13:0] orbit_times_18;
        logic [11:0] x_group_times_6;
        logic [12:0] address_sum;
        begin
            anchor_x = component_coordinate_x(anchor);
            check_x = component_coordinate_x(check);
            anchor_y = anchor - ((anchor_x << 2) + (anchor_x << 1));
            check_y = check - ((check_x << 2) + (check_x << 1));
            translated_x = {1'b0, anchor_x} + {1'b0, check_x};
            translated_y = {1'b0, anchor_y} + {1'b0, check_y};
            if (translated_x >= 6'd12)
                translated_x = translated_x - 6'd12;
            if (translated_y >= 5'd6)
                translated_y = translated_y - 5'd6;
            bank_value = {1'b0, color} + {1'b0, translated_x[1:0]};
            residue = ({1'b0, bank_value} + 3'd4 - {1'b0, color}) & 3'd3;
            x_delta = translated_x - {3'd0, residue};
            x_group = x_delta >> 2;
            orbit_times_18 = ({7'd0, orbit} << 4) + ({7'd0, orbit} << 1);
            x_group_times_6 = ({8'd0, x_group} << 2) +
                              ({8'd0, x_group} << 1);
            address_sum = {1'b0, orbit_times_18[11:0]} +
                          {1'b0, x_group_times_6} +
                          {{9{1'b0}}, translated_y};
            component_address_inline = address_sum[11:0];
        end
    endfunction

    function automatic component_invalid_inline(
        input logic [6:0] orbit,
        input logic [6:0] anchor,
        input logic [6:0] check
    );
        begin
            component_invalid_inline = (orbit >= 7'd122) ||
                                       (anchor >= 7'd72) ||
                                       (check >= 7'd72);
        end
    endfunction

    function automatic [11:0] rotate_hash12(
        input logic [11:0] value,
        input logic [3:0] amount
    );
        begin
            rotate_hash12 = (value << amount) | (value >> (4'd12 - amount));
        end
    endfunction

    function automatic [5:0] rotate_hash6(
        input logic [5:0] value,
        input logic [2:0] amount
    );
        begin
            rotate_hash6 = (value << amount) | (value >> (3'd6 - amount));
        end
    endfunction

    function automatic [1:0] rotate_hash2(
        input logic [1:0] value,
        input logic amount
    );
        begin
            rotate_hash2 = amount ? {value[0], value[1]} : value;
        end
    endfunction

    function automatic [31:0] transform_hash32(
        input logic [31:0] value,
        input logic [6:0] coordinate
    );
        logic [4:0] x;
        logic [2:0] y;
        begin
            x = divide_by_6(coordinate);
            y = coordinate_y(coordinate);
            transform_hash32 = {
                rotate_hash2(value[31:30], y[0]),
                rotate_hash12(value[29:18], x[3:0]),
                rotate_hash6(value[17:12], y),
                rotate_hash12(value[11:0], x[3:0])
            };
        end
    endfunction

    function automatic [23:0] transform_hash24(
        input logic [23:0] value,
        input logic [6:0] coordinate
    );
        logic [4:0] x;
        begin
            x = divide_by_6(coordinate);
            // Keep the low and high 12-bit x-rotation blocks from the
            // compiler's 32-bit word.  Each block is closed under every
            // translation, so projection cannot create a non-equivariant
            // partial rotate.
            transform_hash24 = {
                rotate_hash12(value[23:12], x[3:0]),
                rotate_hash12(value[11:0], x[3:0])
            };
        end
    endfunction

    function automatic [19:0] transform_hash20(
        input logic [19:0] value,
        input logic [6:0] coordinate
    );
        logic [4:0] x;
        logic [2:0] y;
        begin
            x = divide_by_6(coordinate);
            y = coordinate_y(coordinate);
            transform_hash20 = {
                rotate_hash2(value[19:18], y[0]),
                rotate_hash6(value[17:12], y),
                rotate_hash12(value[11:0], x[3:0])
            };
        end
    endfunction

    function automatic [HASH_WIDTH-1:0] transform_hash(
        input logic [31:0] value,
        input logic [6:0] coordinate
    );
        logic [2:0] y;
        begin
            if (HASH_WIDTH == 32)
                transform_hash = transform_hash32(value, coordinate);
            else if (HASH_WIDTH == 24)
                transform_hash = transform_hash24(value[23:0], coordinate);
            else if (HASH_WIDTH == 20)
                transform_hash = transform_hash20(value[19:0], coordinate);
            else begin
                y = coordinate_y(coordinate);
                case (y)
                    0: transform_hash = value[5:0];
                    1: transform_hash = {value[4:0], value[5]};
                    2: transform_hash = {value[3:0], value[5:4]};
                    3: transform_hash = {value[2:0], value[5:3]};
                    4: transform_hash = {value[1:0], value[5:2]};
                    default: transform_hash = {value[0], value[5:1]};
                endcase
            end
        end
    endfunction

    function automatic [9:0] check_id(
        input logic [3:0] time_index,
        input logic [6:0] coordinate
    );
        begin check_id = (time_index << 6) + (time_index << 3) + coordinate; end
    endfunction

    // UART ingress is compacted in the pair partition. Profiles 6/7 use the
    // standard four-lane partition, so retain a raw 13x72 target map and
    // remap the incoming pair nibbles at load time. Without this, profile 6/7
    // compared each standard-lane parity against four unrelated pair bits.
    function automatic [6:0] pair_group_coordinate(
        input logic [4:0] group_id,
        input logic [1:0] lane
    );
        begin
            case (group_id)
                0:  case(lane) 0:pair_group_coordinate=0;  1:pair_group_coordinate=36; 2:pair_group_coordinate=2;  default:pair_group_coordinate=38; endcase
                1:  case(lane) 0:pair_group_coordinate=1;  1:pair_group_coordinate=37; 2:pair_group_coordinate=35; default:pair_group_coordinate=71; endcase
                2:  case(lane) 0:pair_group_coordinate=3;  1:pair_group_coordinate=39; 2:pair_group_coordinate=5; default:pair_group_coordinate=41; endcase
                3:  case(lane) 0:pair_group_coordinate=4;  1:pair_group_coordinate=40; 2:pair_group_coordinate=6; default:pair_group_coordinate=42; endcase
                4:  case(lane) 0:pair_group_coordinate=7;  1:pair_group_coordinate=43; 2:pair_group_coordinate=9; default:pair_group_coordinate=45; endcase
                5:  case(lane) 0:pair_group_coordinate=8;  1:pair_group_coordinate=44; 2:pair_group_coordinate=10; default:pair_group_coordinate=46; endcase
                6:  case(lane) 0:pair_group_coordinate=11; 1:pair_group_coordinate=47; 2:pair_group_coordinate=13; default:pair_group_coordinate=49; endcase
                7:  case(lane) 0:pair_group_coordinate=12; 1:pair_group_coordinate=48; 2:pair_group_coordinate=14; default:pair_group_coordinate=50; endcase
                8:  case(lane) 0:pair_group_coordinate=15; 1:pair_group_coordinate=51; 2:pair_group_coordinate=17; default:pair_group_coordinate=53; endcase
                9:  case(lane) 0:pair_group_coordinate=16; 1:pair_group_coordinate=52; 2:pair_group_coordinate=18; default:pair_group_coordinate=54; endcase
                10: case(lane) 0:pair_group_coordinate=19; 1:pair_group_coordinate=55; 2:pair_group_coordinate=21; default:pair_group_coordinate=57; endcase
                11: case(lane) 0:pair_group_coordinate=20; 1:pair_group_coordinate=56; 2:pair_group_coordinate=22; default:pair_group_coordinate=58; endcase
                12: case(lane) 0:pair_group_coordinate=23; 1:pair_group_coordinate=59; 2:pair_group_coordinate=25; default:pair_group_coordinate=61; endcase
                13: case(lane) 0:pair_group_coordinate=24; 1:pair_group_coordinate=60; 2:pair_group_coordinate=26; default:pair_group_coordinate=62; endcase
                14: case(lane) 0:pair_group_coordinate=27; 1:pair_group_coordinate=63; 2:pair_group_coordinate=29; default:pair_group_coordinate=65; endcase
                15: case(lane) 0:pair_group_coordinate=28; 1:pair_group_coordinate=64; 2:pair_group_coordinate=30; default:pair_group_coordinate=66; endcase
                16: case(lane) 0:pair_group_coordinate=31; 1:pair_group_coordinate=67; 2:pair_group_coordinate=33; default:pair_group_coordinate=69; endcase
                17: case(lane) 0:pair_group_coordinate=32; 1:pair_group_coordinate=68; 2:pair_group_coordinate=34; default:pair_group_coordinate=70; endcase
                default: pair_group_coordinate = 0;
            endcase
        end
    endfunction

    function automatic [4:0] standard_source_pair_group(
        input logic [4:0] group_id,
        input logic [1:0] lane
    );
        begin
            case (group_id)
                0:  case(lane) 0:standard_source_pair_group=0; 1:standard_source_pair_group=2; 2:standard_source_pair_group=1; default:standard_source_pair_group=3; endcase
                1:  case(lane) 0:standard_source_pair_group=1; 1:standard_source_pair_group=3; 2:standard_source_pair_group=0; default:standard_source_pair_group=2; endcase
                2:  case(lane) 0:standard_source_pair_group=0; 1:standard_source_pair_group=2; 2:standard_source_pair_group=0; default:standard_source_pair_group=2; endcase
                3:  case(lane) 0:standard_source_pair_group=3; 1:standard_source_pair_group=4; 2:standard_source_pair_group=4; default:standard_source_pair_group=5; endcase
                4:  case(lane) 0:standard_source_pair_group=4; 1:standard_source_pair_group=5; 2:standard_source_pair_group=5; default:standard_source_pair_group=6; endcase
                5:  case(lane) 0:standard_source_pair_group=5; 1:standard_source_pair_group=6; 2:standard_source_pair_group=3; default:standard_source_pair_group=4; endcase
                6:  case(lane) 0:standard_source_pair_group=7; 1:standard_source_pair_group=8; 2:standard_source_pair_group=6; default:standard_source_pair_group=9; endcase
                7:  case(lane) 0:standard_source_pair_group=6; 1:standard_source_pair_group=9; 2:standard_source_pair_group=7; default:standard_source_pair_group=8; endcase
                8:  case(lane) 0:standard_source_pair_group=7; 1:standard_source_pair_group=8; 2:standard_source_pair_group=7; default:standard_source_pair_group=8; endcase
                9:  case(lane) 0:standard_source_pair_group=9; 1:standard_source_pair_group=10; 2:standard_source_pair_group=10; default:standard_source_pair_group=11; endcase
                10: case(lane) 0:standard_source_pair_group=10; 1:standard_source_pair_group=11; 2:standard_source_pair_group=11; default:standard_source_pair_group=12; endcase
                11: case(lane) 0:standard_source_pair_group=11; 1:standard_source_pair_group=12; 2:standard_source_pair_group=9; default:standard_source_pair_group=10; endcase
                12: case(lane) 0:standard_source_pair_group=13; 1:standard_source_pair_group=14; 2:standard_source_pair_group=12; default:standard_source_pair_group=15; endcase
                13: case(lane) 0:standard_source_pair_group=12; 1:standard_source_pair_group=15; 2:standard_source_pair_group=13; default:standard_source_pair_group=14; endcase
                14: case(lane) 0:standard_source_pair_group=13; 1:standard_source_pair_group=14; 2:standard_source_pair_group=13; default:standard_source_pair_group=14; endcase
                15: case(lane) 0:standard_source_pair_group=15; 1:standard_source_pair_group=16; 2:standard_source_pair_group=16; default:standard_source_pair_group=17; endcase
                16: case(lane) 0:standard_source_pair_group=16; 1:standard_source_pair_group=17; 2:standard_source_pair_group=17; default:standard_source_pair_group=1; endcase
                17: case(lane) 0:standard_source_pair_group=17; 1:standard_source_pair_group=1; 2:standard_source_pair_group=15; default:standard_source_pair_group=16; endcase
                default: standard_source_pair_group = 0;
            endcase
        end
    endfunction

    function automatic [1:0] standard_source_pair_lane(
        input logic [4:0] group_id,
        input logic [1:0] lane
    );
        begin
            case (group_id)
                0:  case(lane) 0:standard_source_pair_lane=0; 1:standard_source_pair_lane=0; 2:standard_source_pair_lane=1; default:standard_source_pair_lane=1; endcase
                1:  case(lane) 0:standard_source_pair_lane=0; 1:standard_source_pair_lane=0; 2:standard_source_pair_lane=3; default:standard_source_pair_lane=3; endcase
                2:  case(lane) 0:standard_source_pair_lane=2; 1:standard_source_pair_lane=2; 2:standard_source_pair_lane=1; default:standard_source_pair_lane=1; endcase
                3:  case(lane) 0:standard_source_pair_lane=2; 1:standard_source_pair_lane=2; 2:standard_source_pair_lane=1; default:standard_source_pair_lane=3; endcase
                4:  case(lane) 0:standard_source_pair_lane=0; 1:standard_source_pair_lane=2; 2:standard_source_pair_lane=1; default:standard_source_pair_lane=1; endcase
                5:  case(lane) 0:standard_source_pair_lane=0; 1:standard_source_pair_lane=0; 2:standard_source_pair_lane=3; default:standard_source_pair_lane=3; endcase
                6:  case(lane) 0:standard_source_pair_lane=0; 1:standard_source_pair_lane=0; 2:standard_source_pair_lane=3; default:standard_source_pair_lane=1; endcase
                7:  case(lane) 0:standard_source_pair_lane=2; 1:standard_source_pair_lane=0; 2:standard_source_pair_lane=3; default:standard_source_pair_lane=3; endcase
                8:  case(lane) 0:standard_source_pair_lane=2; 1:standard_source_pair_lane=2; 2:standard_source_pair_lane=1; default:standard_source_pair_lane=1; endcase
                9:  case(lane) 0:standard_source_pair_lane=2; 1:standard_source_pair_lane=2; 2:standard_source_pair_lane=1; default:standard_source_pair_lane=3; endcase
                10: case(lane) 0:standard_source_pair_lane=0; 1:standard_source_pair_lane=2; 2:standard_source_pair_lane=1; default:standard_source_pair_lane=1; endcase
                11: case(lane) 0:standard_source_pair_lane=0; 1:standard_source_pair_lane=0; 2:standard_source_pair_lane=3; default:standard_source_pair_lane=3; endcase
                12: case(lane) 0:standard_source_pair_lane=0; 1:standard_source_pair_lane=0; 2:standard_source_pair_lane=3; default:standard_source_pair_lane=1; endcase
                13: case(lane) 0:standard_source_pair_lane=2; 1:standard_source_pair_lane=0; 2:standard_source_pair_lane=3; default:standard_source_pair_lane=3; endcase
                14: case(lane) 0:standard_source_pair_lane=2; 1:standard_source_pair_lane=2; 2:standard_source_pair_lane=1; default:standard_source_pair_lane=1; endcase
                15: case(lane) 0:standard_source_pair_lane=2; 1:standard_source_pair_lane=2; 2:standard_source_pair_lane=1; default:standard_source_pair_lane=3; endcase
                16: case(lane) 0:standard_source_pair_lane=0; 1:standard_source_pair_lane=2; 2:standard_source_pair_lane=1; default:standard_source_pair_lane=3; endcase
                17: case(lane) 0:standard_source_pair_lane=0; 1:standard_source_pair_lane=2; 2:standard_source_pair_lane=3; default:standard_source_pair_lane=3; endcase
                default: standard_source_pair_lane = 0;
            endcase
        end
    endfunction

    function automatic [11:0] debug_bank_address(
        input logic [47:0] packed_address,
        input logic [1:0] bank_index
    );
        begin
            case (bank_index)
                2'd0: debug_bank_address = packed_address[0 +: 12];
                2'd1: debug_bank_address = packed_address[12 +: 12];
                2'd2: debug_bank_address = packed_address[24 +: 12];
                default: debug_bank_address = packed_address[36 +: 12];
            endcase
        end
    endfunction

    function automatic [6:0] template_address(
        input logic [3:0] time_index,
        input logic [3:0] beat_index
    );
        logic [7:0] expanded;
        begin
            expanded = ({4'd0, time_index} << 3) + time_index + beat_index;
            template_address = expanded[6:0];
        end
    endfunction

    logic [2:0] profile_reg;
    logic [5:0] sweep_reg;
    logic [3:0] time_ordinal_reg;
    logic [4:0] group_ordinal_reg;
    logic [3:0] schedule_query_time_ordinal;
    logic [4:0] schedule_query_group_ordinal;
    logic [3:0] schedule_time;
    logic [4:0] schedule_group;
    logic [27:0] schedule_coordinates;
    logic [6:0] schedule_coordinate0, schedule_coordinate1;
    logic [6:0] schedule_coordinate2, schedule_coordinate3;
    logic schedule_reverse, schedule_invalid;
    logic [3:0] active_schedule_time;
    logic [4:0] active_schedule_group;
    logic [27:0] active_schedule_coordinates;
    (* syn_keep = 1 *) logic [6:0] active_schedule_coordinate0, active_schedule_coordinate1;
    (* syn_keep = 1 *) logic [6:0] active_schedule_coordinate2, active_schedule_coordinate3;
    // Latch template depth with the schedule context. Reading meta_rom via
    // the live time counter while a group is active can mix the previous time
    // slice's depth with the new slice's descriptors.
    logic [3:0] active_template_beats;
    logic [5:0] profile_max_sweeps;
    logic [2:0] profile_prior_scale, profile_correction_shift;
    logic [5:0] schedule_message_max;
    logic [MESSAGE_MAGNITUDE_BITS-1:0] profile_message_max;

    always_comb begin
        schedule_query_time_ordinal = time_ordinal_reg;
        schedule_query_group_ordinal = group_ordinal_reg;
        // Once a last emit beat is buffered, all live datapath context comes
        // from registered `active_schedule_*`. Reuse the schedule generator
        // during its two write phases to calculate the following group and
        // capture it at commit, eliminating 233 setup bubbles per sweep.
        if (PREFETCH_RECORDS && (state == S_START || state == S_RUN) &&
            !(time_ordinal_reg == 12 && group_ordinal_reg == 17)) begin
            if (group_ordinal_reg == 17) begin
                schedule_query_group_ordinal = 0;
                schedule_query_time_ordinal = time_ordinal_reg + 1'b1;
            end else
                schedule_query_group_ordinal = group_ordinal_reg + 1'b1;
        end else if (state == S_RUN && emit_buffer_valid && emit_buffer_last &&
            !(time_ordinal_reg == 12 && group_ordinal_reg == 17)) begin
            if (group_ordinal_reg == 17) begin
                schedule_query_group_ordinal = 0;
                schedule_query_time_ordinal = time_ordinal_reg + 1'b1;
            end else
                schedule_query_group_ordinal = group_ordinal_reg + 1'b1;
        end
    end

    paper_gross144_s1w_four_lane_schedule #(
        .BASIS_ID(BASIS_ID), .FAST_MAX_SWEEPS(FAST_MAX_SWEEPS)
    ) u_schedule (
        .profile(profile_reg), .sweep(sweep_reg),
        .time_ordinal(schedule_query_time_ordinal),
        .group_ordinal(schedule_query_group_ordinal),
        .time_index(schedule_time), .group_index(schedule_group),
        .coordinates(schedule_coordinates), .reverse(schedule_reverse),
        .coordinate0(schedule_coordinate0), .coordinate1(schedule_coordinate1),
        .coordinate2(schedule_coordinate2), .coordinate3(schedule_coordinate3),
        .max_sweeps(profile_max_sweeps), .prior_scale(profile_prior_scale),
        .correction_shift(profile_correction_shift), .message_max(schedule_message_max),
        .profile_invalid(schedule_invalid)
    );
    // Production S1W uses five-bit sign/magnitude. Keep wider rescue metadata
    // inside the schedule ROM and make release narrowing explicit.
    assign profile_message_max = schedule_message_max[MESSAGE_MAGNITUDE_BITS-1:0];

    logic [3:0] verify_time_ordinal;
    logic [4:0] verify_group_ordinal;
    logic [3:0] verify_time;
    logic [4:0] verify_group_unused;
    logic [27:0] verify_coordinates;
    logic [6:0] verify_coordinate0, verify_coordinate1;
    logic [6:0] verify_coordinate2, verify_coordinate3;
    logic [3:0] active_verify_time;
    logic [27:0] active_verify_coordinates;
    logic [6:0] active_verify_coordinate0, active_verify_coordinate1;
    logic [6:0] active_verify_coordinate2, active_verify_coordinate3;
    logic [3:0] active_verify_template_beats;
    logic verify_schedule_reverse, verify_schedule_invalid;
    logic [5:0] verify_schedule_sweeps;
    logic [2:0] verify_schedule_scale, verify_schedule_shift;
    logic [5:0] verify_schedule_max;

    paper_gross144_s1w_four_lane_schedule #(
        .BASIS_ID(BASIS_ID), .FAST_MAX_SWEEPS(FAST_MAX_SWEEPS)
    ) u_verify_schedule (
        .profile(3'd1), .sweep(6'd0),
        .time_ordinal(state == S_IDLE ? syndrome_write_time : verify_time_ordinal),
        .group_ordinal(state == S_IDLE ? syndrome_write_group : verify_group_ordinal),
        .time_index(verify_time),
        .group_index(verify_group_unused), .coordinates(verify_coordinates),
        .coordinate0(verify_coordinate0), .coordinate1(verify_coordinate1),
        .coordinate2(verify_coordinate2), .coordinate3(verify_coordinate3),
        .reverse(verify_schedule_reverse), .max_sweeps(verify_schedule_sweeps),
        .prior_scale(verify_schedule_scale), .correction_shift(verify_schedule_shift),
        .message_max(verify_schedule_max), .profile_invalid(verify_schedule_invalid)
    );

    logic record_read_en, record_read_valid, record_read_en_b, record_read_valid_b,
          record_write_en;
    logic [9:0] record_read_addr, record_read_addr_b, record_write_addr;
    logic [RECORD_WIDTH-1:0] record_read_data, record_read_data_b, record_write_data;
    logic [2:0] record_response_lane_b;
    assign record_response_lane_b = '0;
    logic record_new_read_en, record_new_read_valid;
    logic [9:0] record_new_read_addr;
    logic [RECORD_WIDTH-1:0] record_new_read_data;
    logic record_write_bank;
    logic relay_phase_reg, relay_old_bank_reg;
    generate
        if (RELAY_MODE) begin : g_relay_records
            paper_gross144_s1w_check_record_dual_read_ram #(
                .ADDR_WIDTH(10), .RECORD_WIDTH(RECORD_WIDTH), .DEPTH(CHECKS)
            ) u_records (
                .clk(clk), .rst(rst),
                .old_read_en(record_read_en), .old_bank_select(relay_old_bank_reg),
                .old_read_addr(record_read_addr), .old_read_valid(record_read_valid),
                .old_read_data(record_read_data),
                .new_read_en(record_new_read_en),
                .new_bank_select(~relay_old_bank_reg),
                .new_read_addr(record_new_read_addr),
                .new_read_valid(record_new_read_valid),
                .new_read_data(record_new_read_data),
                .write_en(record_write_en), .write_bank(record_write_bank),
                .write_addr(record_write_addr), .write_data(record_write_data)
            );
            assign record_read_valid_b = 1'b0;
            assign record_read_data_b = '0;
        end else begin : g_legacy_records
            paper_gross144_s1w_check_record_ram #(
                .ADDR_WIDTH(10), .RECORD_WIDTH(RECORD_WIDTH), .DEPTH(CHECKS)
            ) u_records (
                .clk(clk), .rst(rst), .read_en(record_read_en),
                .read_addr(record_read_addr), .read_valid(record_read_valid),
                .read_data(record_read_data),
                .write_en(record_write_en),
                .write_addr(record_write_addr), .write_data(record_write_data)
            );
            assign record_new_read_valid = 1'b0;
            assign record_new_read_data = '0;
            assign record_read_valid_b = 1'b0;
            assign record_read_data_b = '0;
        end
    endgenerate

    logic syndrome_read_en;
    logic [7:0] syndrome_read_addr, syndrome_write_addr;
    logic [3:0] syndrome_read_bits;
    logic [3:0] syndrome_pair_read_bits1, syndrome_pair_read_bits2;
    logic [3:0] syndrome_pair_read_bits3;
    logic [3:0] standard_syndrome_read_bits;
    logic [7:0] remap_pair_read_addr0, remap_pair_read_addr1;
    logic [7:0] remap_pair_read_addr2, remap_pair_read_addr3;
    logic remap_read_en, remap_write_en, remap_read_valid, remap_last_reg;
    logic [7:0] remap_write_addr_reg;
    logic [4:0] remap_write_group_reg;
    logic [3:0] remap_time;
    logic [4:0] remap_group;
    logic [3:0] remap_write_bits;
    logic [3:0] run_syndrome_bits;
    paper_gross144_s1w_syndrome_group_ram u_syndrome (
        .clk(clk), .read_en(syndrome_read_en), .read_addr(syndrome_read_addr),
        .read_data(syndrome_read_bits), .write_en(syndrome_write_valid && state == S_IDLE),
        .write_addr(syndrome_write_addr), .write_data(syndrome_write_bits)
    );

    // Three read replicas let the target-partition converter gather four
    // source nibbles in parallel. All four copies share the normal ingress
    // write; the extra cost is four narrow native DPBs, not a 936-FF mux net.
    paper_gross144_s1w_syndrome_group_ram u_syndrome_pair1 (
        .clk(clk), .read_en(remap_read_en), .read_addr(remap_pair_read_addr1),
        .read_data(syndrome_pair_read_bits1),
        .write_en(syndrome_write_valid && state == S_IDLE),
        .write_addr(syndrome_write_addr), .write_data(syndrome_write_bits)
    );
    paper_gross144_s1w_syndrome_group_ram u_syndrome_pair2 (
        .clk(clk), .read_en(remap_read_en), .read_addr(remap_pair_read_addr2),
        .read_data(syndrome_pair_read_bits2),
        .write_en(syndrome_write_valid && state == S_IDLE),
        .write_addr(syndrome_write_addr), .write_data(syndrome_write_bits)
    );
    paper_gross144_s1w_syndrome_group_ram u_syndrome_pair3 (
        .clk(clk), .read_en(remap_read_en), .read_addr(remap_pair_read_addr3),
        .read_data(syndrome_pair_read_bits3),
        .write_en(syndrome_write_valid && state == S_IDLE),
        .write_addr(syndrome_write_addr), .write_data(syndrome_write_bits)
    );
    paper_gross144_s1w_syndrome_group_ram u_standard_syndrome (
        .clk(clk), .read_en(state == S_RECORD_LOAD || state == S_START ||
                            state == S_RUN), .read_addr(syndrome_read_addr),
        .read_data(standard_syndrome_read_bits), .write_en(remap_write_en),
        .write_addr(remap_write_addr_reg), .write_data(remap_write_bits)
    );

    always_comb begin
        run_syndrome_bits = syndrome_read_bits;
        if (state == S_RUN && profile_reg >= 3'd6) begin
            run_syndrome_bits = standard_syndrome_read_bits;
        end
    end

    logic [3:0] active_syndrome_bits;
    logic [RECORD_WIDTH-1:0] active_record [0:3];
    logic [RECORD_WIDTH-1:0] active_new_record [0:3];
    logic [34:0] active_signs [0:3], signs_after_emit [0:3];
    logic [34:0] active_new_signs [0:3];
    logic [RECORD_WIDTH-1:0] pending_record [0:3];
    logic [9:0] pending_record_addr [0:3];
    logic pending_record_valid;
    logic [2:0] pending_write_lane;
    logic [2:0] record_issue_lane, record_response_lane;
    logic prefetch_active, prefetch_valid;
    logic [2:0] prefetch_issue_lane, prefetch_response_lane;

    logic [6:0] init_orbit;
    logic [3:0] init_pair;
    logic [9:0] record_clear_addr;
    logic record_clear_done, init_first;
    logic clear_target_hash, clear_correction_hash;
    logic target_load_valid, target_load_bit;
    logic [HASH_WIDTH-1:0] target_load_word;
    logic hash_emit_valid;
    logic [15:0] hash_emit_masks, hash_emit_flips;
    logic [16*HASH_WIDTH-1:0] hash_emit_words;
    logic [HASH_WIDTH-1:0] target_hash, correction_hash, residual_hash;
    logic hash_possible_zero;
    logic [3:0] emit_parity_reg, emit_parity_after;
    logic emit_parity_mismatch;
    logic sweep_parity_failed;
    logic sweep_hash_failed;
    paper_gross144_residual_hash #(.HASH_WIDTH(HASH_WIDTH)) u_hash (
        .clk(clk), .rst(rst), .clear_target(clear_target_hash),
        .clear_correction(clear_correction_hash),
        .target_load_valid(target_load_valid), .target_load_bit(target_load_bit),
        .target_load_word(target_load_word), .emit_valid(hash_emit_valid),
        .emit_masks(hash_emit_masks), .emit_hard_sign_flips(hash_emit_flips),
        .emit_column_words(hash_emit_words), .target_hash(target_hash),
        .correction_hash(correction_hash), .residual_hash(residual_hash),
        .possible_zero(hash_possible_zero)
    );

    logic [27:0] map_coordinates;
    // FAST_HANDOFF skips verify-coordinate muxing only when no exact replay
    // exists.  Production now forces replay; stale final-sweep coordinates
    // would otherwise make every verify read the same four checks.
    wire [6:0] map_coordinate0 = (state == S_VERIFY_RUN) ? active_verify_coordinate0 :
        active_schedule_coordinate0;
    wire [6:0] map_coordinate1 = (state == S_VERIFY_RUN) ? active_verify_coordinate1 :
        active_schedule_coordinate1;
    wire [6:0] map_coordinate2 = (state == S_VERIFY_RUN) ? active_verify_coordinate2 :
        active_schedule_coordinate2;
    wire [6:0] map_coordinate3 = (state == S_VERIFY_RUN) ? active_verify_coordinate3 :
        active_schedule_coordinate3;
    // Separate mux cones for the live scalar mapper.  Keeping these as
    // independent named nets avoids the vendor's packed conditional-net
    // commoning that aliases odd lanes during placement.
    (* syn_keep = 1 *) logic [6:0] physical_map_coordinate0, physical_map_coordinate1;
    (* syn_keep = 1 *) logic [6:0] physical_map_coordinate2, physical_map_coordinate3;
    always_comb begin
        physical_map_coordinate0 = (state == S_VERIFY_RUN) ?
                                    active_verify_coordinate0 : active_schedule_coordinate0;
    end
    always_comb begin
        physical_map_coordinate1 = (state == S_VERIFY_RUN) ?
                                    active_verify_coordinate1 : active_schedule_coordinate1;
    end
    always_comb begin
        physical_map_coordinate2 = (state == S_VERIFY_RUN) ?
                                    active_verify_coordinate2 : active_schedule_coordinate2;
    end
    always_comb begin
        physical_map_coordinate3 = (state == S_VERIFY_RUN) ?
                                    active_verify_coordinate3 : active_schedule_coordinate3;
    end
    logic [22:0] descriptor [0:3];
    logic [HASH_WIDTH-1:0] descriptor_hash [0:3];
    logic [3:0] descriptor_valid;
    logic [23:0] descriptor_edges;
    logic [27:0] descriptor_orbits, descriptor_anchors;
    // Keep mapper outputs in flat packed buses at the vendor boundary.  The
    // unpacked [lane][slot] nets were legal RTL but the GW2AR mapper collapsed
    // the upper lane pair onto lane 0 after synthesis.
    logic [31:0] mapped_bank_flat;
    logic [191:0] mapped_address_flat;
    logic [15:0] mapped_invalid_flat;
    logic [7:0] mapped_bank_lane0, mapped_bank_lane1;
    logic [7:0] mapped_bank_lane2, mapped_bank_lane3;
    logic [47:0] mapped_address_lane0, mapped_address_lane1;
    logic [47:0] mapped_address_lane2, mapped_address_lane3;
    logic [3:0] mapped_invalid_lane0, mapped_invalid_lane1;
    logic [3:0] mapped_invalid_lane2, mapped_invalid_lane3;
    logic [7:0] mapped_bank_calc_lane0, mapped_bank_calc_lane1;
    logic [7:0] mapped_bank_calc_lane2, mapped_bank_calc_lane3;
    logic [47:0] mapped_address_calc_lane0, mapped_address_calc_lane1;
    logic [47:0] mapped_address_calc_lane2, mapped_address_calc_lane3;
    logic [3:0] mapped_invalid_calc_lane0, mapped_invalid_calc_lane1;
    logic [3:0] mapped_invalid_calc_lane2, mapped_invalid_calc_lane3;
    // Scalar mapper endpoints.  Keep one physical net per lane/slot at the
    // Gowin boundary; generated packed part-selects were synthesized with
    // lane aliases even though their RTL simulation was correct.
    (* syn_keep = 1 *) logic [1:0] explicit_bank00, explicit_bank01, explicit_bank02, explicit_bank03;
    (* syn_keep = 1 *) logic [1:0] explicit_bank10, explicit_bank11, explicit_bank12, explicit_bank13;
    (* syn_keep = 1 *) logic [1:0] explicit_bank20, explicit_bank21, explicit_bank22, explicit_bank23;
    (* syn_keep = 1 *) logic [1:0] explicit_bank30, explicit_bank31, explicit_bank32, explicit_bank33;
    (* syn_keep = 1 *) logic [11:0] explicit_addr00, explicit_addr01, explicit_addr02, explicit_addr03;
    (* syn_keep = 1 *) logic [11:0] explicit_addr10, explicit_addr11, explicit_addr12, explicit_addr13;
    (* syn_keep = 1 *) logic [11:0] explicit_addr20, explicit_addr21, explicit_addr22, explicit_addr23;
    (* syn_keep = 1 *) logic [11:0] explicit_addr30, explicit_addr31, explicit_addr32, explicit_addr33;
    wire [1:0] mapped_bank [0:3][0:3];
    wire [11:0] mapped_address [0:3][0:3];
    wire mapped_invalid [0:3][0:3];

    localparam logic [1:0] TEMPLATE_GATHER = 2'd0;
    localparam logic [1:0] TEMPLATE_EMIT   = 2'd1;
    localparam logic [1:0] TEMPLATE_VERIFY = 2'd2;
    logic template_read_en, template_pending, template_consume;
    // Synchronous slot-ROM data must settle for one complete cycle before it
    // is used as an external emit descriptor.  Without this guard the first
    // emit after a gather can consume the previous gather row's descriptor
    // mask/edges while the engine output already belongs to emit beat zero.
    logic template_emit_wait_reg, template_emit_data_valid_reg;
    logic [6:0] template_read_addr;
    logic [4*(23+HASH_WIDTH)-1:0] template_read_data;
    logic [1:0] template_kind, template_request_kind;
    logic [3:0] template_beat, template_request_beat, template_request_time;
    logic emit_descriptor_valid;
    // Capture the synchronous emit row at the ROM wait boundary.  This
    // removes template-ROM output from the engine's wide posterior-update
    // path while preserving the existing one-row-per-cycle emit schedule.
    logic [3:0] emit_descriptor_valid_reg;
    logic [23:0] emit_descriptor_edges_reg;
    logic [27:0] emit_descriptor_orbits_reg;
    logic [27:0] emit_descriptor_anchors_reg;

    paper_gross144_s1w_template_rom #(
        .IMAGE_FILE(TEMPLATE_FILE),
        .SLOT_IMAGE_MODE(TEMPLATE_SLOT_IMAGE_MODE), .HASH_WIDTH(HASH_WIDTH),
        .SLOT0_FILE(TEMPLATE_SLOT0_FILE), .SLOT1_FILE(TEMPLATE_SLOT1_FILE),
        .SLOT2_FILE(TEMPLATE_SLOT2_FILE), .SLOT3_FILE(TEMPLATE_SLOT3_FILE)
    ) u_template_rom (
        .clk(clk), .read_en(template_read_en), .read_addr(template_read_addr),
        .read_data(template_read_data)
    );

    logic orbit_config_read_en, orbit_config_valid;
    logic [6:0] orbit_config_read_addr, orbit_config_tag;
    // Packed orbit configuration:
    // scale2[10:0], scale3[21:11], scale4[32:22], scale5[43:33],
    // scale1[54:44], colour[56:55], logical pattern[59:57].
    logic [59:0] orbit_config_data;
    logic logical_quad_read_en;
    logic [8:0] logical_quad_read_addr0, logical_quad_read_addr1;
    logic [47:0] logical_quad_read_data0, logical_quad_read_data1;

    paper_gross144_s1w_orbit_config_rom #(
        .IMAGE_FILE(ORBIT_CONFIG_FILE)
    ) u_orbit_config (
        .clk(clk), .read_en(orbit_config_read_en),
        .read_addr(orbit_config_read_addr), .read_data(orbit_config_data)
    );
    paper_gross144_s1w_logical_quad_rom #(
        .IMAGE_FILE(LOGICAL_QUADS_FILE),
        .IMAGE_FILE0(LOGICAL_QUADS_BANK0_FILE),
        .IMAGE_FILE1(LOGICAL_QUADS_BANK1_FILE),
        .IMAGE_FILE2(LOGICAL_QUADS_BANK2_FILE),
        .IMAGE_FILE3(LOGICAL_QUADS_BANK3_FILE)
    ) u_logical_quads (
        .clk(clk),
        .read0_en(logical_quad_read_en), .read0_addr(logical_quad_read_addr0),
        .read0_data(logical_quad_read_data0),
        .read1_en(logical_quad_read_en), .read1_addr(logical_quad_read_addr1),
        .read1_data(logical_quad_read_data1)
    );

    // Keep each lane's coordinate slice explicit.  Gowin's GW2AR mapper
    // folded the lane-indexed generate expression into pairs (0,0,2,2),
    // which changed the physical bank tags while remaining RTL-correct.
    genvar map_slot0, map_slot1, map_slot2, map_slot3;
    generate
        for (map_slot0 = 0; map_slot0 < 4; map_slot0 = map_slot0 + 1) begin : g_map_lane0
            gross144_component_banked_address u_address (
                .orbit_id(descriptor_orbits[map_slot0*7 +: 7]),
                .orbit_bank_color(descriptor[map_slot0][22:21]),
                .anchor_coordinate(descriptor_anchors[map_slot0*7 +: 7]),
                .check_coordinate(map_coordinate0),
                .bank(mapped_bank_lane0[map_slot0*2 +: 2]),
                .bank_address(mapped_address_lane0[map_slot0*12 +: 12]),
                .invalid(mapped_invalid_lane0[map_slot0])
            );
        end
        for (map_slot1 = 0; map_slot1 < 4; map_slot1 = map_slot1 + 1) begin : g_map_lane1
            gross144_component_banked_address u_address (
                .orbit_id(descriptor_orbits[map_slot1*7 +: 7]),
                .orbit_bank_color(descriptor[map_slot1][22:21]),
                .anchor_coordinate(descriptor_anchors[map_slot1*7 +: 7]),
                .check_coordinate(map_coordinate1),
                .bank(mapped_bank_lane1[map_slot1*2 +: 2]),
                .bank_address(mapped_address_lane1[map_slot1*12 +: 12]),
                .invalid(mapped_invalid_lane1[map_slot1])
            );
        end
        for (map_slot2 = 0; map_slot2 < 4; map_slot2 = map_slot2 + 1) begin : g_map_lane2
            gross144_component_banked_address u_address (
                .orbit_id(descriptor_orbits[map_slot2*7 +: 7]),
                .orbit_bank_color(descriptor[map_slot2][22:21]),
                .anchor_coordinate(descriptor_anchors[map_slot2*7 +: 7]),
                .check_coordinate(map_coordinate2),
                .bank(mapped_bank_lane2[map_slot2*2 +: 2]),
                .bank_address(mapped_address_lane2[map_slot2*12 +: 12]),
                .invalid(mapped_invalid_lane2[map_slot2])
            );
        end
        for (map_slot3 = 0; map_slot3 < 4; map_slot3 = map_slot3 + 1) begin : g_map_lane3
            gross144_component_banked_address u_address (
                .orbit_id(descriptor_orbits[map_slot3*7 +: 7]),
                .orbit_bank_color(descriptor[map_slot3][22:21]),
                .anchor_coordinate(descriptor_anchors[map_slot3*7 +: 7]),
                .check_coordinate(map_coordinate3),
                .bank(mapped_bank_lane3[map_slot3*2 +: 2]),
                .bank_address(mapped_address_lane3[map_slot3*12 +: 12]),
                .invalid(mapped_invalid_lane3[map_slot3])
            );
        end
    endgenerate
    // Explicit scalar copies used by the live request path.  Do not fold
    // these into the packed mapper buses above: the GW2AR synthesis release
    // used here has a lane-alias bug on repeated variable part-selects.
    gross144_component_banked_address u_map00_explicit (
        .orbit_id(descriptor_orbits[0 +: 7]), .orbit_bank_color(descriptor[0][22:21]),
        .anchor_coordinate(descriptor_anchors[0 +: 7]), .check_coordinate(physical_map_coordinate0),
        .bank(explicit_bank00), .bank_address(explicit_addr00), .invalid()
    );
    gross144_component_banked_address u_map01_explicit (
        .orbit_id(descriptor_orbits[7 +: 7]), .orbit_bank_color(descriptor[1][22:21]),
        .anchor_coordinate(descriptor_anchors[7 +: 7]), .check_coordinate(physical_map_coordinate0),
        .bank(explicit_bank01), .bank_address(explicit_addr01), .invalid()
    );
    gross144_component_banked_address u_map02_explicit (
        .orbit_id(descriptor_orbits[14 +: 7]), .orbit_bank_color(descriptor[2][22:21]),
        .anchor_coordinate(descriptor_anchors[14 +: 7]), .check_coordinate(physical_map_coordinate0),
        .bank(explicit_bank02), .bank_address(explicit_addr02), .invalid()
    );
    gross144_component_banked_address u_map03_explicit (
        .orbit_id(descriptor_orbits[21 +: 7]), .orbit_bank_color(descriptor[3][22:21]),
        .anchor_coordinate(descriptor_anchors[21 +: 7]), .check_coordinate(physical_map_coordinate0),
        .bank(explicit_bank03), .bank_address(explicit_addr03), .invalid()
    );
    gross144_component_banked_address u_map10_explicit (
        .orbit_id(descriptor_orbits[0 +: 7]), .orbit_bank_color(descriptor[0][22:21]),
        .anchor_coordinate(descriptor_anchors[0 +: 7]), .check_coordinate(physical_map_coordinate1),
        .bank(explicit_bank10), .bank_address(explicit_addr10), .invalid()
    );
    gross144_component_banked_address u_map11_explicit (
        .orbit_id(descriptor_orbits[7 +: 7]), .orbit_bank_color(descriptor[1][22:21]),
        .anchor_coordinate(descriptor_anchors[7 +: 7]), .check_coordinate(physical_map_coordinate1),
        .bank(explicit_bank11), .bank_address(explicit_addr11), .invalid()
    );
    gross144_component_banked_address u_map12_explicit (
        .orbit_id(descriptor_orbits[14 +: 7]), .orbit_bank_color(descriptor[2][22:21]),
        .anchor_coordinate(descriptor_anchors[14 +: 7]), .check_coordinate(physical_map_coordinate1),
        .bank(explicit_bank12), .bank_address(explicit_addr12), .invalid()
    );
    gross144_component_banked_address u_map13_explicit (
        .orbit_id(descriptor_orbits[21 +: 7]), .orbit_bank_color(descriptor[3][22:21]),
        .anchor_coordinate(descriptor_anchors[21 +: 7]), .check_coordinate(physical_map_coordinate1),
        .bank(explicit_bank13), .bank_address(explicit_addr13), .invalid()
    );
    gross144_component_banked_address u_map20_explicit (
        .orbit_id(descriptor_orbits[0 +: 7]), .orbit_bank_color(descriptor[0][22:21]),
        .anchor_coordinate(descriptor_anchors[0 +: 7]), .check_coordinate(physical_map_coordinate2),
        .bank(explicit_bank20), .bank_address(explicit_addr20), .invalid()
    );
    gross144_component_banked_address u_map21_explicit (
        .orbit_id(descriptor_orbits[7 +: 7]), .orbit_bank_color(descriptor[1][22:21]),
        .anchor_coordinate(descriptor_anchors[7 +: 7]), .check_coordinate(physical_map_coordinate2),
        .bank(explicit_bank21), .bank_address(explicit_addr21), .invalid()
    );
    gross144_component_banked_address u_map22_explicit (
        .orbit_id(descriptor_orbits[14 +: 7]), .orbit_bank_color(descriptor[2][22:21]),
        .anchor_coordinate(descriptor_anchors[14 +: 7]), .check_coordinate(physical_map_coordinate2),
        .bank(explicit_bank22), .bank_address(explicit_addr22), .invalid()
    );
    gross144_component_banked_address u_map23_explicit (
        .orbit_id(descriptor_orbits[21 +: 7]), .orbit_bank_color(descriptor[3][22:21]),
        .anchor_coordinate(descriptor_anchors[21 +: 7]), .check_coordinate(physical_map_coordinate2),
        .bank(explicit_bank23), .bank_address(explicit_addr23), .invalid()
    );
    gross144_component_banked_address u_map30_explicit (
        .orbit_id(descriptor_orbits[0 +: 7]), .orbit_bank_color(descriptor[0][22:21]),
        .anchor_coordinate(descriptor_anchors[0 +: 7]), .check_coordinate(physical_map_coordinate3),
        .bank(explicit_bank30), .bank_address(explicit_addr30), .invalid()
    );
    gross144_component_banked_address u_map31_explicit (
        .orbit_id(descriptor_orbits[7 +: 7]), .orbit_bank_color(descriptor[1][22:21]),
        .anchor_coordinate(descriptor_anchors[7 +: 7]), .check_coordinate(physical_map_coordinate3),
        .bank(explicit_bank31), .bank_address(explicit_addr31), .invalid()
    );
    gross144_component_banked_address u_map32_explicit (
        .orbit_id(descriptor_orbits[14 +: 7]), .orbit_bank_color(descriptor[2][22:21]),
        .anchor_coordinate(descriptor_anchors[14 +: 7]), .check_coordinate(physical_map_coordinate3),
        .bank(explicit_bank32), .bank_address(explicit_addr32), .invalid()
    );
    gross144_component_banked_address u_map33_explicit (
        .orbit_id(descriptor_orbits[21 +: 7]), .orbit_bank_color(descriptor[3][22:21]),
        .anchor_coordinate(descriptor_anchors[21 +: 7]), .check_coordinate(physical_map_coordinate3),
        .bank(explicit_bank33), .bank_address(explicit_addr33), .invalid()
    );
    // Inline fixed assignments are the board-safe mapper.  Gowin 1.9.11
    // incorrectly commoned repeated address-module instances by lane even
    // when their coordinate inputs differed.  These calls retain the same
    // arithmetic but give synthesis one explicit lane/slot cone each.
    always_comb begin
        mapped_bank_calc_lane0 = '0; mapped_bank_calc_lane1 = '0;
        mapped_bank_calc_lane2 = '0; mapped_bank_calc_lane3 = '0;
        mapped_address_calc_lane0 = '0; mapped_address_calc_lane1 = '0;
        mapped_address_calc_lane2 = '0; mapped_address_calc_lane3 = '0;
        mapped_invalid_calc_lane0 = '0; mapped_invalid_calc_lane1 = '0;
        mapped_invalid_calc_lane2 = '0; mapped_invalid_calc_lane3 = '0;

        mapped_bank_calc_lane0[1:0] = component_bank_inline(descriptor[0][22:21], descriptor_anchors[0 +: 7], map_coordinate0);
        mapped_bank_calc_lane0[3:2] = component_bank_inline(descriptor[1][22:21], descriptor_anchors[7 +: 7], map_coordinate0);
        mapped_bank_calc_lane0[5:4] = component_bank_inline(descriptor[2][22:21], descriptor_anchors[14 +: 7], map_coordinate0);
        mapped_bank_calc_lane0[7:6] = component_bank_inline(descriptor[3][22:21], descriptor_anchors[21 +: 7], map_coordinate0);
        mapped_bank_calc_lane1[1:0] = component_bank_inline(descriptor[0][22:21], descriptor_anchors[0 +: 7], map_coordinate1);
        mapped_bank_calc_lane1[3:2] = component_bank_inline(descriptor[1][22:21], descriptor_anchors[7 +: 7], map_coordinate1);
        mapped_bank_calc_lane1[5:4] = component_bank_inline(descriptor[2][22:21], descriptor_anchors[14 +: 7], map_coordinate1);
        mapped_bank_calc_lane1[7:6] = component_bank_inline(descriptor[3][22:21], descriptor_anchors[21 +: 7], map_coordinate1);
        mapped_bank_calc_lane2[1:0] = component_bank_inline(descriptor[0][22:21], descriptor_anchors[0 +: 7], map_coordinate2);
        mapped_bank_calc_lane2[3:2] = component_bank_inline(descriptor[1][22:21], descriptor_anchors[7 +: 7], map_coordinate2);
        mapped_bank_calc_lane2[5:4] = component_bank_inline(descriptor[2][22:21], descriptor_anchors[14 +: 7], map_coordinate2);
        mapped_bank_calc_lane2[7:6] = component_bank_inline(descriptor[3][22:21], descriptor_anchors[21 +: 7], map_coordinate2);
        mapped_bank_calc_lane3[1:0] = component_bank_inline(descriptor[0][22:21], descriptor_anchors[0 +: 7], map_coordinate3);
        mapped_bank_calc_lane3[3:2] = component_bank_inline(descriptor[1][22:21], descriptor_anchors[7 +: 7], map_coordinate3);
        mapped_bank_calc_lane3[5:4] = component_bank_inline(descriptor[2][22:21], descriptor_anchors[14 +: 7], map_coordinate3);
        mapped_bank_calc_lane3[7:6] = component_bank_inline(descriptor[3][22:21], descriptor_anchors[21 +: 7], map_coordinate3);

        mapped_address_calc_lane0[11:0] = component_address_inline(descriptor_orbits[0 +: 7], descriptor[0][22:21], descriptor_anchors[0 +: 7], map_coordinate0);
        mapped_address_calc_lane0[23:12] = component_address_inline(descriptor_orbits[7 +: 7], descriptor[1][22:21], descriptor_anchors[7 +: 7], map_coordinate0);
        mapped_address_calc_lane0[35:24] = component_address_inline(descriptor_orbits[14 +: 7], descriptor[2][22:21], descriptor_anchors[14 +: 7], map_coordinate0);
        mapped_address_calc_lane0[47:36] = component_address_inline(descriptor_orbits[21 +: 7], descriptor[3][22:21], descriptor_anchors[21 +: 7], map_coordinate0);
        mapped_address_calc_lane1[11:0] = component_address_inline(descriptor_orbits[0 +: 7], descriptor[0][22:21], descriptor_anchors[0 +: 7], map_coordinate1);
        mapped_address_calc_lane1[23:12] = component_address_inline(descriptor_orbits[7 +: 7], descriptor[1][22:21], descriptor_anchors[7 +: 7], map_coordinate1);
        mapped_address_calc_lane1[35:24] = component_address_inline(descriptor_orbits[14 +: 7], descriptor[2][22:21], descriptor_anchors[14 +: 7], map_coordinate1);
        mapped_address_calc_lane1[47:36] = component_address_inline(descriptor_orbits[21 +: 7], descriptor[3][22:21], descriptor_anchors[21 +: 7], map_coordinate1);
        mapped_address_calc_lane2[11:0] = component_address_inline(descriptor_orbits[0 +: 7], descriptor[0][22:21], descriptor_anchors[0 +: 7], map_coordinate2);
        mapped_address_calc_lane2[23:12] = component_address_inline(descriptor_orbits[7 +: 7], descriptor[1][22:21], descriptor_anchors[7 +: 7], map_coordinate2);
        mapped_address_calc_lane2[35:24] = component_address_inline(descriptor_orbits[14 +: 7], descriptor[2][22:21], descriptor_anchors[14 +: 7], map_coordinate2);
        mapped_address_calc_lane2[47:36] = component_address_inline(descriptor_orbits[21 +: 7], descriptor[3][22:21], descriptor_anchors[21 +: 7], map_coordinate2);
        mapped_address_calc_lane3[11:0] = component_address_inline(descriptor_orbits[0 +: 7], descriptor[0][22:21], descriptor_anchors[0 +: 7], map_coordinate3);
        mapped_address_calc_lane3[23:12] = component_address_inline(descriptor_orbits[7 +: 7], descriptor[1][22:21], descriptor_anchors[7 +: 7], map_coordinate3);
        mapped_address_calc_lane3[35:24] = component_address_inline(descriptor_orbits[14 +: 7], descriptor[2][22:21], descriptor_anchors[14 +: 7], map_coordinate3);
        mapped_address_calc_lane3[47:36] = component_address_inline(descriptor_orbits[21 +: 7], descriptor[3][22:21], descriptor_anchors[21 +: 7], map_coordinate3);

        mapped_invalid_calc_lane0 = {component_invalid_inline(descriptor_orbits[21 +: 7], descriptor_anchors[21 +: 7], map_coordinate0), component_invalid_inline(descriptor_orbits[14 +: 7], descriptor_anchors[14 +: 7], map_coordinate0), component_invalid_inline(descriptor_orbits[7 +: 7], descriptor_anchors[7 +: 7], map_coordinate0), component_invalid_inline(descriptor_orbits[0 +: 7], descriptor_anchors[0 +: 7], map_coordinate0)};
        mapped_invalid_calc_lane1 = {component_invalid_inline(descriptor_orbits[21 +: 7], descriptor_anchors[21 +: 7], map_coordinate1), component_invalid_inline(descriptor_orbits[14 +: 7], descriptor_anchors[14 +: 7], map_coordinate1), component_invalid_inline(descriptor_orbits[7 +: 7], descriptor_anchors[7 +: 7], map_coordinate1), component_invalid_inline(descriptor_orbits[0 +: 7], descriptor_anchors[0 +: 7], map_coordinate1)};
        mapped_invalid_calc_lane2 = {component_invalid_inline(descriptor_orbits[21 +: 7], descriptor_anchors[21 +: 7], map_coordinate2), component_invalid_inline(descriptor_orbits[14 +: 7], descriptor_anchors[14 +: 7], map_coordinate2), component_invalid_inline(descriptor_orbits[7 +: 7], descriptor_anchors[7 +: 7], map_coordinate2), component_invalid_inline(descriptor_orbits[0 +: 7], descriptor_anchors[0 +: 7], map_coordinate2)};
        mapped_invalid_calc_lane3 = {component_invalid_inline(descriptor_orbits[21 +: 7], descriptor_anchors[21 +: 7], map_coordinate3), component_invalid_inline(descriptor_orbits[14 +: 7], descriptor_anchors[14 +: 7], map_coordinate3), component_invalid_inline(descriptor_orbits[7 +: 7], descriptor_anchors[7 +: 7], map_coordinate3), component_invalid_inline(descriptor_orbits[0 +: 7], descriptor_anchors[0 +: 7], map_coordinate3)};
    end

    assign mapped_bank_flat = {mapped_bank_calc_lane3, mapped_bank_calc_lane2,
                               mapped_bank_calc_lane1, mapped_bank_calc_lane0};
    assign mapped_address_flat = {mapped_address_calc_lane3, mapped_address_calc_lane2,
                                  mapped_address_calc_lane1, mapped_address_calc_lane0};
    assign mapped_invalid_flat = {mapped_invalid_calc_lane3, mapped_invalid_calc_lane2,
                                  mapped_invalid_calc_lane1, mapped_invalid_calc_lane0};
    genvar map_alias_lane, map_alias_slot;
    generate
        for (map_alias_lane = 0; map_alias_lane < 4; map_alias_lane = map_alias_lane + 1)
            for (map_alias_slot = 0; map_alias_slot < 4; map_alias_slot = map_alias_slot + 1) begin : g_map_alias
                localparam integer MAP_INDEX = map_alias_lane*4 + map_alias_slot;
                assign mapped_bank[map_alias_lane][map_alias_slot] =
                    mapped_bank_flat[MAP_INDEX*2 +: 2];
                assign mapped_address[map_alias_lane][map_alias_slot] =
                    mapped_address_flat[MAP_INDEX*12 +: 12];
                assign mapped_invalid[map_alias_lane][map_alias_slot] =
                    mapped_invalid_flat[MAP_INDEX];
            end
    endgenerate

    logic [7:0] pair_request_valid [0:1];
    logic [15:0] pair_request_banks [0:1];
    logic [95:0] pair_request_addresses [0:1];
    logic signed [87:0] pair_request_write_data [0:1];
    logic [7:0] pair_request_port1 [0:1];
    logic [3:0] pair_port0_valid [0:1], pair_port1_valid [0:1];
    logic [47:0] pair_port0_address [0:1], pair_port1_address [0:1];
    logic signed [43:0] pair_port0_write_data [0:1], pair_port1_write_data [0:1];
    logic pair_mapping_error [0:1];
    logic [3:0] pair_mapping_conflict_banks [0:1];

    genvar pair_gen;
    generate
        for (pair_gen = 0; pair_gen < 2; pair_gen = pair_gen + 1) begin : g_pair_map
            paper_gross144_s1w_dual_port_lane_crossbar u_crossbar (
                .lane0_valid(pair_request_valid[pair_gen][3:0]),
                .lane0_banks(pair_request_banks[pair_gen][7:0]),
                .lane0_addresses(pair_request_addresses[pair_gen][47:0]),
                .lane0_write_data(pair_request_write_data[pair_gen][43:0]),
                .lane1_valid(pair_request_valid[pair_gen][7:4]),
                .lane1_banks(pair_request_banks[pair_gen][15:8]),
                .lane1_addresses(pair_request_addresses[pair_gen][95:48]),
                .lane1_write_data(pair_request_write_data[pair_gen][87:44]),
                .port0_valid(pair_port0_valid[pair_gen]),
                .port0_address(pair_port0_address[pair_gen]),
                .port0_write_data(pair_port0_write_data[pair_gen]),
                .port1_valid(pair_port1_valid[pair_gen]),
                .port1_address(pair_port1_address[pair_gen]),
                .port1_write_data(pair_port1_write_data[pair_gen]),
                .mapping_error(pair_mapping_error[pair_gen]),
                .mapping_conflict_banks(pair_mapping_conflict_banks[pair_gen])
            );
            always_comb
                pair_request_port1[pair_gen] = {
                    pair_request_valid[pair_gen][7:4], 4'b0000
                };
        end
    endgenerate

    logic mem_port0_read [0:1][0:3], mem_port0_write [0:1][0:3];
    logic mem_port1_read [0:1][0:3], mem_port1_write [0:1][0:3];
    logic [11:0] mem_port0_addr [0:1][0:3], mem_port1_addr [0:1][0:3];
    logic mem_port0_write_q [0:1][0:3], mem_port1_write_q [0:1][0:3];
    logic [11:0] mem_port0_write_addr_q [0:1][0:3],
                 mem_port1_write_addr_q [0:1][0:3];
    logic signed [10:0] mem_port0_write_data [0:1][0:3],
                              mem_port1_write_data [0:1][0:3];
    logic signed [10:0] mem_port0_write_data_q [0:1][0:3],
                              mem_port1_write_data_q [0:1][0:3];
    logic signed [10:0] mem_port0_read_data [0:1][0:3],
                              mem_port1_read_data [0:1][0:3];
    logic mem_write_conflict [0:1][0:3];
    logic mem_port0_write_commit [0:1][0:3], mem_port1_write_commit [0:1][0:3];
    genvar replica_gen, bank_gen;
    generate
        for (replica_gen = 0; replica_gen < 2; replica_gen = replica_gen + 1)
            for (bank_gen = 0; bank_gen < 4; bank_gen = bank_gen + 1) begin : g_memory
                paper_gross144_s1w_posterior_bank_dual u_bank (
                    .clk(clk), .rst(rst),
                    .port0_read_en(mem_port0_read[replica_gen][bank_gen]),
                    .port0_write_en(mem_port0_write_q[replica_gen][bank_gen]),
                    .port0_addr(mem_port0_addr[replica_gen][bank_gen]),
                    .port0_write_addr(mem_port0_write_addr_q[replica_gen][bank_gen]),
                    .port0_write_data(mem_port0_write_data_q[replica_gen][bank_gen]),
                    .port0_read_data(mem_port0_read_data[replica_gen][bank_gen]),
                    .port1_read_en(mem_port1_read[replica_gen][bank_gen]),
                    .port1_write_en(mem_port1_write_q[replica_gen][bank_gen]),
                    .port1_addr(mem_port1_addr[replica_gen][bank_gen]),
                    .port1_write_addr(mem_port1_write_addr_q[replica_gen][bank_gen]),
                    .port1_write_data(mem_port1_write_data_q[replica_gen][bank_gen]),
                    .port1_read_data(mem_port1_read_data[replica_gen][bank_gen]),
                    .write_conflict(mem_write_conflict[replica_gen][bank_gen]),
                    .port0_write_commit(mem_port0_write_commit[replica_gen][bank_gen]),
                    .port1_write_commit(mem_port1_write_commit[replica_gen][bank_gen])
                );
            end
    endgenerate

    logic pair0_start_ready, pair1_start_ready, engine_start_valid;
    logic pair0_gather_ready, pair1_gather_ready;
    logic pair0_emit_valid, pair1_emit_valid, engine_emit_ready;
    logic pair0_emit_last, pair1_emit_last;
    logic pair0_done, pair1_done;
    logic pair0_error, pair1_error, pair0_lockstep, pair1_lockstep;
    logic pair0_busy, pair1_busy;
    logic [3:0] engine_gather_valid [0:3], engine_gather_old_signs [0:3];
    logic [3:0] engine_emit_old_signs [0:3];
    logic [23:0] engine_gather_edges [0:3];
    logic [27:0] engine_gather_orbits [0:3], engine_gather_anchors [0:3];
    logic signed [43:0] engine_gather_posteriors [0:3];
    logic [3:0] engine_out_valid_mask [0:3], engine_out_new_signs [0:3],
                engine_out_flips [0:3];
    logic [23:0] engine_out_edges [0:3];
    logic [27:0] engine_out_orbits [0:3], engine_out_anchors [0:3];
    logic signed [43:0] engine_out_posteriors [0:3];
    logic [MESSAGE_MAGNITUDE_BITS-1:0] engine_new_min1 [0:3], engine_new_min2 [0:3];
    logic [5:0] engine_new_argmin [0:3];
    // Keep the result endpoints scalar through the Gowin wrapper boundary.
    // The packed/unpacked array form is convenient in RTL but has previously
    // allowed GW2AR synthesis to alias a lane's late min2/result net.
    logic [MESSAGE_MAGNITUDE_BITS-1:0] engine_new_min1_0, engine_new_min2_0;
    logic [5:0] engine_new_argmin_0;
    logic [MESSAGE_MAGNITUDE_BITS-1:0] engine_new_min1_1, engine_new_min2_1;
    logic [5:0] engine_new_argmin_1;
    logic [MESSAGE_MAGNITUDE_BITS-1:0] engine_new_min1_2, engine_new_min2_2;
    logic [5:0] engine_new_argmin_2;
    logic [MESSAGE_MAGNITUDE_BITS-1:0] engine_new_min1_3, engine_new_min2_3;
    logic [5:0] engine_new_argmin_3;
    assign engine_new_min1[0] = engine_new_min1_0;
    assign engine_new_min2[0] = engine_new_min2_0;
    assign engine_new_argmin[0] = engine_new_argmin_0;
    assign engine_new_min1[1] = engine_new_min1_1;
    assign engine_new_min2[1] = engine_new_min2_1;
    assign engine_new_argmin[1] = engine_new_argmin_1;
    assign engine_new_min1[2] = engine_new_min1_2;
    assign engine_new_min2[2] = engine_new_min2_2;
    assign engine_new_argmin[2] = engine_new_argmin_2;
    assign engine_new_min1[3] = engine_new_min1_3;
    assign engine_new_min2[3] = engine_new_min2_3;
    assign engine_new_argmin[3] = engine_new_argmin_3;

    paper_gross144_s1w_paired_check_engine #(
        .MESSAGE_MAGNITUDE_BITS(MESSAGE_MAGNITUDE_BITS), .RUNTIME_CONFIG(1),
        .FAST_PATH(FAST_HANDOFF)
    ) u_pair0 (
        .clk(clk), .rst(rst), .batch_start_valid(engine_start_valid),
        .batch_start_ready(pair0_start_ready),
        .config_correction_shift(profile_correction_shift),
        .config_message_max(profile_message_max),
        .lane0_degree(meta_rom[active_schedule_time][9:4]),
        .lane0_syndrome_bit(run_syndrome_bits[0]),
        .lane0_old_min1((RELAY_MODE && relay_phase_reg) ? active_new_record[0][MESSAGE_MAGNITUDE_BITS-1:0] : active_record[0][MESSAGE_MAGNITUDE_BITS-1:0]),
        .lane0_old_min2((RELAY_MODE && relay_phase_reg) ? active_new_record[0][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS] : active_record[0][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS]),
        .lane0_old_argmin((RELAY_MODE && relay_phase_reg) ? active_new_record[0][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS] : active_record[0][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS]),
        .scatter_mode(RELAY_MODE && relay_phase_reg),
        .lane0_scatter_old_min1(active_record[0][MESSAGE_MAGNITUDE_BITS-1:0]), .lane0_scatter_old_min2(active_record[0][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS]),
        .lane0_scatter_old_argmin(active_record[0][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS]), .lane0_scatter_old_signs(active_signs[0]),
        .lane1_scatter_old_min1(active_record[1][MESSAGE_MAGNITUDE_BITS-1:0]), .lane1_scatter_old_min2(active_record[1][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS]),
        .lane1_scatter_old_argmin(active_record[1][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS]), .lane1_scatter_old_signs(active_signs[1]),
        .lane1_degree(meta_rom[active_schedule_time][9:4]),
        .lane1_syndrome_bit(run_syndrome_bits[1]),
        .lane1_old_min1((RELAY_MODE && relay_phase_reg) ? active_new_record[1][MESSAGE_MAGNITUDE_BITS-1:0] : active_record[1][MESSAGE_MAGNITUDE_BITS-1:0]),
        .lane1_old_min2((RELAY_MODE && relay_phase_reg) ? active_new_record[1][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS] : active_record[1][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS]),
        .lane1_old_argmin((RELAY_MODE && relay_phase_reg) ? active_new_record[1][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS] : active_record[1][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS]),
        .lane0_gather_valid(engine_gather_valid[0]),
        .lane0_gather_edge_indices(engine_gather_edges[0]),
        .lane0_gather_posteriors(engine_gather_posteriors[0]),
        .lane0_gather_old_signs(engine_gather_old_signs[0]),
        .lane0_gather_orbits(engine_gather_orbits[0]),
        .lane0_gather_anchors(engine_gather_anchors[0]),
        .lane1_gather_valid(engine_gather_valid[1]),
        .lane1_gather_edge_indices(engine_gather_edges[1]),
        .lane1_gather_posteriors(engine_gather_posteriors[1]),
        .lane1_gather_old_signs(engine_gather_old_signs[1]),
        .lane1_gather_orbits(engine_gather_orbits[1]),
        .lane1_gather_anchors(engine_gather_anchors[1]),
        .batch_gather_ready(pair0_gather_ready),
        .batch_emit_valid(pair0_emit_valid), .batch_emit_ready(engine_emit_ready),
        .lane0_emit_old_signs(engine_emit_old_signs[0]),
        .lane0_external_emit_valid_mask(emit_descriptor_valid_reg),
        .lane0_external_emit_edge_indices(emit_descriptor_edges_reg),
        .lane0_external_emit_orbits(emit_descriptor_orbits_reg),
        .lane0_external_emit_anchors(emit_descriptor_anchors_reg),
        .lane0_out_valid_mask(engine_out_valid_mask[0]),
        .lane0_out_edge_indices(engine_out_edges[0]),
        .lane0_out_orbits(engine_out_orbits[0]), .lane0_out_anchors(engine_out_anchors[0]),
        .lane0_out_posteriors(engine_out_posteriors[0]),
        .lane0_out_new_signs(engine_out_new_signs[0]),
        .lane0_out_hard_sign_flips(engine_out_flips[0]),
        .lane1_emit_old_signs(engine_emit_old_signs[1]),
        .lane1_external_emit_valid_mask(emit_descriptor_valid_reg),
        .lane1_external_emit_edge_indices(emit_descriptor_edges_reg),
        .lane1_external_emit_orbits(emit_descriptor_orbits_reg),
        .lane1_external_emit_anchors(emit_descriptor_anchors_reg),
        .lane1_out_valid_mask(engine_out_valid_mask[1]),
        .lane1_out_edge_indices(engine_out_edges[1]),
        .lane1_out_orbits(engine_out_orbits[1]), .lane1_out_anchors(engine_out_anchors[1]),
        .lane1_out_posteriors(engine_out_posteriors[1]),
        .lane1_out_new_signs(engine_out_new_signs[1]),
        .lane1_out_hard_sign_flips(engine_out_flips[1]),
        .batch_emit_last(pair0_emit_last), .batch_done_valid(pair0_done),
        .batch_done_ready(1'b1), .lane0_new_min1(engine_new_min1_0),
        .lane0_new_min2(engine_new_min2_0), .lane0_new_argmin(engine_new_argmin_0),
        .lane1_new_min1(engine_new_min1_1), .lane1_new_min2(engine_new_min2_1),
        .lane1_new_argmin(engine_new_argmin_1), .image_error(pair0_error),
        .lockstep_error(pair0_lockstep), .busy(pair0_busy)
    );

    paper_gross144_s1w_paired_check_engine #(
        .MESSAGE_MAGNITUDE_BITS(MESSAGE_MAGNITUDE_BITS), .RUNTIME_CONFIG(1),
        .FAST_PATH(FAST_HANDOFF)
    ) u_pair1 (
        .clk(clk), .rst(rst), .batch_start_valid(engine_start_valid),
        .batch_start_ready(pair1_start_ready),
        .config_correction_shift(profile_correction_shift),
        .config_message_max(profile_message_max),
        .lane0_degree(meta_rom[active_schedule_time][9:4]),
        .lane0_syndrome_bit(run_syndrome_bits[2]),
        .lane0_old_min1((RELAY_MODE && relay_phase_reg) ? active_new_record[2][MESSAGE_MAGNITUDE_BITS-1:0] : active_record[2][MESSAGE_MAGNITUDE_BITS-1:0]),
        .lane0_old_min2((RELAY_MODE && relay_phase_reg) ? active_new_record[2][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS] : active_record[2][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS]),
        .lane0_old_argmin((RELAY_MODE && relay_phase_reg) ? active_new_record[2][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS] : active_record[2][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS]),
        .scatter_mode(RELAY_MODE && relay_phase_reg),
        .lane0_scatter_old_min1(active_record[2][MESSAGE_MAGNITUDE_BITS-1:0]), .lane0_scatter_old_min2(active_record[2][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS]),
        .lane0_scatter_old_argmin(active_record[2][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS]), .lane0_scatter_old_signs(active_signs[2]),
        .lane1_scatter_old_min1(active_record[3][MESSAGE_MAGNITUDE_BITS-1:0]), .lane1_scatter_old_min2(active_record[3][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS]),
        .lane1_scatter_old_argmin(active_record[3][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS]), .lane1_scatter_old_signs(active_signs[3]),
        .lane1_degree(meta_rom[active_schedule_time][9:4]),
        .lane1_syndrome_bit(run_syndrome_bits[3]),
        .lane1_old_min1((RELAY_MODE && relay_phase_reg) ? active_new_record[3][MESSAGE_MAGNITUDE_BITS-1:0] : active_record[3][MESSAGE_MAGNITUDE_BITS-1:0]),
        .lane1_old_min2((RELAY_MODE && relay_phase_reg) ? active_new_record[3][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS] : active_record[3][2*MESSAGE_MAGNITUDE_BITS-1:MESSAGE_MAGNITUDE_BITS]),
        .lane1_old_argmin((RELAY_MODE && relay_phase_reg) ? active_new_record[3][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS] : active_record[3][2*MESSAGE_MAGNITUDE_BITS+5:2*MESSAGE_MAGNITUDE_BITS]),
        .lane0_gather_valid(engine_gather_valid[2]),
        .lane0_gather_edge_indices(engine_gather_edges[2]),
        .lane0_gather_posteriors(engine_gather_posteriors[2]),
        .lane0_gather_old_signs(engine_gather_old_signs[2]),
        .lane0_gather_orbits(engine_gather_orbits[2]),
        .lane0_gather_anchors(engine_gather_anchors[2]),
        .lane1_gather_valid(engine_gather_valid[3]),
        .lane1_gather_edge_indices(engine_gather_edges[3]),
        .lane1_gather_posteriors(engine_gather_posteriors[3]),
        .lane1_gather_old_signs(engine_gather_old_signs[3]),
        .lane1_gather_orbits(engine_gather_orbits[3]),
        .lane1_gather_anchors(engine_gather_anchors[3]),
        .batch_gather_ready(pair1_gather_ready),
        .batch_emit_valid(pair1_emit_valid), .batch_emit_ready(engine_emit_ready),
        .lane0_emit_old_signs(engine_emit_old_signs[2]),
        .lane0_external_emit_valid_mask(emit_descriptor_valid_reg),
        .lane0_external_emit_edge_indices(emit_descriptor_edges_reg),
        .lane0_external_emit_orbits(emit_descriptor_orbits_reg),
        .lane0_external_emit_anchors(emit_descriptor_anchors_reg),
        .lane0_out_valid_mask(engine_out_valid_mask[2]),
        .lane0_out_edge_indices(engine_out_edges[2]),
        .lane0_out_orbits(engine_out_orbits[2]), .lane0_out_anchors(engine_out_anchors[2]),
        .lane0_out_posteriors(engine_out_posteriors[2]),
        .lane0_out_new_signs(engine_out_new_signs[2]),
        .lane0_out_hard_sign_flips(engine_out_flips[2]),
        .lane1_emit_old_signs(engine_emit_old_signs[3]),
        .lane1_external_emit_valid_mask(emit_descriptor_valid_reg),
        .lane1_external_emit_edge_indices(emit_descriptor_edges_reg),
        .lane1_external_emit_orbits(emit_descriptor_orbits_reg),
        .lane1_external_emit_anchors(emit_descriptor_anchors_reg),
        .lane1_out_valid_mask(engine_out_valid_mask[3]),
        .lane1_out_edge_indices(engine_out_edges[3]),
        .lane1_out_orbits(engine_out_orbits[3]), .lane1_out_anchors(engine_out_anchors[3]),
        .lane1_out_posteriors(engine_out_posteriors[3]),
        .lane1_out_new_signs(engine_out_new_signs[3]),
        .lane1_out_hard_sign_flips(engine_out_flips[3]),
        .batch_emit_last(pair1_emit_last), .batch_done_valid(pair1_done),
        .batch_done_ready(1'b1), .lane0_new_min1(engine_new_min1_2),
        .lane0_new_min2(engine_new_min2_2), .lane0_new_argmin(engine_new_argmin_2),
        .lane1_new_min1(engine_new_min1_3), .lane1_new_min2(engine_new_min2_3),
        .lane1_new_argmin(engine_new_argmin_3), .image_error(pair1_error),
        .lockstep_error(pair1_lockstep), .busy(pair1_busy)
    );

    logic [3:0] gather_issue_beat, emit_beat;
    logic gather_issue_valid, gather_response_valid;
    logic read_pending_reg;
    logic read_request_valid, read_request_verify, read_request_last;
    logic [3:0] read_request_valid_mask;
    logic [23:0] read_request_edges;
    logic [27:0] read_request_orbits, read_request_anchors;
    logic [1:0] read_request_bank [0:3][0:3];
    logic [11:0] read_request_address [0:3][0:3];
    (* syn_keep = 1 *) logic [7:0] read_request_bank_lane0, read_request_bank_lane1;
    (* syn_keep = 1 *) logic [7:0] read_request_bank_lane2, read_request_bank_lane3;
    (* syn_keep = 1 *) logic [47:0] read_request_address_lane0, read_request_address_lane1;
    (* syn_keep = 1 *) logic [47:0] read_request_address_lane2, read_request_address_lane3;
    logic [31:0] read_request_bank_flat;
    logic [191:0] read_request_address_flat;
    logic read_response_valid_stage, read_response_verify_stage,
          read_response_last_stage;
    logic [3:0] read_response_valid_mask_stage;
    logic [23:0] read_response_edges_stage;
    logic [27:0] read_response_orbits_stage, read_response_anchors_stage;
    logic [15:0] read_response_banks_stage [0:1];
    logic [7:0] read_response_port1_stage [0:1];
    logic read_response_valid_stage2, read_response_verify_stage2,
          read_response_last_stage2;
    logic [3:0] read_response_valid_mask_stage2;
    logic [23:0] read_response_edges_stage2;
    logic [27:0] read_response_orbits_stage2, read_response_anchors_stage2;
    logic [15:0] read_response_banks_stage2 [0:1];
    logic [7:0] read_response_port1_stage2 [0:1];
    logic read_response_valid_stage3, read_response_verify_stage3,
          read_response_last_stage3;
    logic [3:0] read_response_valid_mask_stage3;
    logic [23:0] read_response_edges_stage3;
    logic [27:0] read_response_orbits_stage3, read_response_anchors_stage3;
    logic [15:0] read_response_banks_stage3 [0:1];
    logic [7:0] read_response_port1_stage3 [0:1];
    // Streaming native-DPB reads need registered data matching response
    // metadata. Four stages keep READ_RESPONSE_STAGES=1..3 aligned.
    logic signed [10:0] posterior_data_port0_stage1 [0:1][0:3];
    logic signed [10:0] posterior_data_port0_stage2 [0:1][0:3];
    logic signed [10:0] posterior_data_port0_stage3 [0:1][0:3];
    logic signed [10:0] posterior_data_port0_stage4 [0:1][0:3];
    logic signed [10:0] posterior_data_port1_stage1 [0:1][0:3];
    logic signed [10:0] posterior_data_port1_stage2 [0:1][0:3];
    logic signed [10:0] posterior_data_port1_stage3 [0:1][0:3];
    logic signed [10:0] posterior_data_port1_stage4 [0:1][0:3];
    logic signed [10:0] response_posterior_port0 [0:1][0:3];
    logic signed [10:0] response_posterior_port1 [0:1][0:3];
    logic [3:0] response_valid_mask;
    logic [23:0] response_edges;
    logic [27:0] response_orbits, response_anchors;
    logic [15:0] response_banks [0:1];
    logic [7:0] response_port1 [0:1];
    logic emit_phase;
    logic emit_capture_valid, emit_advance, emit_commit;
    logic emit_buffer_valid, emit_buffer_last;
    // Physical emit request image.  Keep the live write path in flat buses
    // with constant lane/slot slices; Gowin previously folded unpacked lane
    // arrays after the gather-side alias was fixed.
    logic [15:0] emit_buffer_valid_mask_flat;
    logic [15:0] emit_buffer_new_signs_flat;
    logic [95:0] emit_buffer_edges_flat;
    logic [175:0] emit_buffer_posteriors_flat;
    logic [31:0] emit_buffer_bank_flat;
    logic [191:0] emit_buffer_address_flat;
    logic [15:0] capture_hash_masks, capture_hash_flips;
    logic [16*HASH_WIDTH-1:0] capture_hash_words;
    logic [15:0] emit_buffer_hash_masks, emit_buffer_hash_flips;
    logic [16*HASH_WIDTH-1:0] emit_buffer_hash_words;

    logic verify_issue_valid, verify_response_valid, verify_response_last;
    logic [3:0] verify_issue_beat;
    logic [3:0] verify_parity, verify_parity_after;
    logic verify_failed;
    // A zero detector word has the identity correction by definition. Track
    // this during ingress so the common no-error shot bypasses all posterior
    // initialization, flooding, and exact replay work.
    logic syndrome_nonzero;

    logic [6:0] logical_orbit;
    logic [4:0] logical_local;
    logic logical_issue_valid, logical_response_valid, logical_response_last;
    logic logical_response_valid_stage, logical_response_last_stage;
    logic [6:0] logical_response_orbit_stage;
    logic [4:0] logical_response_local_stage;
    logic logical_response_valid_stage2, logical_response_last_stage2;
    logic [6:0] logical_response_orbit_stage2;
    logic [4:0] logical_response_local_stage2;
    logic logical_all_issued;
    logic [6:0] logical_response_orbit;
    logic [4:0] logical_response_local;
    logic [11:0] logical_accum, logical_accum_after;
    // Logical scan response tags already spend one register stage. Native
    // posterior/quad memories update their outputs on the same edge as the
    // request, so consume an explicit data stage with those tags. Without it
    // the first logical word was dropped and the last word was consumed twice.
    logic signed [43:0] logical_mem_port0_stage, logical_mem_port1_stage;
    logic [47:0] logical_quad_data0_stage, logical_quad_data1_stage;
    logic signed [43:0] logical_mem_port0_stage2, logical_mem_port1_stage2;
    logic [47:0] logical_quad_data0_stage2, logical_quad_data1_stage2;
    wire signed [43:0] logical_mem_port0_selected =
        (LOGICAL_READ_RESPONSE_STAGES <= 1) ? logical_mem_port0_stage : logical_mem_port0_stage2;
    wire signed [43:0] logical_mem_port1_selected =
        (LOGICAL_READ_RESPONSE_STAGES <= 1) ? logical_mem_port1_stage : logical_mem_port1_stage2;
    wire [47:0] logical_quad_data0_selected =
        (LOGICAL_READ_RESPONSE_STAGES <= 1) ? logical_quad_data0_stage : logical_quad_data0_stage2;
    wire [47:0] logical_quad_data1_selected =
        (LOGICAL_READ_RESPONSE_STAGES <= 1) ? logical_quad_data1_stage : logical_quad_data1_stage2;
    logic structural_error_pending;
    logic guard_schedule_error, guard_engine_error;
    logic guard_mapping_error, guard_memory_error;
    logic guard_mapping_pair0, guard_mapping_pair1, guard_mapping_invalid;
    logic debug_fault_latched;
    logic [3:0] debug_fault_time_reg;
    logic [4:0] debug_fault_group_reg;
    logic [3:0] debug_fault_beat_reg;
    logic [7:0] debug_fault_conflict_banks_reg;
    logic [23:0] debug_fault_addr0_reg, debug_fault_addr1_reg;
    logic [12:0] debug_invalid_state_reg;
    logic debug_default_hit;
    logic [22:0] debug_fault_descriptor0_reg, debug_fault_descriptor1_reg;
    logic [31:0] debug_fault_banks_reg;
    logic [31:0] debug_gather_digest_reg;
    logic [31:0] debug_early_gathers_reg;
    logic [31:0] debug_early_raw0_reg, debug_early_raw1_reg;
    logic [15:0] debug_early_response_banks_reg;
    logic [7:0] debug_early_response_port1_reg;
    logic [31:0] debug_early_map_banks_reg;
    logic [27:0] debug_early_map_coordinates_reg;
    logic [8:0] debug_gather_count_reg;
    logic debug_early_issue_seen_reg, debug_early_response_seen_reg;
    logic debug_record_seen_reg;
    logic [15:0] debug_gather_checkpoint0_reg;
    logic [15:0] debug_gather_checkpoint1_reg;
    logic debug_gather_seen_reg;
    logic debug_post_emit_pending_reg, debug_post_emit_done_reg;
    logic [31:0] debug_post_emit_digest_reg;
    logic debug_watch_active_reg, debug_watch_done_reg;
    logic debug_watch_hit0_reg, debug_watch_hit1_reg;
    logic [1:0] debug_watch_bank0_reg, debug_watch_bank1_reg;
    logic [11:0] debug_watch_addr0_reg, debug_watch_addr1_reg;
    logic signed [10:0] debug_watch_data0_reg, debug_watch_data1_reg;
    logic signed [10:0] debug_watch_read0_reg, debug_watch_read1_reg;
    logic [10:0] debug_init_value0_reg, debug_init_value1_reg;
    logic signed [10:0] debug_target_write0_reg, debug_target_write1_reg;
    logic [31:0] debug_verify_digest_reg;
    logic [3:0] debug_verify_parity_reg;
    logic [3:0] debug_verify_syndrome_reg;
    logic [7:0] debug_verify_address_reg;
    logic [31:0] debug_gather_fold, debug_verify_fold;
    logic memory_error_now;
    logic mapped_invalid_now, mapping_error_stage;

    integer lane_index, slot_index, bank_index, replica_index, request_index;
    integer guard_replica, guard_bank, invalid_lane, invalid_slot;
    integer seq_lane, seq_bank, seq_replica, debug_pair, debug_bank;
    integer parity_lane_idx, parity_slot_idx;
    integer debug_digest_lane, debug_digest_slot;

    // Read-side diagnostics are intentionally separate from the decoder
    // decision. They make a physical DPB/latency mismatch observable without
    // changing the S1W datapath or its acceptance policy.
    // The wide diagnostic hash multipliers are intentionally disabled in the
    // production-capacity image.  They do not participate in decode
    // acceptance and consumed the final placement margin needed by the
    // first-emit qualification tap.  The compact low-byte XOR below remains
    // as a physical DPB qualification digest.
    always_comb begin
        debug_gather_fold = {
            engine_gather_posteriors[3][7:0],
            engine_gather_posteriors[2][7:0],
            engine_gather_posteriors[1][7:0],
            engine_gather_posteriors[0][7:0]
        };
        debug_verify_fold = '0;
    end

    // Accumulate parity from the registered emit buffer, not the live engine
    // output. The buffer is the exact writeback image; using live output can
    // cause false zero-residual accepts on long-tail cases.
    always_comb begin
        emit_parity_after = emit_parity_reg;
        if (emit_commit)
            for (parity_lane_idx = 0; parity_lane_idx < 4; parity_lane_idx = parity_lane_idx + 1)
                for (parity_slot_idx = 0; parity_slot_idx < 4; parity_slot_idx = parity_slot_idx + 1)
                    if (emit_buffer_valid_mask_flat[parity_lane_idx*4 + parity_slot_idx])
                        emit_parity_after[parity_lane_idx] =
                            emit_parity_after[parity_lane_idx] ^
                            emit_buffer_posteriors_flat[
                                (parity_lane_idx*4 + parity_slot_idx)*11 + 10];
        emit_parity_mismatch =
            (emit_parity_after[0] != active_syndrome_bits[0]) ||
            (emit_parity_after[1] != active_syndrome_bits[1]) ||
            (emit_parity_after[2] != active_syndrome_bits[2]) ||
            (emit_parity_after[3] != active_syndrome_bits[3]);
    end

    // Keep proof guards off the main controller next-state cone and partition
    // their write cones.  One monolithic guard made state/address signals fan
    // through a large OR tree into a global register.  These sticky classes
    // preserve every error until the common four-input read-side check.
    generate
        if (ENABLE_MEMORY_GUARD) begin : g_memory_guard
            always_comb begin
                memory_error_now = 1'b0;
                for (guard_replica = 0; guard_replica < 2; guard_replica = guard_replica + 1)
                    for (guard_bank = 0; guard_bank < 4; guard_bank = guard_bank + 1)
                        memory_error_now = memory_error_now ||
                                           mem_write_conflict[guard_replica][guard_bank];
            end
        end else begin : g_memory_guard_off
            always_comb memory_error_now = 1'b0;
        end
    endgenerate
    assign structural_error_pending = guard_schedule_error || guard_engine_error ||
                                      guard_mapping_error || guard_memory_error;
    assign debug_state = state;
    assign debug_guards = {guard_memory_error, guard_mapping_error,
                           guard_engine_error, guard_schedule_error};
    generate
        if (ENABLE_DEBUG) begin : g_debug_outputs
            assign debug_map_detail = {guard_mapping_invalid, guard_mapping_pair1,
                                       guard_mapping_pair0, debug_fault_latched,
                                       debug_default_hit, (debug_invalid_state_reg != 0),
                                       1'b0, structural_error_pending};
            assign debug_fault_time = debug_fault_time_reg;
            assign debug_fault_group = debug_fault_group_reg;
            assign debug_fault_beat = debug_fault_beat_reg;
            assign debug_fault_conflict_banks = debug_fault_conflict_banks_reg;
            assign debug_fault_addr0 = debug_fault_addr0_reg;
            assign debug_fault_addr1 = debug_fault_addr1_reg;
            assign debug_invalid_state = debug_invalid_state_reg;
            assign debug_fault_descriptor0 = debug_fault_descriptor0_reg;
            assign debug_fault_descriptor1 = debug_fault_descriptor1_reg;
            assign debug_fault_banks = debug_fault_banks_reg;
            assign debug_gather_digest = debug_gather_digest_reg;
            assign debug_gather_count = debug_gather_count_reg[7:0];
            assign debug_early_gathers = debug_early_gathers_reg;
            assign debug_early_raw0 = debug_early_raw0_reg;
            assign debug_early_raw1 = debug_early_raw1_reg;
            assign debug_early_response_banks = debug_early_response_banks_reg;
            assign debug_early_response_port1 = debug_early_response_port1_reg;
            assign debug_early_map_banks = debug_early_map_banks_reg;
            assign debug_early_map_coordinates = debug_early_map_coordinates_reg;
            assign debug_gather_checkpoint0 = debug_gather_checkpoint0_reg;
            assign debug_gather_checkpoint1 = debug_gather_checkpoint1_reg;
            assign debug_verify_digest = debug_verify_digest_reg;
            assign debug_verify_parity = debug_verify_parity_reg;
            assign debug_verify_syndrome = debug_verify_syndrome_reg;
            assign debug_verify_address = debug_verify_address_reg;
        end else begin : g_debug_outputs_off
            assign debug_map_detail = '0;
            assign debug_fault_time = '0;
            assign debug_fault_group = '0;
            assign debug_fault_beat = '0;
            assign debug_fault_conflict_banks = '0;
            assign debug_fault_addr0 = '0;
            assign debug_fault_addr1 = '0;
            assign debug_invalid_state = '0;
            assign debug_fault_descriptor0 = '0;
            assign debug_fault_descriptor1 = '0;
            assign debug_fault_banks = '0;
            assign debug_gather_digest = '0;
            assign debug_gather_count = '0;
            assign debug_early_gathers = '0;
            assign debug_early_raw0 = '0;
            assign debug_early_raw1 = '0;
            assign debug_early_response_banks = '0;
            assign debug_early_response_port1 = '0;
            assign debug_early_map_banks = '0;
            assign debug_early_map_coordinates = '0;
            assign debug_gather_checkpoint0 = '0;
            assign debug_gather_checkpoint1 = '0;
            assign debug_verify_digest = '0;
            assign debug_verify_parity = '0;
            assign debug_verify_syndrome = '0;
            assign debug_verify_address = '0;
        end
    endgenerate

    always_comb begin
        mapped_invalid_now = 1'b0;
        if (gather_issue_valid || verify_issue_valid || emit_capture_valid)
            for (invalid_lane = 0; invalid_lane < 4; invalid_lane = invalid_lane + 1)
                for (invalid_slot = 0; invalid_slot < 4; invalid_slot = invalid_slot + 1)
                    if (descriptor_valid[invalid_slot])
                        mapped_invalid_now = mapped_invalid_now ||
                            mapped_invalid[invalid_lane][invalid_slot];
    end

    always_comb begin
        logic [34:0] selected_signs;
        selected_signs = '0;
        // Static template read. Four independent slot ROMs provide one full
        // conflict-free beat without replication.
        map_coordinates = {map_coordinate3, map_coordinate2,
                           map_coordinate1, map_coordinate0};
        for (slot_index = 0; slot_index < 4; slot_index = slot_index + 1) begin
            descriptor[slot_index] = template_read_data[slot_index*23 +: 23];
            descriptor_hash[slot_index] =
                template_read_data[4*23+slot_index*HASH_WIDTH +: HASH_WIDTH];
        end
        descriptor_valid = '0; descriptor_edges = '0;
        descriptor_orbits = '0; descriptor_anchors = '0;
        for (slot_index = 0; slot_index < 4; slot_index = slot_index + 1) begin
            descriptor_valid[slot_index] = descriptor[slot_index][20];
            descriptor_edges[slot_index*6 +: 6] = descriptor[slot_index][19:14];
            descriptor_orbits[slot_index*7 +: 7] = descriptor[slot_index][13:7];
            descriptor_anchors[slot_index*7 +: 7] = descriptor[slot_index][6:0];
        end
        for (lane_index = 0; lane_index < 4; lane_index = lane_index + 1) begin
            // Emit descriptors advance independently of the registered gather
            // response. The old sign must therefore be indexed by the current
            // emit edge, not by the preceding gather beat.
            engine_emit_old_signs[lane_index] = '0;
            for (slot_index = 0; slot_index < 4; slot_index = slot_index + 1)
                if (emit_descriptor_valid_reg[slot_index]) begin
                    selected_signs = (RELAY_MODE && relay_phase_reg) ?
                                     active_new_signs[lane_index] : active_signs[lane_index];
                    engine_emit_old_signs[lane_index][slot_index] =
                        selected_signs[emit_descriptor_edges_reg[slot_index*6 +: 6]];
                end
        end

        // Both pair crossbars see the same sparse beat at different check
        // translations. Data is relevant only during emit writes.
        // Request vectors are laid out as two lanes x four slots.  Use
        // elaboration-time constant assignments below; Gowin can mis-map a
        // loop-variable indexed packed-vector write even when RTL simulation
        // is correct, producing the observed cross-port aliases.

        gather_issue_valid = state == S_RUN && template_pending &&
                             template_kind == TEMPLATE_GATHER &&
                             template_beat < active_template_beats &&
                             pair0_gather_ready && pair1_gather_ready;
        verify_issue_valid = state == S_VERIFY_RUN && template_pending &&
                             template_kind == TEMPLATE_VERIFY &&
                             template_beat < active_verify_template_beats;
        emit_descriptor_valid = state == S_RUN && template_pending &&
                                template_kind == TEMPLATE_EMIT &&
                                template_emit_data_valid_reg &&
                                (emit_descriptor_valid_reg != '0);
        emit_capture_valid = emit_descriptor_valid && pair0_emit_valid &&
                             pair1_emit_valid && (!emit_buffer_valid || emit_phase);
        emit_advance = state == S_RUN && emit_buffer_valid && !emit_phase &&
                       pair0_emit_valid && pair1_emit_valid;
        emit_commit = state == S_RUN && emit_buffer_valid && emit_phase &&
                      (emit_buffer_last || emit_capture_valid);

        // Memory defaults and operation priority: profile initialization,
        // emit broadcast, update/verify gather, then logical scan.
        for (replica_index = 0; replica_index < 2; replica_index = replica_index + 1)
            for (bank_index = 0; bank_index < 4; bank_index = bank_index + 1) begin
                mem_port0_read[replica_index][bank_index] = 1'b0;
                mem_port0_write[replica_index][bank_index] = 1'b0;
                mem_port1_read[replica_index][bank_index] = 1'b0;
                mem_port1_write[replica_index][bank_index] = 1'b0;
                mem_port0_addr[replica_index][bank_index] = '0;
                mem_port1_addr[replica_index][bank_index] = '0;
                mem_port0_write_data[replica_index][bank_index] = '0;
                mem_port1_write_data[replica_index][bank_index] = '0;
                if (state == S_INIT && orbit_config_valid && orbit_config_tag == init_orbit) begin
                    mem_port0_write[replica_index][bank_index] = 1'b1;
                    mem_port1_write[replica_index][bank_index] = 1'b1;
                    mem_port0_addr[replica_index][bank_index] =
                        ({5'd0, init_orbit} * 12'd18) +
                        ({8'd0, init_pair} << 1);
                    mem_port1_addr[replica_index][bank_index] =
                        ({5'd0, init_orbit} * 12'd18) +
                        ({8'd0, init_pair} << 1) + 12'd1;
                    case (profile_prior_scale)
                        1: begin
                            mem_port0_write_data[replica_index][bank_index] = orbit_config_data[54:44];
                            mem_port1_write_data[replica_index][bank_index] = orbit_config_data[54:44];
                        end
                        2: begin
                            mem_port0_write_data[replica_index][bank_index] = orbit_config_data[10:0];
                            mem_port1_write_data[replica_index][bank_index] = orbit_config_data[10:0];
                        end
                        3: begin
                            mem_port0_write_data[replica_index][bank_index] = orbit_config_data[21:11];
                            mem_port1_write_data[replica_index][bank_index] = orbit_config_data[21:11];
                        end
                        5: begin
                            mem_port0_write_data[replica_index][bank_index] = orbit_config_data[43:33];
                            mem_port1_write_data[replica_index][bank_index] = orbit_config_data[43:33];
                        end
                        default: begin
                            mem_port0_write_data[replica_index][bank_index] = orbit_config_data[32:22];
                            mem_port1_write_data[replica_index][bank_index] = orbit_config_data[32:22];
                        end
                    endcase
                end else if (emit_buffer_valid &&
                             (!RELAY_MODE || relay_phase_reg)) begin
                    if (!emit_phase) begin
                        mem_port0_write[replica_index][bank_index] = pair_port0_valid[0][bank_index];
                        mem_port1_write[replica_index][bank_index] = pair_port1_valid[0][bank_index];
                        mem_port0_addr[replica_index][bank_index] =
                            pair_port0_address[0][bank_index*12 +: 12];
                        mem_port1_addr[replica_index][bank_index] =
                            pair_port1_address[0][bank_index*12 +: 12];
                        mem_port0_write_data[replica_index][bank_index] =
                            pair_port0_write_data[0][bank_index*11 +: 11];
                        mem_port1_write_data[replica_index][bank_index] =
                            pair_port1_write_data[0][bank_index*11 +: 11];
                    end else begin
                        mem_port0_write[replica_index][bank_index] = pair_port0_valid[1][bank_index];
                        mem_port1_write[replica_index][bank_index] = pair_port1_valid[1][bank_index];
                        mem_port0_addr[replica_index][bank_index] =
                            pair_port0_address[1][bank_index*12 +: 12];
                        mem_port1_addr[replica_index][bank_index] =
                            pair_port1_address[1][bank_index*12 +: 12];
                        mem_port0_write_data[replica_index][bank_index] =
                            pair_port0_write_data[1][bank_index*11 +: 11];
                        mem_port1_write_data[replica_index][bank_index] =
                            pair_port1_write_data[1][bank_index*11 +: 11];
                    end
                end else if (read_request_valid) begin
                    mem_port0_read[replica_index][bank_index] =
                        pair_port0_valid[replica_index][bank_index];
                    mem_port1_read[replica_index][bank_index] =
                        pair_port1_valid[replica_index][bank_index];
                    mem_port0_addr[replica_index][bank_index] =
                        pair_port0_address[replica_index][bank_index*12 +: 12];
                    mem_port1_addr[replica_index][bank_index] =
                        pair_port1_address[replica_index][bank_index*12 +: 12];
                end else if (state == S_LOGICAL_RUN && logical_issue_valid && replica_index == 0) begin
                    mem_port0_read[0][bank_index] = 1'b1;
                    mem_port1_read[0][bank_index] = 1'b1;
                    mem_port0_addr[0][bank_index] =
                        ({5'd0, logical_orbit} * 12'd18) + {7'd0, logical_local};
                    mem_port1_addr[0][bank_index] =
                        ({5'd0, logical_orbit} * 12'd18) + {7'd0, logical_local} + 12'd1;
                end
            end

        // Map native-DPB data to same latency as selected response metadata.
        // Stage N captures output of request issued one edge earlier; that is
        // exact data for metadata stage N under continuous requests.
        for (replica_index = 0; replica_index < 2; replica_index = replica_index + 1)
            for (bank_index = 0; bank_index < 4; bank_index = bank_index + 1) begin
                if (READ_RESPONSE_STAGES <= 1) begin
                    response_posterior_port0[replica_index][bank_index] =
                        posterior_data_port0_stage1[replica_index][bank_index];
                    response_posterior_port1[replica_index][bank_index] =
                        posterior_data_port1_stage1[replica_index][bank_index];
                end else if (READ_RESPONSE_STAGES == 2) begin
                    response_posterior_port0[replica_index][bank_index] =
                        posterior_data_port0_stage2[replica_index][bank_index];
                    response_posterior_port1[replica_index][bank_index] =
                        posterior_data_port1_stage2[replica_index][bank_index];
                end else begin
                    response_posterior_port0[replica_index][bank_index] =
                        posterior_data_port0_stage3[replica_index][bank_index];
                    response_posterior_port1[replica_index][bank_index] =
                        posterior_data_port1_stage3[replica_index][bank_index];
                end
            end

        // Map registered crossbar tags back from physical bank ports.
        for (lane_index = 0; lane_index < 4; lane_index = lane_index + 1) begin
            engine_gather_valid[lane_index] = gather_response_valid ? response_valid_mask : 4'd0;
            engine_gather_edges[lane_index] = response_edges;
            engine_gather_orbits[lane_index] = response_orbits;
            engine_gather_anchors[lane_index] = response_anchors;
            engine_gather_posteriors[lane_index] = '0;
            engine_gather_old_signs[lane_index] = '0;
            for (slot_index = 0; slot_index < 4; slot_index = slot_index + 1) begin
                request_index = (lane_index & 1) * 4 + slot_index;
                bank_index = response_banks[lane_index >> 1][request_index*2 +: 2];
                if (response_port1[lane_index >> 1][request_index])
                    engine_gather_posteriors[lane_index][slot_index*11 +: 11] =
                        response_posterior_port1[lane_index >> 1][bank_index];
                else
                    engine_gather_posteriors[lane_index][slot_index*11 +: 11] =
                        response_posterior_port0[lane_index >> 1][bank_index];
                if (response_valid_mask[slot_index]) begin
                    selected_signs = (RELAY_MODE && relay_phase_reg) ?
                                     active_new_signs[lane_index] : active_signs[lane_index];
                    engine_gather_old_signs[lane_index][slot_index] =
                        selected_signs[response_edges[slot_index*6 +: 6]];
                end
            end
        end

        engine_start_valid = state == S_START;
        engine_emit_ready = emit_advance;

        for (lane_index = 0; lane_index < 4; lane_index = lane_index + 1) begin
            signs_after_emit[lane_index] = active_signs[lane_index];
            for (slot_index = 0; slot_index < 4; slot_index = slot_index + 1)
                if (emit_buffer_valid_mask_flat[lane_index*4 + slot_index])
                    signs_after_emit[lane_index]
                        [emit_buffer_edges_flat[(lane_index*24) + slot_index*6 +: 6]] =
                        emit_buffer_new_signs_flat[lane_index*4 + slot_index];
        end

        capture_hash_masks = '0; capture_hash_flips = '0; capture_hash_words = '0;
        for (lane_index = 0; lane_index < 4; lane_index = lane_index + 1)
            for (slot_index = 0; slot_index < 4; slot_index = slot_index + 1) begin
                capture_hash_masks[lane_index*4+slot_index] =
                    engine_out_valid_mask[lane_index][slot_index];
                capture_hash_flips[lane_index*4+slot_index] =
                    engine_out_flips[lane_index][slot_index];
                capture_hash_words[(lane_index*4+slot_index)*HASH_WIDTH +: HASH_WIDTH] =
                    transform_hash(descriptor_hash[slot_index],
                                   active_schedule_coordinates[lane_index*7 +: 7]);
            end
        hash_emit_valid = emit_commit;
        hash_emit_masks = emit_buffer_hash_masks;
        hash_emit_flips = emit_buffer_hash_flips;
        hash_emit_words = emit_buffer_hash_words;

        verify_parity_after = verify_parity;
        if (verify_response_valid)
            for (lane_index = 0; lane_index < 4; lane_index = lane_index + 1)
                for (slot_index = 0; slot_index < 4; slot_index = slot_index + 1)
                    if (response_valid_mask[slot_index])
                        verify_parity_after[lane_index] = verify_parity_after[lane_index] ^
                            engine_gather_posteriors[lane_index][slot_index*11+10];

        logical_accum_after = logical_accum;
        if (logical_response_valid) begin
            for (bank_index = 0; bank_index < 4; bank_index = bank_index + 1) begin
                if (logical_mem_port0_selected[bank_index*11+10])
                    logical_accum_after = logical_accum_after ^
                                          logical_quad_data0_selected[bank_index*12 +: 12];
                if (logical_mem_port1_selected[bank_index*11+10])
                    logical_accum_after = logical_accum_after ^
                                          logical_quad_data1_selected[bank_index*12 +: 12];
            end
        end

        start_ready = state == S_IDLE;
        done_valid = state == S_DONE;
        busy = state != S_IDLE && state != S_DONE;
        clear_target_hash = shot_clear;
        clear_correction_hash = state == S_INIT && init_first;
        // `syndrome_write_valid` is part of the load-while-idle interface
        // contract (and the syndrome RAM independently enforces it). Avoid
        // putting a state decode into every target-hash bit's data path.
        target_load_valid = syndrome_write_valid;
        target_load_bit = target_load_valid;
        target_load_word = '0;
        for (lane_index = 0; lane_index < 4; lane_index = lane_index + 1)
            if (syndrome_write_bits[lane_index])
                target_load_word = target_load_word ^
                                    transform_hash(hash_time_rom[syndrome_write_time],
                                                   pair_group_coordinate(syndrome_write_group,
                                                                         lane_index[1:0]));

        syndrome_write_addr = (({4'd0, syndrome_write_time} << 4) +
                               ({4'd0, syndrome_write_time} << 1)) +
                              syndrome_write_group;
        remap_read_en = state == S_TARGET_REMAP && !remap_last_reg;
        remap_pair_read_addr0 = ({4'd0, remap_time} * 8'd18) +
            standard_source_pair_group(remap_group, 2'd0);
        remap_pair_read_addr1 = ({4'd0, remap_time} * 8'd18) +
            standard_source_pair_group(remap_group, 2'd1);
        remap_pair_read_addr2 = ({4'd0, remap_time} * 8'd18) +
            standard_source_pair_group(remap_group, 2'd2);
        remap_pair_read_addr3 = ({4'd0, remap_time} * 8'd18) +
            standard_source_pair_group(remap_group, 2'd3);
        remap_write_en = state == S_TARGET_REMAP && remap_read_valid;
        remap_write_bits = '0;
        remap_write_bits[0] = syndrome_read_bits[
            standard_source_pair_lane(remap_write_group_reg, 2'd0)];
        remap_write_bits[1] = syndrome_pair_read_bits1[
            standard_source_pair_lane(remap_write_group_reg, 2'd1)];
        remap_write_bits[2] = syndrome_pair_read_bits2[
            standard_source_pair_lane(remap_write_group_reg, 2'd2)];
        remap_write_bits[3] = syndrome_pair_read_bits3[
            standard_source_pair_lane(remap_write_group_reg, 2'd3)];
        syndrome_read_en = state == S_RECORD_LOAD || state == S_START ||
                           state == S_RUN || state == S_VERIFY_SETUP ||
                           state == S_VERIFY_RUN || remap_read_en;
        if (state == S_TARGET_REMAP)
            syndrome_read_addr = remap_pair_read_addr0;
        else if (state == S_VERIFY_SETUP)
            syndrome_read_addr = (({4'd0, verify_time} << 4) +
                                  ({4'd0, verify_time} << 1)) +
                                  verify_group_ordinal;
        else if (state == S_VERIFY_RUN)
            syndrome_read_addr = (({4'd0, active_verify_time} << 4) +
                                  ({4'd0, active_verify_time} << 1)) +
                                  verify_group_ordinal;
        else if (PREFETCH_RECORDS && state == S_RUN &&
                 !(time_ordinal_reg == 12 && group_ordinal_reg == 17))
            syndrome_read_addr = (({4'd0, schedule_time} << 4) +
                                  ({4'd0, schedule_time} << 1)) +
                                 schedule_group;
        else
            syndrome_read_addr = (({4'd0, active_schedule_time} << 4) +
                                  ({4'd0, active_schedule_time} << 1)) +
                                  active_schedule_group;

        record_read_en = (state == S_RECORD_LOAD && record_issue_lane < 4) ||
                         (PREFETCH_RECORDS && state == S_RUN &&
                          prefetch_active && prefetch_issue_lane < 4);
        record_read_en_b = 1'b0;
        // Use the four registered scalar schedule coordinates here.  The
        // packed [lane*7 +: 7] form is RTL-equivalent but the GW2AR mapper
        // can fold variable lane slices; that only becomes visible after the
        // first group, when record RAM contains updated (non-identical)
        // records.
        case ((PREFETCH_RECORDS && state == S_RUN) ?
              prefetch_issue_lane[1:0] : record_issue_lane[1:0])
            2'd0: record_read_addr = check_id(
                                              (PREFETCH_RECORDS && state == S_RUN) ?
                                              schedule_time : active_schedule_time,
                                              (PREFETCH_RECORDS && state == S_RUN) ?
                                              schedule_coordinate0 : active_schedule_coordinate0);
            2'd1: record_read_addr = check_id(
                                              (PREFETCH_RECORDS && state == S_RUN) ?
                                              schedule_time : active_schedule_time,
                                              (PREFETCH_RECORDS && state == S_RUN) ?
                                              schedule_coordinate1 : active_schedule_coordinate1);
            2'd2: record_read_addr = check_id(
                                              (PREFETCH_RECORDS && state == S_RUN) ?
                                              schedule_time : active_schedule_time,
                                              (PREFETCH_RECORDS && state == S_RUN) ?
                                              schedule_coordinate2 : active_schedule_coordinate2);
            default: record_read_addr = check_id(
                                                  (PREFETCH_RECORDS && state == S_RUN) ?
                                                  schedule_time : active_schedule_time,
                                                  (PREFETCH_RECORDS && state == S_RUN) ?
                                                  schedule_coordinate3 : active_schedule_coordinate3);
        endcase
        record_read_addr_b = '0;
        record_new_read_en = RELAY_MODE && relay_phase_reg && record_read_en;
        record_new_read_addr = record_read_addr;
        record_write_en = 1'b0; record_write_addr = '0; record_write_data = '0;
        record_write_bank = RELAY_MODE ? ~relay_old_bank_reg : 1'b0;
        if (state == S_INIT && !record_clear_done) begin
            record_write_en = 1'b1; record_write_addr = record_clear_addr;
            if (RELAY_MODE)
                record_write_bank = relay_old_bank_reg;
        end else if (pending_record_valid && pending_write_lane < 4 &&
                     (!RELAY_MODE || !relay_phase_reg)) begin
            record_write_en = 1'b1;
            record_write_addr = pending_record_addr[pending_write_lane];
            record_write_data = pending_record[pending_write_lane];
        end

        // The synchronous packed template ROM is streamed one beat ahead of
        // posterior accesses. Gather immediately emits beat zero;
        // emit rows are held for the two replica-write phases.
        template_read_en = 1'b0;
        template_request_kind = TEMPLATE_GATHER;
        template_request_beat = 0;
        template_request_time = active_schedule_time;
        if (state == S_RECORD_LOAD && record_read_valid &&
            record_response_lane == 3) begin
            template_read_en = 1'b1;
        end else if (state == S_START && !template_pending) begin
            template_read_en = 1'b1;
        end else if (gather_issue_valid) begin
            template_read_en = 1'b1;
            if (template_beat + 1'b1 < active_template_beats) begin
                template_request_kind = TEMPLATE_GATHER;
                template_request_beat = template_beat + 1'b1;
            end else begin
                template_request_kind = TEMPLATE_EMIT;
                template_request_beat = 0;
            end
        end else if (emit_advance &&
                     template_beat + 1'b1 < active_template_beats) begin
            template_read_en = 1'b1;
            template_request_kind = TEMPLATE_EMIT;
            template_request_beat = template_beat + 1'b1;
        end else if (state == S_VERIFY_SETUP) begin
            template_read_en = 1'b1;
            template_request_kind = TEMPLATE_VERIFY;
            template_request_time = verify_time;
        end else if (verify_issue_valid &&
                     template_beat + 1'b1 < active_verify_template_beats) begin
            template_read_en = 1'b1;
            template_request_kind = TEMPLATE_VERIFY;
            template_request_time = active_verify_time;
            template_request_beat = template_beat + 1'b1;
        end
        template_read_addr = template_address(template_request_time, template_request_beat);
        template_consume = gather_issue_valid || verify_issue_valid ||
                           emit_advance;

        orbit_config_read_en = 1'b0;
        orbit_config_read_addr = init_orbit;
        if (state == S_INIT && (!orbit_config_valid || orbit_config_tag != init_orbit)) begin
            orbit_config_read_en = 1'b1;
        end else if (state == S_INIT && orbit_config_valid &&
                     orbit_config_tag == init_orbit && init_pair == 8 &&
                     init_orbit < ORBITS-1) begin
            orbit_config_read_en = 1'b1;
            orbit_config_read_addr = init_orbit + 1'b1;
        end else if (state == S_LOGICAL_SETUP) begin
            orbit_config_read_en = 1'b1;
            orbit_config_read_addr = 0;
        end else if (state == S_LOGICAL_RUN && logical_issue_valid &&
                     logical_local == 16 && logical_orbit < ORBITS-1) begin
            orbit_config_read_en = 1'b1;
            orbit_config_read_addr = logical_orbit + 1'b1;
        end
        logical_quad_read_en = logical_issue_valid;
        // Widen before shifting.  The old 3-bit `pattern_id << 2` expression
        // wrapped IDs 2..7 modulo 8, selecting the wrong 72-word logical
        // table while detector parity still passed.  This produced rare
        // board-only wrong-coset accepts that no extra ROM/read stage could
        // repair.
        logical_quad_read_addr0 =
            (((({7'd0, orbit_config_data[59:57]} << 2) +
               {8'd0, orbit_config_data[56:55]}) * 10'd18) +
             {5'd0, logical_local});
        logical_quad_read_addr1 = logical_quad_read_addr0 + 9'd1;
    end

    genvar pack_pair, pack_lane, pack_slot;
    generate
        for (pack_pair = 0; pack_pair < 2; pack_pair = pack_pair + 1)
            for (pack_lane = 0; pack_lane < 2; pack_lane = pack_lane + 1)
                for (pack_slot = 0; pack_slot < 4; pack_slot = pack_slot + 1) begin : g_request_pack
                    localparam integer SRC_LANE = pack_pair*2 + pack_lane;
                    localparam integer PACK_SLOT = pack_lane*4 + pack_slot;
                    localparam integer PACK_BANK = PACK_SLOT*2;
                    localparam integer PACK_ADDR = PACK_SLOT*12;
                    localparam integer PACK_DATA = PACK_SLOT*11;
                    assign pair_request_valid[pack_pair][PACK_SLOT] =
                        emit_buffer_valid ?
                            emit_buffer_valid_mask_flat[SRC_LANE*4 + pack_slot] :
                        read_request_valid ? read_request_valid_mask[pack_slot] : 1'b0;
                    assign pair_request_banks[pack_pair][PACK_BANK +: 2] =
                        emit_buffer_valid ?
                        emit_buffer_bank_flat[SRC_LANE*8 + pack_slot*2 +: 2] :
                        read_request_valid ?
                            (SRC_LANE == 0) ? read_request_bank_lane0[pack_slot*2 +: 2] :
                            (SRC_LANE == 1) ? read_request_bank_lane1[pack_slot*2 +: 2] :
                            (SRC_LANE == 2) ? read_request_bank_lane2[pack_slot*2 +: 2] :
                                               read_request_bank_lane3[pack_slot*2 +: 2] : 2'b0;
                    assign pair_request_addresses[pack_pair][PACK_ADDR +: 12] =
                        emit_buffer_valid ?
                        emit_buffer_address_flat[SRC_LANE*48 + pack_slot*12 +: 12] :
                        read_request_valid ?
                            (SRC_LANE == 0) ? read_request_address_lane0[pack_slot*12 +: 12] :
                            (SRC_LANE == 1) ? read_request_address_lane1[pack_slot*12 +: 12] :
                            (SRC_LANE == 2) ? read_request_address_lane2[pack_slot*12 +: 12] :
                                               read_request_address_lane3[pack_slot*12 +: 12] : 12'b0;
                    assign pair_request_write_data[pack_pair][PACK_DATA +: 11] =
                        emit_buffer_valid ?
                        emit_buffer_posteriors_flat[SRC_LANE*44 + pack_slot*11 +: 11] : 11'b0;
                end
    endgenerate

    always_ff @(posedge clk) begin
        // shot_clear is a synchronous per-shot reset.  It must reset the
        // complete datapath, not only guard/debug latches: otherwise an
        // isolated S_ERROR leaves the controller permanently non-ready and
        // the next request becomes a long-run failure cascade.  Immutable
        // ROM/configuration contents are unaffected by this register reset.
        if (rst || shot_clear) begin
            state <= S_IDLE; profile_reg <= 0; sweep_reg <= 0;
            time_ordinal_reg <= 0; group_ordinal_reg <= 0; cycle_count <= 0;
            active_schedule_time <= 0; active_schedule_group <= 0;
            active_schedule_coordinates <= 0; active_template_beats <= 0;
            active_schedule_coordinate0 <= 0; active_schedule_coordinate1 <= 0;
            active_schedule_coordinate2 <= 0; active_schedule_coordinate3 <= 0;
            active_syndrome_bits <= 0;
            active_verify_time <= 0; active_verify_template_beats <= 0;
            active_verify_coordinates <= 0;
            active_verify_coordinate0 <= 0; active_verify_coordinate1 <= 0;
            active_verify_coordinate2 <= 0; active_verify_coordinate3 <= 0;
            sweeps_used <= 0; profile_used <= 0; success <= 0; deferred <= 0;
            logical_class <= 0; error <= 0; init_orbit <= 0; init_pair <= 0;
            record_clear_addr <= 0; record_clear_done <= 0; init_first <= 0;
            record_issue_lane <= 0;
            record_response_lane <= 0;
            prefetch_active <= 1'b0; prefetch_valid <= 1'b0;
            prefetch_issue_lane <= 0; prefetch_response_lane <= 0;
            pending_record_valid <= 0;
            relay_phase_reg <= 1'b0; relay_old_bank_reg <= 1'b0;
            pending_write_lane <= 0; gather_issue_beat <= 0;
            gather_response_valid <= 0; read_request_valid <= 0;
            read_pending_reg <= 1'b0;
            read_request_verify <= 0; read_request_last <= 0;
            read_request_valid_mask <= 0; read_request_edges <= 0;
            read_request_orbits <= 0; read_request_anchors <= 0;
            read_response_valid_stage <= 0;
            read_response_verify_stage <= 0;
            read_response_last_stage <= 0;
            read_response_valid_mask_stage <= 0;
            read_response_edges_stage <= 0;
            read_response_orbits_stage <= 0;
            read_response_anchors_stage <= 0;
            read_response_banks_stage[0] <= 0;
            read_response_banks_stage[1] <= 0;
            read_response_port1_stage[0] <= 0;
            read_response_port1_stage[1] <= 0;
            read_response_valid_stage2 <= 0;
            read_response_verify_stage2 <= 0;
            read_response_last_stage2 <= 0;
            read_response_valid_mask_stage2 <= 0;
            read_response_edges_stage2 <= 0;
            read_response_orbits_stage2 <= 0;
            read_response_anchors_stage2 <= 0;
            read_response_banks_stage2[0] <= 0;
            read_response_banks_stage2[1] <= 0;
            read_response_port1_stage2[0] <= 0;
            read_response_port1_stage2[1] <= 0;
            read_response_valid_stage3 <= 0;
            read_response_verify_stage3 <= 0;
            read_response_last_stage3 <= 0;
            read_response_valid_mask_stage3 <= 0;
            read_response_edges_stage3 <= 0;
            read_response_orbits_stage3 <= 0;
            read_response_anchors_stage3 <= 0;
            read_response_banks_stage3[0] <= 0;
            read_response_banks_stage3[1] <= 0;
            read_response_port1_stage3[0] <= 0;
            read_response_port1_stage3[1] <= 0;
            for (seq_replica = 0; seq_replica < 2; seq_replica = seq_replica + 1)
                for (seq_bank = 0; seq_bank < 4; seq_bank = seq_bank + 1) begin
                    posterior_data_port0_stage1[seq_replica][seq_bank] <= '0;
                    posterior_data_port0_stage2[seq_replica][seq_bank] <= '0;
                    posterior_data_port0_stage3[seq_replica][seq_bank] <= '0;
                    posterior_data_port0_stage4[seq_replica][seq_bank] <= '0;
                    posterior_data_port1_stage1[seq_replica][seq_bank] <= '0;
                    posterior_data_port1_stage2[seq_replica][seq_bank] <= '0;
                    posterior_data_port1_stage3[seq_replica][seq_bank] <= '0;
                    posterior_data_port1_stage4[seq_replica][seq_bank] <= '0;
                    mem_port0_write_q[seq_replica][seq_bank] <= 1'b0;
                    mem_port1_write_q[seq_replica][seq_bank] <= 1'b0;
                    mem_port0_write_addr_q[seq_replica][seq_bank] <= '0;
                    mem_port1_write_addr_q[seq_replica][seq_bank] <= '0;
                    mem_port0_write_data_q[seq_replica][seq_bank] <= '0;
                    mem_port1_write_data_q[seq_replica][seq_bank] <= '0;
                end
            response_valid_mask <= 0;
            response_edges <= 0; response_orbits <= 0; response_anchors <= 0;
            emit_beat <= 0; emit_phase <= 0; emit_buffer_valid <= 0;
            emit_buffer_last <= 0; emit_buffer_hash_masks <= 0;
            emit_buffer_hash_flips <= 0; emit_buffer_hash_words <= 0;
            emit_buffer_valid_mask_flat <= '0;
            emit_buffer_new_signs_flat <= '0;
            emit_buffer_edges_flat <= '0;
            emit_buffer_posteriors_flat <= '0;
            emit_buffer_bank_flat <= '0;
            emit_buffer_address_flat <= '0;
            verify_time_ordinal <= 0;
            verify_group_ordinal <= 0; verify_issue_beat <= 0;
            verify_response_valid <= 0; verify_response_last <= 0;
            verify_parity <= 0; verify_failed <= 0; logical_orbit <= 0;
            syndrome_nonzero <= 1'b0;
            logical_local <= 0; logical_response_valid <= 0;
            logical_response_valid_stage <= 0;
            logical_response_last_stage <= 0;
            logical_response_orbit_stage <= 0;
            logical_response_local_stage <= 0;
            logical_response_valid_stage2 <= 0;
            logical_response_last_stage2 <= 0;
            logical_response_orbit_stage2 <= 0;
            logical_response_local_stage2 <= 0;
            logical_all_issued <= 0;
            logical_response_last <= 0; logical_response_orbit <= 0;
            logical_response_local <= 0; logical_accum <= 0;
            emit_parity_reg <= 0;
            sweep_parity_failed <= 0;
            sweep_hash_failed <= 0;
            logical_mem_port0_stage <= '0;
            logical_mem_port1_stage <= '0;
            logical_quad_data0_stage <= '0;
            logical_quad_data1_stage <= '0;
            logical_mem_port0_stage2 <= '0;
            logical_mem_port1_stage2 <= '0;
            logical_quad_data0_stage2 <= '0;
            logical_quad_data1_stage2 <= '0;
            template_pending <= 0; template_kind <= TEMPLATE_GATHER;
            template_beat <= 0;
            template_emit_wait_reg <= 1'b0;
            template_emit_data_valid_reg <= 1'b0;
            emit_descriptor_valid_reg <= '0;
            emit_descriptor_edges_reg <= '0;
            emit_descriptor_orbits_reg <= '0;
            emit_descriptor_anchors_reg <= '0;
            orbit_config_valid <= 0; orbit_config_tag <= 0;
            guard_schedule_error <= 0; guard_engine_error <= 0;
            guard_mapping_error <= 0; guard_memory_error <= 0;
            guard_mapping_pair0 <= 0; guard_mapping_pair1 <= 0;
            guard_mapping_invalid <= 0; debug_fault_latched <= 0;
            debug_fault_time_reg <= 0; debug_fault_group_reg <= 0;
            debug_fault_beat_reg <= 0;
            debug_fault_conflict_banks_reg <= 0;
            debug_fault_addr0_reg <= 0;
            debug_fault_addr1_reg <= 0;
            debug_invalid_state_reg <= 0;
            debug_default_hit <= 0;
            debug_fault_descriptor0_reg <= 0;
            debug_fault_descriptor1_reg <= 0;
            debug_fault_banks_reg <= 0;
            debug_gather_digest_reg <= 0;
            debug_early_gathers_reg <= 0;
            debug_early_raw0_reg <= 0;
            debug_early_raw1_reg <= 0;
            debug_early_response_banks_reg <= 0;
            debug_early_response_port1_reg <= 0;
            debug_early_map_banks_reg <= 0;
            debug_early_map_coordinates_reg <= 0;
            debug_gather_count_reg <= 0;
            debug_early_issue_seen_reg <= 1'b0;
            debug_early_response_seen_reg <= 1'b0;
            debug_record_seen_reg <= 1'b0;
            debug_gather_checkpoint0_reg <= 0;
            debug_gather_checkpoint1_reg <= 0;
            debug_gather_seen_reg <= 1'b0;
            debug_post_emit_pending_reg <= 1'b0;
            debug_post_emit_done_reg <= 1'b0;
            debug_post_emit_digest_reg <= '0;
            debug_watch_active_reg <= 1'b0;
            debug_watch_done_reg <= 1'b0;
            debug_watch_hit0_reg <= 1'b0;
            debug_watch_hit1_reg <= 1'b0;
            debug_watch_bank0_reg <= '0;
            debug_watch_bank1_reg <= '0;
            debug_watch_addr0_reg <= '0;
            debug_watch_addr1_reg <= '0;
            debug_watch_data0_reg <= '0;
            debug_watch_data1_reg <= '0;
            debug_watch_read0_reg <= '0;
            debug_watch_read1_reg <= '0;
            debug_init_value0_reg <= '0;
            debug_init_value1_reg <= '0;
            debug_verify_digest_reg <= 0;
            debug_verify_parity_reg <= 0;
            debug_verify_syndrome_reg <= 0;
            debug_verify_address_reg <= 0;
            mapping_error_stage <= 0;
            remap_time <= 0; remap_group <= 0;
            remap_read_valid <= 1'b0; remap_last_reg <= 1'b0;
            remap_write_addr_reg <= 0; remap_write_group_reg <= 0;
            for (seq_lane = 0; seq_lane < 4; seq_lane = seq_lane + 1) begin
                active_record[seq_lane] <= 0; active_new_record[seq_lane] <= 0;
                active_signs[seq_lane] <= 0; active_new_signs[seq_lane] <= 0;
                pending_record[seq_lane] <= 0; pending_record_addr[seq_lane] <= 0;
                for (seq_bank = 0; seq_bank < 4; seq_bank = seq_bank + 1) begin
                    read_request_bank[seq_lane][seq_bank] <= 0;
                    read_request_address[seq_lane][seq_bank] <= 0;
                end
            end
            read_request_bank_flat <= '0;
            read_request_address_flat <= '0;
            read_request_bank_lane0 <= 0; read_request_bank_lane1 <= 0;
            read_request_bank_lane2 <= 0; read_request_bank_lane3 <= 0;
            read_request_address_lane0 <= 0; read_request_address_lane1 <= 0;
            read_request_address_lane2 <= 0; read_request_address_lane3 <= 0;
            for (seq_replica = 0; seq_replica < 2; seq_replica = seq_replica + 1) begin
                response_banks[seq_replica] <= 0; response_port1[seq_replica] <= 0;
            end
        end else begin
            // DPB write commands are registered one cycle before the native
            // memory edge. Reads stay on the existing request/response pipe;
            // only writeback/init commands move through this short register
            // boundary, removing state/crossbar fanout from DPB timing.
            for (seq_replica = 0; seq_replica < 2; seq_replica = seq_replica + 1)
                for (seq_bank = 0; seq_bank < 4; seq_bank = seq_bank + 1) begin
                    mem_port0_write_q[seq_replica][seq_bank] <=
                        mem_port0_write[seq_replica][seq_bank];
                    mem_port1_write_q[seq_replica][seq_bank] <=
                        mem_port1_write[seq_replica][seq_bank];
                    mem_port0_write_addr_q[seq_replica][seq_bank] <=
                        mem_port0_addr[seq_replica][seq_bank];
                    mem_port1_write_addr_q[seq_replica][seq_bank] <=
                        mem_port1_addr[seq_replica][seq_bank];
                    mem_port0_write_data_q[seq_replica][seq_bank] <=
                        mem_port0_write_data[seq_replica][seq_bank];
                    mem_port1_write_data_q[seq_replica][seq_bank] <=
                        mem_port1_write_data[seq_replica][seq_bank];
                end
            mapping_error_stage <= mapped_invalid_now;
            if (shot_clear) begin
                syndrome_nonzero <= 1'b0;
                guard_schedule_error <= 1'b0;
                guard_engine_error <= 1'b0;
                guard_mapping_error <= 1'b0;
                guard_memory_error <= 1'b0;
                guard_mapping_pair0 <= 1'b0;
                guard_mapping_pair1 <= 1'b0;
                guard_mapping_invalid <= 1'b0;
                debug_fault_latched <= 1'b0;
                debug_fault_time_reg <= 0;
                debug_fault_group_reg <= 0;
                debug_fault_beat_reg <= 0;
                debug_fault_conflict_banks_reg <= 0;
                debug_fault_addr0_reg <= 0;
                debug_fault_addr1_reg <= 0;
                debug_invalid_state_reg <= 0;
                debug_default_hit <= 0;
                debug_fault_descriptor0_reg <= 0;
                debug_fault_descriptor1_reg <= 0;
                debug_fault_banks_reg <= 0;
                debug_gather_digest_reg <= 0;
                debug_early_gathers_reg <= 0;
                debug_early_raw0_reg <= 0;
                debug_early_raw1_reg <= 0;
                debug_early_response_banks_reg <= 0;
                debug_early_response_port1_reg <= 0;
                debug_early_map_banks_reg <= 0;
                debug_early_map_coordinates_reg <= 0;
                debug_gather_count_reg <= 0;
                debug_early_issue_seen_reg <= 1'b0;
                debug_early_response_seen_reg <= 1'b0;
                debug_record_seen_reg <= 1'b0;
                debug_gather_checkpoint0_reg <= 0;
                debug_gather_checkpoint1_reg <= 0;
                read_pending_reg <= 1'b0;
                debug_gather_seen_reg <= 1'b0;
                debug_post_emit_pending_reg <= 1'b0;
                debug_post_emit_done_reg <= 1'b0;
                debug_post_emit_digest_reg <= '0;
                debug_watch_active_reg <= 1'b0;
                debug_watch_done_reg <= 1'b0;
                debug_watch_hit0_reg <= 1'b0;
                debug_watch_hit1_reg <= 1'b0;
                debug_watch_bank0_reg <= '0;
                debug_watch_bank1_reg <= '0;
                debug_watch_addr0_reg <= '0;
                debug_watch_addr1_reg <= '0;
                debug_watch_data0_reg <= '0;
                debug_watch_data1_reg <= '0;
                debug_watch_read0_reg <= '0;
                debug_watch_read1_reg <= '0;
                debug_init_value0_reg <= '0;
                debug_init_value1_reg <= '0;
                debug_target_write0_reg <= '0;
                debug_target_write1_reg <= '0;
                debug_verify_digest_reg <= 0;
                 debug_verify_parity_reg <= 0;
                 debug_verify_syndrome_reg <= 0;
                 debug_verify_address_reg <= 0;
            end else begin
                if (syndrome_write_valid && |syndrome_write_bits)
                    syndrome_nonzero <= 1'b1;
                guard_schedule_error <= guard_schedule_error || schedule_invalid ||
                                        verify_schedule_invalid;
                guard_engine_error <= guard_engine_error || pair0_error || pair1_error ||
                                      pair0_lockstep || pair1_lockstep;
                guard_mapping_error <= guard_mapping_error || mapping_error_stage ||
                                       pair_mapping_error[0] || pair_mapping_error[1];
                guard_mapping_pair0 <= guard_mapping_pair0 || pair_mapping_error[0];
                guard_mapping_pair1 <= guard_mapping_pair1 || pair_mapping_error[1];
                guard_mapping_invalid <= guard_mapping_invalid || mapped_invalid_now;
                guard_memory_error <= guard_memory_error || memory_error_now;
                if (!debug_fault_latched &&
                    (mapped_invalid_now || pair_mapping_error[0] || pair_mapping_error[1])) begin
                    debug_fault_latched <= 1'b1;
                    debug_fault_descriptor0_reg <= descriptor[2];
                    debug_fault_descriptor1_reg <= descriptor[3];
                    for (debug_pair = 0; debug_pair < 4; debug_pair = debug_pair + 1)
                        for (debug_bank = 0; debug_bank < 4; debug_bank = debug_bank + 1)
                            debug_fault_banks_reg[debug_pair*8 + debug_bank*2 +: 2] <=
                                mapped_bank[debug_pair][debug_bank];
                    debug_fault_time_reg <= (state == S_VERIFY_RUN) ? active_verify_time :
                                            active_schedule_time;
                    debug_fault_group_reg <= (state == S_VERIFY_RUN) ? verify_group_ordinal :
                                             active_schedule_group;
                    debug_fault_beat_reg <= template_beat;
                    debug_fault_conflict_banks_reg <= {
                        pair_mapping_conflict_banks[1], pair_mapping_conflict_banks[0]
                    };
                    for (debug_pair = 0; debug_pair < 2; debug_pair = debug_pair + 1)
                        for (debug_bank = 0; debug_bank < 4; debug_bank = debug_bank + 1)
                            if (pair_mapping_conflict_banks[debug_pair][debug_bank]) begin
                                if (debug_pair == 0) begin
                                    debug_fault_addr0_reg[11:0] <=
                                        debug_bank_address(pair_port0_address[debug_pair], debug_bank[1:0]);
                                    debug_fault_addr1_reg[11:0] <=
                                        debug_bank_address(pair_port1_address[debug_pair], debug_bank[1:0]);
                                end else begin
                                    debug_fault_addr0_reg[23:12] <=
                                        debug_bank_address(pair_port0_address[debug_pair], debug_bank[1:0]);
                                    debug_fault_addr1_reg[23:12] <=
                                        debug_bank_address(pair_port1_address[debug_pair], debug_bank[1:0]);
                                end
                            end
                    // Also retain the direct mapper outputs for the two
                    // currently failing cross-port aliases.  This separates
                    // mapper arithmetic corruption from crossbar packing.
                    debug_fault_addr0_reg[11:0] <= mapped_address[0][3];
                    debug_fault_addr0_reg[23:12] <= mapped_address[2][2];
                    debug_fault_addr1_reg[11:0] <= mapped_address[1][1];
                    debug_fault_addr1_reg[23:12] <= mapped_address[3][0];
                end
            end
            // Slot-ROM output is synchronous.  A request for an emit row
            // therefore spends one full controller cycle in `wait` before
            // the descriptor can drive the engine/crossbar.
            if (state != S_RUN) begin
                template_emit_wait_reg <= 1'b0;
                template_emit_data_valid_reg <= 1'b0;
                emit_descriptor_valid_reg <= '0;
            end else if (template_read_en &&
                         template_request_kind == TEMPLATE_EMIT) begin
                template_emit_wait_reg <= 1'b1;
                template_emit_data_valid_reg <= 1'b0;
                emit_descriptor_valid_reg <= '0;
            end else if (template_emit_wait_reg) begin
                template_emit_wait_reg <= 1'b0;
                template_emit_data_valid_reg <= 1'b1;
                for (slot_index = 0; slot_index < 4; slot_index = slot_index + 1) begin
                    emit_descriptor_valid_reg[slot_index] <=
                        template_read_data[slot_index*23 + 20];
                    emit_descriptor_edges_reg[slot_index*6 +: 6] <=
                        template_read_data[slot_index*23 + 14 +: 6];
                    emit_descriptor_orbits_reg[slot_index*7 +: 7] <=
                        template_read_data[slot_index*23 + 7 +: 7];
                    emit_descriptor_anchors_reg[slot_index*7 +: 7] <=
                        template_read_data[slot_index*23 +: 7];
                end
            end
            if (template_read_en) begin
                template_pending <= 1'b1;
                template_kind <= template_request_kind;
                template_beat <= template_request_beat;
            end else if (template_consume)
                template_pending <= 1'b0;
            if (orbit_config_read_en) begin
                orbit_config_valid <= 1'b1;
                orbit_config_tag <= orbit_config_read_addr;
            end
            if (state != S_IDLE && state != S_DONE)
                cycle_count <= cycle_count + 1'b1;

            // Capture posterior outputs after their synchronous read edge.
            // Extra stage decouples continuous DPB requests from response
            // metadata; direct output was only safe with serialized reads.
            for (seq_replica = 0; seq_replica < 2; seq_replica = seq_replica + 1)
                for (seq_bank = 0; seq_bank < 4; seq_bank = seq_bank + 1) begin
                    posterior_data_port0_stage1[seq_replica][seq_bank] <=
                        mem_port0_read_data[seq_replica][seq_bank];
                    posterior_data_port0_stage2[seq_replica][seq_bank] <=
                        posterior_data_port0_stage1[seq_replica][seq_bank];
                    posterior_data_port0_stage3[seq_replica][seq_bank] <=
                        posterior_data_port0_stage2[seq_replica][seq_bank];
                    posterior_data_port0_stage4[seq_replica][seq_bank] <=
                        posterior_data_port0_stage3[seq_replica][seq_bank];
                    posterior_data_port1_stage1[seq_replica][seq_bank] <=
                        mem_port1_read_data[seq_replica][seq_bank];
                    posterior_data_port1_stage2[seq_replica][seq_bank] <=
                        posterior_data_port1_stage1[seq_replica][seq_bank];
                    posterior_data_port1_stage3[seq_replica][seq_bank] <=
                        posterior_data_port1_stage2[seq_replica][seq_bank];
                    posterior_data_port1_stage4[seq_replica][seq_bank] <=
                        posterior_data_port1_stage3[seq_replica][seq_bank];
                end

            // Capture memory outputs after their synchronous read edge. This
            // aligns data with logical_response_* tags without inserting a
            // bubble into the 122-orbit scan.
            if (state == S_LOGICAL_RUN) begin
                logical_mem_port0_stage <= {
                    mem_port0_read_data[0][3], mem_port0_read_data[0][2],
                    mem_port0_read_data[0][1], mem_port0_read_data[0][0]};
                logical_mem_port1_stage <= {
                    mem_port1_read_data[0][3], mem_port1_read_data[0][2],
                    mem_port1_read_data[0][1], mem_port1_read_data[0][0]};
                logical_quad_data0_stage <= logical_quad_read_data0;
                logical_quad_data1_stage <= logical_quad_read_data1;
                logical_mem_port0_stage2 <= logical_mem_port0_stage;
                logical_mem_port1_stage2 <= logical_mem_port1_stage;
                logical_quad_data0_stage2 <= logical_quad_data0_stage;
                logical_quad_data1_stage2 <= logical_quad_data1_stage;
            end

            // Background writeback consumes one completed lane record/cycle.
            if (pending_record_valid && state != S_INIT) begin
                if (pending_write_lane == 3) begin
                    pending_write_lane <= 0;
                    pending_record_valid <= 0;
                end else
                    pending_write_lane <= pending_write_lane + 1'b1;
            end

            // First register descriptor mapping, then issue physical DPB read.
            // Native DPBs accept one request/cycle; response metadata/data
            // pipeline below preserves alignment. `read_pending_reg` used to
            // serialize requests, wasting every other cycle and inflating
            // mean core latency. Keep it as debug state only.
            read_request_valid <= gather_issue_valid || verify_issue_valid;
            if (gather_issue_valid || verify_issue_valid)
                read_pending_reg <= 1'b1;
            else if (read_request_valid)
                // The request token is the one-cycle DPB pipeline boundary.
                // Waiting for gather/verify_response_valid adds a second
                // dead cycle because that signal is itself registered from
                // read_response_valid_stage.  Clear here so the next read
                // can issue while the current native-DPB data is consumed.
                read_pending_reg <= 1'b0;
            if (gather_issue_valid || verify_issue_valid) begin
                read_request_verify <= verify_issue_valid;
                read_request_last <= verify_issue_valid &&
                    (verify_issue_beat + 1'b1 >= active_verify_template_beats);
                read_request_valid_mask <= descriptor_valid;
                read_request_edges <= descriptor_edges;
                read_request_orbits <= descriptor_orbits;
                read_request_anchors <= descriptor_anchors;
                read_request_bank[0][0] <= mapped_bank[0][0]; read_request_address[0][0] <= mapped_address[0][0];
                read_request_bank[0][1] <= mapped_bank[0][1]; read_request_address[0][1] <= mapped_address[0][1];
                read_request_bank[0][2] <= mapped_bank[0][2]; read_request_address[0][2] <= mapped_address[0][2];
                read_request_bank[0][3] <= mapped_bank[0][3]; read_request_address[0][3] <= mapped_address[0][3];
                read_request_bank[1][0] <= mapped_bank[1][0]; read_request_address[1][0] <= mapped_address[1][0];
                read_request_bank[1][1] <= mapped_bank[1][1]; read_request_address[1][1] <= mapped_address[1][1];
                read_request_bank[1][2] <= mapped_bank[1][2]; read_request_address[1][2] <= mapped_address[1][2];
                read_request_bank[1][3] <= mapped_bank[1][3]; read_request_address[1][3] <= mapped_address[1][3];
                read_request_bank[2][0] <= mapped_bank[2][0]; read_request_address[2][0] <= mapped_address[2][0];
                read_request_bank[2][1] <= mapped_bank[2][1]; read_request_address[2][1] <= mapped_address[2][1];
                read_request_bank[2][2] <= mapped_bank[2][2]; read_request_address[2][2] <= mapped_address[2][2];
                read_request_bank[2][3] <= mapped_bank[2][3]; read_request_address[2][3] <= mapped_address[2][3];
                read_request_bank[3][0] <= mapped_bank[3][0]; read_request_address[3][0] <= mapped_address[3][0];
                read_request_bank[3][1] <= mapped_bank[3][1]; read_request_address[3][1] <= mapped_address[3][1];
                read_request_bank[3][2] <= mapped_bank[3][2]; read_request_address[3][2] <= mapped_address[3][2];
                read_request_bank[3][3] <= mapped_bank[3][3]; read_request_address[3][3] <= mapped_address[3][3];
                read_request_bank_flat <= mapped_bank_flat;
                read_request_address_flat <= mapped_address_flat;
                // Use the four physically separate mapper cones here.  The
                // packed inline-function path is RTL-correct but Gowin 1.9.11
                // commoned lane 1 with lane 0 and lane 3 with lane 2 after
                // packing, which made accepted shots defer on hardware.
                read_request_bank_lane0 <= {explicit_bank03, explicit_bank02, explicit_bank01, explicit_bank00};
                read_request_bank_lane1 <= {explicit_bank13, explicit_bank12, explicit_bank11, explicit_bank10};
                read_request_bank_lane2 <= {explicit_bank23, explicit_bank22, explicit_bank21, explicit_bank20};
                read_request_bank_lane3 <= {explicit_bank33, explicit_bank32, explicit_bank31, explicit_bank30};
                read_request_address_lane0 <= {explicit_addr03, explicit_addr02, explicit_addr01, explicit_addr00};
                read_request_address_lane1 <= {explicit_addr13, explicit_addr12, explicit_addr11, explicit_addr10};
                read_request_address_lane2 <= {explicit_addr23, explicit_addr22, explicit_addr21, explicit_addr20};
                read_request_address_lane3 <= {explicit_addr33, explicit_addr32, explicit_addr31, explicit_addr30};
                if (gather_issue_valid && !debug_early_issue_seen_reg) begin
                    debug_early_issue_seen_reg <= 1'b1;
                    debug_early_map_banks_reg <= {
                        explicit_bank33, explicit_bank32, explicit_bank31, explicit_bank30,
                        explicit_bank23, explicit_bank22, explicit_bank21, explicit_bank20,
                        explicit_bank13, explicit_bank12, explicit_bank11, explicit_bank10,
                        explicit_bank03, explicit_bank02, explicit_bank01, explicit_bank00
                    };
                    debug_early_map_coordinates_reg <= map_coordinates;
                end
            end

            // The checkpoints are reserved for the first phase-1 write
            // qualification below.  A read-side snapshot here used to
            // overwrite the write telemetry before the host could inspect
            // it, making the write/readback diagnosis ambiguous.

            // Keep the response metadata on the same registered path as the
            // physical DPB read.  On the routed GW2AR image the native DPB
            // output settles one controller edge after the request register;
            // consuming read_request_* directly is therefore one edge early
            // and produces stale posteriors on the board despite a behavioral
            // model appearing correct.
            read_response_valid_stage <= read_request_valid;
            read_response_verify_stage <= read_request_verify;
            read_response_last_stage <= read_request_last;
            if (read_request_valid) begin
                read_response_valid_mask_stage <= read_request_valid_mask;
                read_response_edges_stage <= read_request_edges;
                read_response_orbits_stage <= read_request_orbits;
                read_response_anchors_stage <= read_request_anchors;
                // Pack the mapper tags directly at the issue edge.  Keeping
                // this path independent of the multidimensional registered
                // request array avoids a GW2AR synthesis fold that changed
                // the first hardware response tag from RTL 0x9339 to
                // 0x3939, selecting the wrong physical posterior banks.
                read_response_banks_stage[0] <= {
                    read_request_bank_lane1[6 +: 2], read_request_bank_lane1[4 +: 2],
                    read_request_bank_lane1[2 +: 2], read_request_bank_lane1[0 +: 2],
                    read_request_bank_lane0[6 +: 2], read_request_bank_lane0[4 +: 2],
                    read_request_bank_lane0[2 +: 2], read_request_bank_lane0[0 +: 2]
                };
                read_response_banks_stage[1] <= {
                    read_request_bank_lane3[6 +: 2], read_request_bank_lane3[4 +: 2],
                    read_request_bank_lane3[2 +: 2], read_request_bank_lane3[0 +: 2],
                    read_request_bank_lane2[6 +: 2], read_request_bank_lane2[4 +: 2],
                    read_request_bank_lane2[2 +: 2], read_request_bank_lane2[0 +: 2]
                };
                read_response_port1_stage[0] <= 8'hF0;
                read_response_port1_stage[1] <= 8'hF0;
            end
            read_response_valid_stage2 <= read_response_valid_stage;
            read_response_verify_stage2 <= read_response_verify_stage;
            read_response_last_stage2 <= read_response_last_stage;
            if (read_response_valid_stage) begin
                read_response_valid_mask_stage2 <= read_response_valid_mask_stage;
                read_response_edges_stage2 <= read_response_edges_stage;
                read_response_orbits_stage2 <= read_response_orbits_stage;
                read_response_anchors_stage2 <= read_response_anchors_stage;
                read_response_banks_stage2[0] <= read_response_banks_stage[0];
                read_response_banks_stage2[1] <= read_response_banks_stage[1];
                read_response_port1_stage2[0] <= read_response_port1_stage[0];
                read_response_port1_stage2[1] <= read_response_port1_stage[1];
            end
            read_response_valid_stage3 <= read_response_valid_stage2;
            read_response_verify_stage3 <= read_response_verify_stage2;
            read_response_last_stage3 <= read_response_last_stage2;
            if (read_response_valid_stage2) begin
                read_response_valid_mask_stage3 <= read_response_valid_mask_stage2;
                read_response_edges_stage3 <= read_response_edges_stage2;
                read_response_orbits_stage3 <= read_response_orbits_stage2;
                read_response_anchors_stage3 <= read_response_anchors_stage2;
                read_response_banks_stage3[0] <= read_response_banks_stage2[0];
                read_response_banks_stage3[1] <= read_response_banks_stage2[1];
                read_response_port1_stage3[0] <= read_response_port1_stage2[0];
                read_response_port1_stage3[1] <= read_response_port1_stage2[1];
            end
            if (READ_RESPONSE_STAGES <= 1) begin
                gather_response_valid <= read_response_valid_stage &&
                                         !read_response_verify_stage;
                verify_response_valid <= read_response_valid_stage &&
                                         read_response_verify_stage;
                if (read_response_valid_stage) begin
                    response_valid_mask <= read_response_valid_mask_stage;
                    response_edges <= read_response_edges_stage;
                    response_orbits <= read_response_orbits_stage;
                    response_anchors <= read_response_anchors_stage;
                    response_banks[0] <= read_response_banks_stage[0];
                    response_banks[1] <= read_response_banks_stage[1];
                    response_port1[0] <= read_response_port1_stage[0];
                    response_port1[1] <= read_response_port1_stage[1];
                    if (read_response_verify_stage)
                        verify_response_last <= read_response_last_stage;
                end
            end else if (READ_RESPONSE_STAGES == 2) begin
                gather_response_valid <= read_response_valid_stage2 &&
                                         !read_response_verify_stage2;
                verify_response_valid <= read_response_valid_stage2 &&
                                         read_response_verify_stage2;
                if (read_response_valid_stage2) begin
                    response_valid_mask <= read_response_valid_mask_stage2;
                    response_edges <= read_response_edges_stage2;
                    response_orbits <= read_response_orbits_stage2;
                    response_anchors <= read_response_anchors_stage2;
                    response_banks[0] <= read_response_banks_stage2[0];
                    response_banks[1] <= read_response_banks_stage2[1];
                    response_port1[0] <= read_response_port1_stage2[0];
                    response_port1[1] <= read_response_port1_stage2[1];
                    if (read_response_verify_stage2)
                        verify_response_last <= read_response_last_stage2;
                end
            end else begin
                gather_response_valid <= read_response_valid_stage3 &&
                                         !read_response_verify_stage3;
                verify_response_valid <= read_response_valid_stage3 &&
                                         read_response_verify_stage3;
                if (read_response_valid_stage3) begin
                    response_valid_mask <= read_response_valid_mask_stage3;
                    response_edges <= read_response_edges_stage3;
                    response_orbits <= read_response_orbits_stage3;
                    response_anchors <= read_response_anchors_stage3;
                    response_banks[0] <= read_response_banks_stage3[0];
                    response_banks[1] <= read_response_banks_stage3[1];
                    response_port1[0] <= read_response_port1_stage3[0];
                    response_port1[1] <= read_response_port1_stage3[1];
                    if (read_response_verify_stage3)
                        verify_response_last <= read_response_last_stage3;
                end
            end
            if (state == S_INIT) begin
                if (mem_port0_write[0][1] && mem_port0_addr[0][1] == 12'd2136)
                    debug_target_write0_reg <= mem_port0_write_data[0][1];
                if (mem_port0_write[0][2] && mem_port0_addr[0][2] == 12'd2114)
                    debug_target_write1_reg <= mem_port0_write_data[0][2];
            end
            if (state == S_RECORD_LOAD && record_read_valid &&
                    record_response_lane == 0 && !debug_record_seen_reg) begin
                debug_gather_checkpoint0_reg <= {4'b0, record_read_data[11:0]};
                debug_record_seen_reg <= 1'b1;
            end
            if (gather_response_valid && debug_gather_count_reg < 9'd234) begin
                debug_gather_digest_reg <= debug_gather_digest_reg ^ debug_gather_fold;
                if (!debug_early_response_seen_reg)
                    debug_early_gathers_reg <= {
                        engine_gather_posteriors[3][7:0],
                        engine_gather_posteriors[2][7:0],
                        engine_gather_posteriors[1][7:0],
                        engine_gather_posteriors[0][7:0]
                    };
                if (!debug_early_response_seen_reg) begin
                    debug_early_response_seen_reg <= 1'b1;
                    debug_early_raw0_reg <= {
                        mem_port0_read_data[0][3][7:0],
                        mem_port0_read_data[0][2][7:0],
                        mem_port0_read_data[0][1][7:0],
                        mem_port0_read_data[0][0][7:0]
                    };
                    debug_early_raw1_reg <= {
                        mem_port1_read_data[0][3][7:0],
                        mem_port1_read_data[0][2][7:0],
                        mem_port1_read_data[0][1][7:0],
                        mem_port1_read_data[0][0][7:0]
                    };
                    debug_early_response_banks_reg <= read_response_banks_stage3[0];
                    debug_early_response_port1_reg <= read_response_port1_stage3[0];
                    // Preserve the two initialization write samples in the
                    // probe frame; emit-time watch telemetry must not
                    // overwrite this first-read qualification.
                    debug_gather_checkpoint0_reg <= {
                        5'd0, debug_target_write0_reg
                    };
                    debug_gather_checkpoint1_reg <= {
                        5'd0, debug_target_write1_reg
                    };
                end
                debug_gather_count_reg <= debug_gather_count_reg + 1'b1;
            end
            if (gather_response_valid && debug_post_emit_pending_reg) begin
                debug_post_emit_digest_reg <= {
                    engine_gather_posteriors[3][7:0],
                    engine_gather_posteriors[2][7:0],
                    engine_gather_posteriors[1][7:0],
                    engine_gather_posteriors[0][7:0]
                };
                debug_post_emit_pending_reg <= 1'b0;
                debug_post_emit_done_reg <= 1'b1;
            end
            if (emit_commit && !debug_post_emit_done_reg)
                debug_post_emit_pending_reg <= 1'b1;

            // Temporary write/readback qualification: retain one phase-1
            // write from the first emit and compare it when the controller
            // later revisits the same physical address.
            // Capture the first completed group.  The old extra
            // `!debug_gather_count_reg` gate was not a valid first-group
            // discriminator once gather responses were pipelined: depending
            // on the DPB/response latency it either missed the first emit or
            // latched a later stale write.  The one-bit sticky done gate is
            // enough to make this a low-cost, first-group-only probe.
            if (emit_commit && emit_buffer_last &&
                !debug_watch_active_reg && !debug_watch_done_reg) begin
                if (pair_port0_valid[1][0]) begin
                    debug_watch_bank0_reg <= 2'd0;
                    debug_watch_addr0_reg <= mem_port0_addr[0][0];
                    debug_watch_data0_reg <= mem_port0_write_data[0][0];
                    debug_watch_hit0_reg <= 1'b0;
                end else if (pair_port0_valid[1][1]) begin
                    debug_watch_bank0_reg <= 2'd1;
                    debug_watch_addr0_reg <= mem_port0_addr[0][1];
                    debug_watch_data0_reg <= mem_port0_write_data[0][1];
                    debug_watch_hit0_reg <= 1'b0;
                end else if (pair_port0_valid[1][2]) begin
                    debug_watch_bank0_reg <= 2'd2;
                    debug_watch_addr0_reg <= mem_port0_addr[0][2];
                    debug_watch_data0_reg <= mem_port0_write_data[0][2];
                    debug_watch_hit0_reg <= 1'b0;
                end else if (pair_port0_valid[1][3]) begin
                    debug_watch_bank0_reg <= 2'd3;
                    debug_watch_addr0_reg <= mem_port0_addr[0][3];
                    debug_watch_data0_reg <= mem_port0_write_data[0][3];
                    debug_watch_hit0_reg <= 1'b0;
                end
                if (pair_port1_valid[1][0]) begin
                    debug_watch_bank1_reg <= 2'd0;
                    debug_watch_addr1_reg <= pair_port1_address[1][0 +: 12];
                    debug_watch_data1_reg <= pair_port1_write_data[1][0 +: 11];
                    debug_watch_hit1_reg <= 1'b0;
                end else if (pair_port1_valid[1][1]) begin
                    debug_watch_bank1_reg <= 2'd1;
                    debug_watch_addr1_reg <= pair_port1_address[1][12 +: 12];
                    debug_watch_data1_reg <= pair_port1_write_data[1][11 +: 11];
                    debug_watch_hit1_reg <= 1'b0;
                end else if (pair_port1_valid[1][2]) begin
                    debug_watch_bank1_reg <= 2'd2;
                    debug_watch_addr1_reg <= pair_port1_address[1][24 +: 12];
                    debug_watch_data1_reg <= pair_port1_write_data[1][22 +: 11];
                    debug_watch_hit1_reg <= 1'b0;
                end else if (pair_port1_valid[1][3]) begin
                    debug_watch_bank1_reg <= 2'd3;
                    debug_watch_addr1_reg <= pair_port1_address[1][36 +: 12];
                    debug_watch_data1_reg <= pair_port1_write_data[1][33 +: 11];
                    debug_watch_hit1_reg <= 1'b0;
                end
                debug_watch_active_reg <= 1'b1;
            end
            if (debug_watch_active_reg && gather_response_valid) begin
                if (!debug_watch_hit0_reg &&
                    mem_port0_read[0][debug_watch_bank0_reg] &&
                    mem_port0_addr[0][debug_watch_bank0_reg] == debug_watch_addr0_reg) begin
                    debug_watch_read0_reg <= mem_port0_read_data[0][debug_watch_bank0_reg];
                    debug_watch_hit0_reg <= 1'b1;
                end
                if (!debug_watch_hit1_reg &&
                    mem_port1_read[0][debug_watch_bank1_reg] &&
                    mem_port1_addr[0][debug_watch_bank1_reg] == debug_watch_addr1_reg) begin
                    debug_watch_read1_reg <= mem_port1_read_data[0][debug_watch_bank1_reg];
                    debug_watch_hit1_reg <= 1'b1;
                end
            end
            if (debug_watch_active_reg && debug_watch_hit0_reg && debug_watch_hit1_reg) begin
                debug_post_emit_digest_reg <= {
                    debug_watch_data1_reg[7:0], debug_watch_read1_reg[7:0],
                    debug_watch_data0_reg[7:0], debug_watch_read0_reg[7:0]
                };
                debug_watch_active_reg <= 1'b0;
                debug_watch_done_reg <= 1'b1;
            end

            if (state == S_INIT && orbit_config_valid &&
                orbit_config_tag == init_orbit && init_pair == 0) begin
                if (init_orbit == 0)
                    debug_init_value0_reg <= orbit_config_data[32:22];
                else if (init_orbit == 1)
                    debug_init_value1_reg <= orbit_config_data[32:22];
            end
            if (verify_response_valid) begin
                debug_verify_digest_reg <= debug_verify_digest_reg ^ debug_verify_fold;
                debug_verify_parity_reg <= verify_parity_after;
                debug_verify_syndrome_reg <= syndrome_read_bits;
                debug_verify_address_reg <= syndrome_read_addr;
                // Reuse the qualification checkpoints for the final
                // response's per-slot sign bits.  This is four direct wires,
                // not a first-failure comparator, and exposes whether the
                // late parity mismatch comes from the physical posterior
                // read or from verify accumulation.
                debug_gather_checkpoint0_reg[3:0] <= {
                    engine_gather_posteriors[0][3*11+10],
                    engine_gather_posteriors[0][2*11+10],
                    engine_gather_posteriors[0][1*11+10],
                    engine_gather_posteriors[0][0*11+10]
                };
                debug_gather_checkpoint1_reg[3:0] <= {
                    engine_gather_posteriors[1][3*11+10],
                    engine_gather_posteriors[1][2*11+10],
                    engine_gather_posteriors[1][1*11+10],
                    engine_gather_posteriors[1][0*11+10]
                };
            end

            // Register one complete emit beat before the dynamic bank
            // crossbar. While pair 1 of the buffered beat is written, the
            // engines and template ROM present the following beat here. This
            // keeps the original two write clocks/beat throughput after the
            // one-clock group startup fill.
            if (emit_capture_valid) begin
                // Preserve the first engine-produced emit word in the
                // existing qualification bytes.  This is deliberately a
                // direct tap before the physical crossbar/DPB write; it
                // distinguishes stale engine output from lost writeback
                // without adding a wide comparator or state-machine cone.
                // The first active-record snapshot is captured at S_START;
                // leave the qualification words stable until the host reads
                // the probe after the shot.
                emit_buffer_valid <= 1'b1;
                emit_buffer_last <= pair0_emit_last && pair1_emit_last;
                emit_buffer_hash_masks <= capture_hash_masks;
                emit_buffer_hash_flips <= capture_hash_flips;
                emit_buffer_hash_words <= capture_hash_words;
                emit_buffer_valid_mask_flat[3:0] <= engine_out_valid_mask[0];
                emit_buffer_valid_mask_flat[7:4] <= engine_out_valid_mask[1];
                emit_buffer_valid_mask_flat[11:8] <= engine_out_valid_mask[2];
                emit_buffer_valid_mask_flat[15:12] <= engine_out_valid_mask[3];
                emit_buffer_new_signs_flat[3:0] <= engine_out_new_signs[0];
                emit_buffer_new_signs_flat[7:4] <= engine_out_new_signs[1];
                emit_buffer_new_signs_flat[11:8] <= engine_out_new_signs[2];
                emit_buffer_new_signs_flat[15:12] <= engine_out_new_signs[3];
                emit_buffer_edges_flat[23:0] <= engine_out_edges[0];
                emit_buffer_edges_flat[47:24] <= engine_out_edges[1];
                emit_buffer_edges_flat[71:48] <= engine_out_edges[2];
                emit_buffer_edges_flat[95:72] <= engine_out_edges[3];
                emit_buffer_posteriors_flat[43:0] <= engine_out_posteriors[0];
                emit_buffer_posteriors_flat[87:44] <= engine_out_posteriors[1];
                emit_buffer_posteriors_flat[131:88] <= engine_out_posteriors[2];
                emit_buffer_posteriors_flat[175:132] <= engine_out_posteriors[3];
                // Emit/writeback must use the same scalar mapper endpoints
                // as gather.  The old packed mapped_bank/mapped_address path
                // reintroduced lane aliases after an otherwise-correct first
                // read and corrupted the posterior store before verification.
                emit_buffer_bank_flat[1:0] <= explicit_bank00;
                emit_buffer_bank_flat[3:2] <= explicit_bank01;
                emit_buffer_bank_flat[5:4] <= explicit_bank02;
                emit_buffer_bank_flat[7:6] <= explicit_bank03;
                emit_buffer_bank_flat[9:8] <= explicit_bank10;
                emit_buffer_bank_flat[11:10] <= explicit_bank11;
                emit_buffer_bank_flat[13:12] <= explicit_bank12;
                emit_buffer_bank_flat[15:14] <= explicit_bank13;
                emit_buffer_bank_flat[17:16] <= explicit_bank20;
                emit_buffer_bank_flat[19:18] <= explicit_bank21;
                emit_buffer_bank_flat[21:20] <= explicit_bank22;
                emit_buffer_bank_flat[23:22] <= explicit_bank23;
                emit_buffer_bank_flat[25:24] <= explicit_bank30;
                emit_buffer_bank_flat[27:26] <= explicit_bank31;
                emit_buffer_bank_flat[29:28] <= explicit_bank32;
                emit_buffer_bank_flat[31:30] <= explicit_bank33;
                emit_buffer_address_flat[11:0] <= explicit_addr00;
                emit_buffer_address_flat[23:12] <= explicit_addr01;
                emit_buffer_address_flat[35:24] <= explicit_addr02;
                emit_buffer_address_flat[47:36] <= explicit_addr03;
                emit_buffer_address_flat[59:48] <= explicit_addr10;
                emit_buffer_address_flat[71:60] <= explicit_addr11;
                emit_buffer_address_flat[83:72] <= explicit_addr12;
                emit_buffer_address_flat[95:84] <= explicit_addr13;
                emit_buffer_address_flat[107:96] <= explicit_addr20;
                emit_buffer_address_flat[119:108] <= explicit_addr21;
                emit_buffer_address_flat[131:120] <= explicit_addr22;
                emit_buffer_address_flat[143:132] <= explicit_addr23;
                emit_buffer_address_flat[155:144] <= explicit_addr30;
                emit_buffer_address_flat[167:156] <= explicit_addr31;
                emit_buffer_address_flat[179:168] <= explicit_addr32;
                emit_buffer_address_flat[191:180] <= explicit_addr33;
            end

            case (state)
                S_IDLE: begin
                    if (start_valid) begin
                        success <= 1'b0; deferred <= 1'b0; logical_class <= 0; error <= 0;
                        if (!syndrome_nonzero) begin
                            success <= 1'b1;
                            deferred <= 1'b0;
                            logical_class <= 12'b0;
                            state <= S_DONE;
                        end else begin
                            state <= S_INIT;
                        end
                        profile_reg <= 0; sweep_reg <= 0;
                        time_ordinal_reg <= 0; group_ordinal_reg <= 0;
                        cycle_count <= 0; sweeps_used <= 0; profile_used <= 0;
                        init_orbit <= 0; init_pair <= 0; record_clear_addr <= 0;
                        record_clear_done <= 0; init_first <= 1;
                        relay_phase_reg <= 1'b0; relay_old_bank_reg <= 1'b0;
                        pending_record_valid <= 0; pending_write_lane <= 0;
                        prefetch_active <= 1'b0; prefetch_valid <= 1'b0;
                        orbit_config_valid <= 0; emit_buffer_valid <= 0;
                    end
                end

                S_TARGET_REMAP: begin
                    // Convert the packed pair target into standard four-lane
                    // nibbles only when rescue profile 6 is first reached.
                    // One target group is issued per clock; the final read
                    // response is written on the following clock.
                    remap_read_valid <= remap_read_en;
                    if (remap_read_en) begin
                        remap_write_addr_reg <= ({4'd0, remap_time} * 8'd18) +
                                                remap_group;
                        remap_write_group_reg <= remap_group;
                        remap_last_reg <= remap_time == 4'd12 &&
                                          remap_group == 5'd17;
                        if (remap_group == 5'd17) begin
                            remap_group <= 0;
                            remap_time <= remap_time + 1'b1;
                        end else begin
                            remap_group <= remap_group + 1'b1;
                        end
                    end
                    if (remap_read_valid && remap_last_reg) begin
                        remap_read_valid <= 1'b0;
                        remap_last_reg <= 1'b0;
                        init_orbit <= 0; init_pair <= 0;
                        record_clear_addr <= 0; record_clear_done <= 0;
                        state <= S_INIT;
                    end
                end

                S_INIT: begin
                    init_first <= 0;
                    if (!record_clear_done) begin
                        if (record_clear_addr == CHECKS-1)
                            record_clear_done <= 1;
                        else
                            record_clear_addr <= record_clear_addr + 1'b1;
                    end
                    if (orbit_config_valid && orbit_config_tag == init_orbit && init_pair == 8) begin
                        init_pair <= 0;
                        if (init_orbit == ORBITS-1) begin
                            orbit_config_valid <= 0;
                            init_orbit <= 0; record_issue_lane <= 0;
                            record_response_lane <= 0;
                            time_ordinal_reg <= 0; group_ordinal_reg <= 0;
                            state <= S_GROUP_SETUP;
                        end else begin
                            init_orbit <= init_orbit + 1'b1;
                            // The final write for this orbit simultaneously
                            // issued the next config ROM read; retain validity
                            // and let the common read tag advance with it.
                        end
                    end else if (orbit_config_valid && orbit_config_tag == init_orbit)
                        init_pair <= init_pair + 1'b1;
                end

                S_GROUP_SETUP: begin
                    if (time_ordinal_reg == 0 && group_ordinal_reg == 0) begin
                        emit_parity_reg <= 0;
                        sweep_parity_failed <= 0;
                        sweep_hash_failed <= 0;
                    end
                    active_schedule_time <= schedule_time;
                    active_schedule_group <= schedule_group;
                    active_schedule_coordinates <= schedule_coordinates;
                    active_schedule_coordinate0 <= schedule_coordinate0;
                    active_schedule_coordinate1 <= schedule_coordinate1;
                    active_schedule_coordinate2 <= schedule_coordinate2;
                    active_schedule_coordinate3 <= schedule_coordinate3;
                    active_template_beats <= meta_rom[schedule_time][3:0];
                    // Drop the prior group's synchronous-ROM tag before the
                    // record-load installs the new time/beat zero.
                    template_pending <= 1'b0;
                    template_emit_wait_reg <= 1'b0;
                    template_emit_data_valid_reg <= 1'b0;
                    template_kind <= TEMPLATE_GATHER;
                    template_beat <= 0;
                    record_issue_lane <= 0;
                    record_response_lane <= 0;
                    prefetch_active <= 1'b0;
                    prefetch_valid <= 1'b0;
                    state <= S_RECORD_LOAD;
                end

                S_RECORD_LOAD: begin
                    if (record_read_en) begin
                        record_response_lane <= record_issue_lane;
                        record_issue_lane <= record_issue_lane + 1'b1;
                    end
                    if (record_read_valid) begin
                        active_record[record_response_lane] <= record_read_data;
                        active_signs[record_response_lane] <= record_read_data[RECORD_WIDTH-1:RECORD_SIGN_LSB];
                        if (RELAY_MODE && relay_phase_reg && record_new_read_valid) begin
                            active_new_record[record_response_lane] <= record_new_read_data;
                            active_new_signs[record_response_lane] <= record_new_read_data[RECORD_WIDTH-1:RECORD_SIGN_LSB];
                        end
                    end
                    if (record_read_valid && record_response_lane == 3) begin
                        record_issue_lane <= 0;
                        state <= S_START;
                    end
                end

                S_START: begin
                    if (pair0_start_ready && pair1_start_ready) begin
                        active_syndrome_bits <= run_syndrome_bits;
                        gather_issue_beat <= 0; gather_response_valid <= 0;
                        emit_beat <= 0; emit_phase <= 0; emit_buffer_valid <= 0;
                        prefetch_issue_lane <= 0;
                        prefetch_response_lane <= 0;
                        prefetch_valid <= 1'b0;
                        prefetch_active <= PREFETCH_RECORDS &&
                                           !(time_ordinal_reg == 12 && group_ordinal_reg == 17);
                        state <= S_RUN;
                    end
                end

                S_RUN: begin
                    if (gather_issue_valid)
                        gather_issue_beat <= gather_issue_beat + 1'b1;
                    if (PREFETCH_RECORDS && prefetch_active) begin
                        if (record_read_en) begin
                            prefetch_response_lane <= prefetch_issue_lane;
                            prefetch_issue_lane <= prefetch_issue_lane + 1'b1;
                        end
                        if (record_read_valid) begin
                        // Legacy mode never consumes active_new_record; reuse
                        // that resident four-record array as the prefetch
                        // buffer instead of instantiating a second RAM16 bank.
                        active_new_record[prefetch_response_lane] <= record_read_data;
                            if (prefetch_response_lane == 3) begin
                                prefetch_active <= 1'b0;
                                prefetch_valid <= 1'b1;
                            end
                        end
                    end
                    if (emit_buffer_valid) begin
                        if (!emit_phase)
                            emit_phase <= 1;
                        else if (emit_commit) begin
                            emit_phase <= 0;
                            emit_parity_reg <= emit_parity_after;
                            for (seq_lane = 0; seq_lane < 4; seq_lane = seq_lane + 1)
                            active_signs[seq_lane] <= signs_after_emit[seq_lane];
                            if (emit_buffer_last) begin
                                sweep_parity_failed <= sweep_parity_failed ||
                                    emit_parity_mismatch;
                                emit_parity_reg <= 0;
                                emit_buffer_valid <= 0;
                                if (!RELAY_MODE || !relay_phase_reg) begin
                                    pending_record_valid <= 1;
                                    pending_write_lane <= 0;
                                    for (seq_lane = 0; seq_lane < 4; seq_lane = seq_lane + 1) begin
                                        pending_record[seq_lane] <= {
                                            signs_after_emit[seq_lane], engine_new_argmin[seq_lane],
                                            engine_new_min2[seq_lane], engine_new_min1[seq_lane]
                                        };
                                        case (seq_lane)
                                            0: pending_record_addr[seq_lane] <= check_id(
                                                active_schedule_time,
                                                active_schedule_coordinate0);
                                            1: pending_record_addr[seq_lane] <= check_id(
                                                active_schedule_time,
                                                active_schedule_coordinate1);
                                            2: pending_record_addr[seq_lane] <= check_id(
                                                active_schedule_time,
                                                active_schedule_coordinate2);
                                            default: pending_record_addr[seq_lane] <= check_id(
                                                active_schedule_time,
                                                active_schedule_coordinate3);
                                        endcase
                                    end
                                end else begin
                                    pending_record_valid <= 0;
                                    pending_write_lane <= 0;
                                end
                                emit_beat <= 0; gather_issue_beat <= 0;
                                if (time_ordinal_reg == 12 && group_ordinal_reg == 17)
                                    state <= S_SWEEP_DECIDE;
                                else begin
                                    if (group_ordinal_reg == 17) begin
                                        group_ordinal_reg <= 0;
                                        time_ordinal_reg <= time_ordinal_reg + 1'b1;
                                    end else
                                        group_ordinal_reg <= group_ordinal_reg + 1'b1;
                                    active_schedule_time <= schedule_time;
                                    active_schedule_group <= schedule_group;
                                    active_schedule_coordinates <= schedule_coordinates;
                                    active_schedule_coordinate0 <= schedule_coordinate0;
                                    active_schedule_coordinate1 <= schedule_coordinate1;
                                    active_schedule_coordinate2 <= schedule_coordinate2;
                                    active_schedule_coordinate3 <= schedule_coordinate3;
                                    active_template_beats <= meta_rom[schedule_time][3:0];
                                    template_pending <= 1'b0;
                                    template_emit_wait_reg <= 1'b0;
                                    template_emit_data_valid_reg <= 1'b0;
                                    template_kind <= TEMPLATE_GATHER;
                                    template_beat <= 0;
                                    if (PREFETCH_RECORDS && prefetch_valid) begin
                                        for (seq_lane = 0; seq_lane < 4; seq_lane = seq_lane + 1) begin
                                            active_record[seq_lane] <= active_new_record[seq_lane];
                                            active_signs[seq_lane] <= active_new_record[seq_lane][RECORD_WIDTH-1:RECORD_SIGN_LSB];
                                        end
                                        prefetch_valid <= 1'b0;
                                        prefetch_active <= 1'b0;
                                        state <= S_START;
                                    end else begin
                                        record_issue_lane <= 0;
                                        state <= S_RECORD_LOAD;
                                    end
                                end
                            end else
                                emit_beat <= emit_beat + 1'b1;
                        end
                    end
                end

                S_SWEEP_DECIDE: begin
                    // Terminal emit and residual-hash update share a clock
                    // edge. Sample the post-emit hash while the four pending
                    // record writes drain; decision then uses this sticky
                    // value, never a pre-update combinational hash.
                    if (pending_record_valid)
                        sweep_hash_failed <= sweep_hash_failed ||
                                             (residual_hash != '0);
                    else begin
                    sweeps_used <= sweeps_used + 1'b1;
                    if (RELAY_MODE && !relay_phase_reg && hash_possible_zero) begin
                        // Phase-0 already has a hash-consistent correction.
                        // Verify it before paying for the Relay scatter leg;
                        // hash collisions remain exact-safe because failure
                        // below still enters phase 1.
                        verify_time_ordinal <= 0; verify_group_ordinal <= 0;
                        verify_failed <= 0;
                        state <= S_VERIFY_SETUP;
                    end else if (RELAY_MODE && !relay_phase_reg) begin
                        // Check phase wrote a complete next-record bank while
                        // P stayed frozen. Revisit the same schedule with
                        // both records resident for the flooding scatter.
                        relay_phase_reg <= 1'b1;
                        time_ordinal_reg <= 0; group_ordinal_reg <= 0;
                        record_issue_lane <= 0; state <= S_GROUP_SETUP;
                    end else if (RELAY_MODE && relay_phase_reg && !hash_possible_zero &&
                                 (sweep_reg + 1'b1 < profile_max_sweeps)) begin
                        // A nonzero residual hash is an exact-safe rejection:
                        // this correction cannot satisfy the detector word,
                        // so skip the 234-group replay and start the next
                        // Relay leg directly.
                        relay_phase_reg <= 1'b0;
                        relay_old_bank_reg <= ~relay_old_bank_reg;
                        sweep_reg <= sweep_reg + 1'b1;
                        time_ordinal_reg <= 0; group_ordinal_reg <= 0;
                        record_issue_lane <= 0; state <= S_GROUP_SETUP;
                    end else if (!RELAY_MODE && INLINE_EXACT_CHECK &&
                                 !FORCE_EXACT_REPLAY) begin
                        // Emit parity is exact for every scheduled detector.
                        // Advance directly on failed parity; skip serial
                        // 936-check replay on both pass and fail.
                        if (!sweep_parity_failed && !sweep_hash_failed &&
                            hash_possible_zero) begin
                            if (RISK_DEFER_FINAL_SWEEP &&
                                (sweep_reg + 1'b1 >= profile_max_sweeps)) begin
                                deferred <= 1; success <= 0;
                                profile_used <= profile_reg;
                                state <= S_DONE;
                            end else begin
                                state <= S_LOGICAL_SETUP;
                            end
                        end else if (sweep_reg + 1'b1 < profile_max_sweeps) begin
                            sweep_reg <= sweep_reg + 1'b1;
                            time_ordinal_reg <= 0; group_ordinal_reg <= 0;
                            record_issue_lane <= 0; state <= S_GROUP_SETUP;
                        end else if (profile_reg < MAX_PROFILE) begin
                            profile_reg <= profile_reg + 1'b1;
                            profile_used <= profile_reg + 1'b1; sweep_reg <= 0;
                            init_orbit <= 0; init_pair <= 0; record_clear_addr <= 0;
                            record_clear_done <= 0; init_first <= 1;
                            relay_phase_reg <= 1'b0; relay_old_bank_reg <= 1'b0;
                            pending_record_valid <= 0; orbit_config_valid <= 0;
                            if (profile_reg + 1'b1 >= 3'd6) begin
                                remap_time <= 0; remap_group <= 0;
                                remap_read_valid <= 1'b0; remap_last_reg <= 1'b0;
                                state <= S_TARGET_REMAP;
                            end else
                                state <= S_INIT;
                        end else begin
                            deferred <= 1; success <= 0; profile_used <= profile_reg;
                            state <= S_DONE;
                        end
                    end else if (HASH_ONLY_ACCEPT && !sweep_hash_failed &&
                                 hash_possible_zero) begin
                        // Fast experimental certificate.  Production keeps
                        // this off: a compressed hash is not a proof of the
                        // full detector word.
                        state <= S_LOGICAL_SETUP;
                    end else if (FORCE_EXACT_REPLAY ?
                                 // The emit-time parity tap is only a
                                 // diagnostic: each variable can be touched
                                 // again by a later check, so a per-check
                                 // emit snapshot is not the final syndrome.
                                 // Exact replay is the authority and must not
                                 // be blocked by that stale intermediate.
                                 ((!sweep_hash_failed && hash_possible_zero &&
                                   ((EXACT_VERIFY_INTERVAL <= 1) ||
                                    (EXACT_VERIFY_INTERVAL == 2 && sweep_reg[0]))) ||
                                  (sweep_reg + 1'b1 >= profile_max_sweeps)) :
                                 hash_possible_zero) begin
                        verify_time_ordinal <= 0; verify_group_ordinal <= 0;
                        // Clear once at the start of the complete 936-check
                        // replay.  S_VERIFY_SETUP is revisited for every
                        // four-check group, so clearing there loses failures
                        // from all but the final group and can turn a hash
                        // collision into a false syndrome acceptance.
                        verify_failed <= 0;
                        state <= S_VERIFY_SETUP;
                    end else if (sweep_reg + 1'b1 < profile_max_sweeps) begin
                        sweep_reg <= sweep_reg + 1'b1;
                        time_ordinal_reg <= 0; group_ordinal_reg <= 0;
                        record_issue_lane <= 0; state <= S_GROUP_SETUP;
                    end else if (profile_reg < MAX_PROFILE) begin
                        profile_reg <= profile_reg + 1'b1; profile_used <= profile_reg + 1'b1;
                        sweep_reg <= 0; init_orbit <= 0; init_pair <= 0;
                        relay_phase_reg <= 1'b0; relay_old_bank_reg <= 1'b0;
                        record_clear_addr <= 0; record_clear_done <= 0; init_first <= 1;
                        pending_record_valid <= 0; orbit_config_valid <= 0;
                        if (profile_reg + 1'b1 >= 3'd6) begin
                            remap_time <= 0; remap_group <= 0;
                            remap_read_valid <= 1'b0; remap_last_reg <= 1'b0;
                            state <= S_TARGET_REMAP;
                        end else
                            state <= S_INIT;
                    end else begin
                        deferred <= 1; success <= 0; profile_used <= profile_reg;
                        state <= S_DONE;
                    end
                    end
                end

                S_VERIFY_SETUP: begin
                    active_verify_time <= verify_time;
                    active_verify_coordinates <= verify_coordinates;
                    active_verify_coordinate0 <= verify_coordinate0;
                    active_verify_coordinate1 <= verify_coordinate1;
                    active_verify_coordinate2 <= verify_coordinate2;
                    active_verify_coordinate3 <= verify_coordinate3;
                    active_verify_template_beats <= meta_rom[verify_time][3:0];
                    verify_issue_beat <= 0; verify_response_valid <= 0;
                    verify_parity <= 0;
                    state <= S_VERIFY_RUN;
                end

                S_VERIFY_RUN: begin
                    if (verify_issue_valid)
                        verify_issue_beat <= verify_issue_beat + 1'b1;
                    if (verify_response_valid) begin
                        verify_parity <= verify_parity_after;
                        if (verify_response_last) begin
                            for (seq_lane = 0; seq_lane < 4; seq_lane = seq_lane + 1)
                                if (verify_parity_after[seq_lane] != syndrome_read_bits[seq_lane])
                                    verify_failed <= 1;
                            // A failed group is a proof of rejection.  Do
                            // not replay the remaining 233 groups; jump to
                            // one terminal group so the existing failure
                            // transition stays centralized and exact.
                            if ((((verify_parity_after[0] != syndrome_read_bits[0]) ||
                                  (verify_parity_after[1] != syndrome_read_bits[1]) ||
                                  (verify_parity_after[2] != syndrome_read_bits[2]) ||
                                  (verify_parity_after[3] != syndrome_read_bits[3])) &&
                                 !(verify_time_ordinal == 12 && verify_group_ordinal == 17))) begin
                                verify_failed <= 1'b1;
                                verify_time_ordinal <= 12;
                                verify_group_ordinal <= 17;
                                verify_issue_beat <= 0;
                                verify_parity <= 0;
                                state <= S_VERIFY_SETUP;
                            end else if (verify_time_ordinal == 12 && verify_group_ordinal == 17) begin
                                if (!verify_failed &&
                                    (verify_parity_after == syndrome_read_bits) &&
                                    !(RISK_DEFER_FINAL_SWEEP &&
                                      (sweep_reg + 1'b1 >= profile_max_sweeps)))
                                    state <= S_LOGICAL_SETUP;
                                else if (!verify_failed &&
                                         (verify_parity_after == syndrome_read_bits) &&
                                         RISK_DEFER_FINAL_SWEEP &&
                                         (sweep_reg + 1'b1 >= profile_max_sweeps)) begin
                                    // Exact syndrome satisfaction is necessary
                                    // but not sufficient for logical quality at
                                    // the bounded terminal budget. Hand final
                                    // candidates to resident CPU telescope.
                                    deferred <= 1'b1; success <= 1'b0;
                                    profile_used <= profile_reg; state <= S_DONE;
                                end
                                else if (RELAY_MODE && !relay_phase_reg) begin
                                    // A phase-0 hash collision: phase 1 is
                                    // still required before spending another
                                    // profile/sweep budget.
                                    relay_phase_reg <= 1'b1;
                                    time_ordinal_reg <= 0; group_ordinal_reg <= 0;
                                    record_issue_lane <= 0; state <= S_GROUP_SETUP;
                                end
                                else if (RELAY_MODE && relay_phase_reg &&
                                         (sweep_reg + 1'b1 < profile_max_sweeps)) begin
                                    // A failed verify after scatter starts a
                                    // fresh Relay leg on the newly committed
                                    // record bank before trying another check
                                    // budget. The bank flip is atomic here.
                                    relay_phase_reg <= 1'b0;
                                    relay_old_bank_reg <= ~relay_old_bank_reg;
                                    sweep_reg <= sweep_reg + 1'b1;
                                    time_ordinal_reg <= 0; group_ordinal_reg <= 0;
                                    record_issue_lane <= 0; state <= S_GROUP_SETUP;
                                end
                                else if (sweep_reg + 1'b1 < profile_max_sweeps) begin
                                    sweep_reg <= sweep_reg + 1'b1;
                                    time_ordinal_reg <= 0; group_ordinal_reg <= 0;
                                    record_issue_lane <= 0; state <= S_GROUP_SETUP;
                                end else if (profile_reg < MAX_PROFILE) begin
                                    profile_reg <= profile_reg + 1'b1;
                                    profile_used <= profile_reg + 1'b1; sweep_reg <= 0;
                                    relay_phase_reg <= 1'b0; relay_old_bank_reg <= 1'b0;
                                    init_orbit <= 0; init_pair <= 0; record_clear_addr <= 0;
                                    record_clear_done <= 0; init_first <= 1;
                                    if (profile_reg + 1'b1 >= 3'd6) begin
                                        remap_time <= 0; remap_group <= 0;
                                        remap_read_valid <= 1'b0; remap_last_reg <= 1'b0;
                                        state <= S_TARGET_REMAP;
                                    end else
                                        state <= S_INIT;
                                    orbit_config_valid <= 0;
                                end else begin
                                    deferred <= 1; state <= S_DONE;
                                end
                            end else begin
                                if (verify_group_ordinal == 17) begin
                                    verify_group_ordinal <= 0;
                                    verify_time_ordinal <= verify_time_ordinal + 1'b1;
                                end else
                                    verify_group_ordinal <= verify_group_ordinal + 1'b1;
                                state <= S_VERIFY_SETUP;
                            end
                        end
                    end
                end

                S_LOGICAL_SETUP: begin
                    logical_orbit <= 0; logical_local <= 0;
                    logical_response_valid <= 0;
                    logical_response_valid_stage <= 0;
                    logical_response_last_stage <= 0;
                    logical_response_orbit_stage <= 0;
                    logical_response_local_stage <= 0;
                    logical_response_valid_stage2 <= 0;
                    logical_response_last_stage2 <= 0;
                    logical_response_orbit_stage2 <= 0;
                    logical_response_local_stage2 <= 0;
                    logical_all_issued <= 0;
                    logical_accum <= 0;
                    state <= S_LOGICAL_RUN;
                end

                S_LOGICAL_RUN: begin
                    if (LOGICAL_READ_RESPONSE_STAGES <= 1) begin
                        logical_response_valid <= logical_response_valid_stage;
                        logical_response_last <= logical_response_last_stage;
                        logical_response_orbit <= logical_response_orbit_stage;
                        logical_response_local <= logical_response_local_stage;
                    end else begin
                        logical_response_valid <= logical_response_valid_stage2;
                        logical_response_last <= logical_response_last_stage2;
                        logical_response_orbit <= logical_response_orbit_stage2;
                        logical_response_local <= logical_response_local_stage2;
                        logical_response_valid_stage2 <= logical_response_valid_stage;
                        logical_response_last_stage2 <= logical_response_last_stage;
                        logical_response_orbit_stage2 <= logical_response_orbit_stage;
                        logical_response_local_stage2 <= logical_response_local_stage;
                    end
                    logical_response_valid_stage <= logical_issue_valid;
                    if (logical_issue_valid) begin
                        logical_response_orbit_stage <= logical_orbit;
                        logical_response_local_stage <= logical_local;
                        logical_response_last_stage <=
                            logical_orbit == ORBITS-1 && logical_local == 16;
                        if (logical_orbit == ORBITS-1 && logical_local == 16)
                            logical_all_issued <= 1'b1;
                        if (logical_local == 16) begin
                            logical_local <= 0;
                            if (logical_orbit < ORBITS-1)
                                logical_orbit <= logical_orbit + 1'b1;
                        end else
                            logical_local <= logical_local + 2'd2;
                    end
                    if (logical_response_valid) begin
                        logical_accum <= logical_accum_after;
                        if (logical_response_last) begin
                            logical_class <= logical_accum_after; success <= 1; deferred <= 0;
                            profile_used <= profile_reg; state <= S_DONE;
                        end
                    end
                end

                S_DONE: if (done_ready) state <= S_IDLE;
                S_ERROR: begin
                    // Keep error sticky until host shot_clear, then return
                    // through the same idle boundary used by normal shots.
                    // The outer shot_clear reset clears the full datapath;
                    // this explicit transition also documents/reinforces the
                    // recovery contract for guarded/synthesized FSM paths.
                    if (shot_clear) begin
                        state <= S_IDLE;
                        error <= 1'b0;
                    end else begin
                        error <= 1'b1;
                    end
                end
                default: begin
                    // Preserve the pre-error state.  A nonzero value here
                    // distinguishes synthesized FSM corruption from the
                    // guarded datapath error classes above.
                    debug_invalid_state_reg <= state;
                    debug_default_hit <= 1'b1;
                    state <= S_ERROR;
                end
            endcase

            if (structural_error_pending) begin
                error <= 1; state <= S_ERROR;
            end
        end
    end

    assign logical_issue_valid = state == S_LOGICAL_RUN && !logical_all_issued &&
        orbit_config_valid && orbit_config_tag == logical_orbit;
endmodule
