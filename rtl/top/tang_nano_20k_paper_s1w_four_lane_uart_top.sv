// Board endpoint for the exact Paper Gross144 four-lane S1W decoder.
//
// UART uses the repository MT/CRC32 frame format at an exact 3 Mbaud from the
// board's 27 MHz oscillator. Command 0x31 carries the 936 detector bits as 117
// little-endian packed bytes. A stop-and-wait CDC snapshot feeds the
// 40.5 MHz production decoder without including transport time in `cycle_count`.
module tang_nano_20k_paper_s1w_four_lane_uart_top #(
    parameter integer BASIS_ID = 0
) (
    input  logic clk27,
    input  logic rst_n,
    input  logic uart_rx_pin,
    output logic uart_tx_pin,
    output logic led
);
    localparam logic [7:0] CMD_PING = 8'h01;
    localparam logic [7:0] CMD_PROBE = 8'h32;
    localparam logic [7:0] CMD_DECODE = 8'h31;
    localparam integer SYNDROME_BYTES = 117;
    localparam integer RESPONSE_BYTES = 11;

    logic clk51, pll_locked;
    logic [1:0] core_reset_sync = 2'b11;
    logic core_rst, uart_rst;
    mitten_clock_51 u_clock (
        .clk27(clk27), .clk51(clk51), .locked(pll_locked)
    );
    // Pin 88 (`rst_n`) is physically low on the attached board even when no
    // button is held.  Do not make the endpoint depend on that input.  The
    // PLL lock edge plus power-up register values provide deterministic boot
    // reset for both clock domains.
    always_ff @(posedge clk51 or negedge pll_locked) begin
        if (!pll_locked)
            core_reset_sync <= 2'b11;
        else
            core_reset_sync <= {core_reset_sync[0], 1'b0};
    end
    assign core_rst = core_reset_sync[1];

    // Keep transport alive even when the decoder PLL is slow or fails to
    // lock.  The UART is the bring-up/diagnostic path; only the decoder core
    // stays behind pll_locked.
    (* ASYNC_REG = "TRUE" *) logic pll_locked_uart_meta = 1'b0;
    (* ASYNC_REG = "TRUE" *) logic pll_locked_uart = 1'b0;
    logic [15:0] uart_boot_count = 16'd0;
    logic uart_boot_done = 1'b0;
    always_ff @(posedge clk27) begin
        pll_locked_uart_meta <= pll_locked;
        pll_locked_uart <= pll_locked_uart_meta;
        if (!uart_boot_done) begin
            if (&uart_boot_count)
                uart_boot_done <= 1'b1;
            else
                uart_boot_count <= uart_boot_count + 1'b1;
        end
    end
    assign uart_rst = !uart_boot_done;

    logic uart_rx_valid;
    logic [7:0] uart_rx_data;
    logic uart_tx_start, uart_tx_busy;
    logic [7:0] uart_tx_data;
    uart_rx #(.CLK_HZ(27_000_000), .BAUD(3_000_000)) u_uart_rx (
        .clk(clk27), .rst(uart_rst), .rx(uart_rx_pin),
        .data_valid(uart_rx_valid), .data(uart_rx_data)
    );
    uart_tx #(.CLK_HZ(27_000_000), .BAUD(3_000_000)) u_uart_tx (
        .clk(clk27), .rst(uart_rst), .start(uart_tx_start),
        .data(uart_tx_data), .busy(uart_tx_busy), .tx(uart_tx_pin)
    );

    logic frame_rx_ready, frame_valid, frame_ready, frame_error;
    logic [7:0] frame_command;
    logic [15:0] frame_sequence, frame_length;
    logic frame_payload_byte_valid;
    logic [15:0] frame_payload_byte_index;
    logic [7:0] frame_payload_byte_data;
    logic frame_tx_start, frame_tx_busy, frame_tx_valid, frame_tx_ready;
    logic [7:0] frame_tx_command;
    logic [15:0] frame_tx_sequence, frame_tx_length;
    logic [SYNDROME_BYTES*8-1:0] frame_tx_payload;

    uart_framer #(
        .MAX_PAYLOAD(SYNDROME_BYTES), .STORE_RX_PAYLOAD(0)
    ) u_framer (
        .clk(clk27), .rst(uart_rst),
        .rx_valid(uart_rx_valid), .rx_ready(frame_rx_ready), .rx_data(uart_rx_data),
        .frame_valid(frame_valid), .frame_ready(frame_ready),
        .frame_command(frame_command), .frame_sequence(frame_sequence),
        .frame_length(frame_length), .frame_payload(),
        .rx_payload_byte_valid(frame_payload_byte_valid),
        .rx_payload_byte_index(frame_payload_byte_index),
        .rx_payload_byte_data(frame_payload_byte_data),
        .frame_error(frame_error), .tx_start(frame_tx_start),
        .tx_command(frame_tx_command), .tx_sequence(frame_tx_sequence),
        .tx_length(frame_tx_length), .tx_payload(frame_tx_payload),
        .tx_busy(frame_tx_busy), .tx_valid(frame_tx_valid),
        .tx_ready(frame_tx_ready), .tx_data(uart_tx_data)
    );
    assign frame_tx_ready = !uart_tx_busy;
    assign uart_tx_start = frame_tx_valid && frame_tx_ready;

    logic payload_read_en;
    logic [6:0] payload_read_addr;
    logic [7:0] payload_read_data;
    uart_payload_cdc_ram #(.DEPTH(SYNDROME_BYTES)) u_payload_buffer (
        .write_clk(clk27), .write_rst(uart_rst),
        .write_en(frame_payload_byte_valid &&
                  frame_payload_byte_index < SYNDROME_BYTES),
        .write_addr(frame_payload_byte_index[6:0]),
        .write_data(frame_payload_byte_data),
        .read_clk(clk51), .read_rst(core_rst),
        .read_en(payload_read_en), .read_addr(payload_read_addr),
        .read_data(payload_read_data)
    );

    // Stop-and-wait request snapshot. The framer holds sequence/payload stable
    // until `frame_ready`; the core consumes the snapshot before that release.
    logic request_toggle, request_latched;
    logic [15:0] request_sequence_latched;
    logic [31:0] parser_error_count;
    (* ASYNC_REG = "TRUE" *) logic result_toggle_meta, result_toggle_sync;
    logic result_toggle_seen;

    logic result_toggle_core;
    logic [15:0] result_sequence_core;
    logic [7:0] result_status_core;
    logic [11:0] result_logical_core;
    logic [31:0] result_cycles_core;
    logic [15:0] result_sweeps_core;
    logic [2:0] result_profile_core;

    always_comb begin
        frame_tx_command = CMD_DECODE | 8'h80;
        frame_tx_sequence = result_sequence_core;
        frame_tx_length = RESPONSE_BYTES;
        frame_tx_payload = '0;
        frame_tx_payload[7:0] = result_status_core;
        frame_tx_payload[15:8] = {5'd0, result_profile_core};
        frame_tx_payload[23:16] = {4'd0, result_logical_core[3:0]};
        frame_tx_payload[31:24] = result_logical_core[11:4];
        frame_tx_payload[63:32] = result_cycles_core;
        frame_tx_payload[79:64] = result_sweeps_core;
        frame_tx_payload[87:80] = BASIS_ID[7:0];
        // Immediate transport/status probe.  Byte 0 is the success marker;
        // byte 1 exposes PLL lock, then live request/core diagnostics.  This
        // remains available while a decode is running.
        if (frame_valid && !frame_tx_busy &&
            (frame_command == CMD_PING || frame_command == CMD_PROBE) &&
            (frame_length == 0 || frame_length == SYNDROME_BYTES)) begin
            frame_tx_command = frame_command | 8'h80;
            frame_tx_sequence = frame_sequence;
            frame_tx_length = 16'd47;
            frame_tx_payload = '0;
            frame_tx_payload[15:8] = {7'd0, pll_locked_uart};
            frame_tx_payload[23:16] = {7'd0, request_latched};
            frame_tx_payload[31:24] = {7'd0, result_toggle_core};
            frame_tx_payload[39:32] = {5'd0, control_state};
            frame_tx_payload[47:40] = {7'd0, decoder_busy};
            frame_tx_payload[55:48] = {7'd0, decoder_error};
            frame_tx_payload[63:56] = parser_error_count[7:0];
            frame_tx_payload[71:64] = decoder_state_debug[7:0];
            frame_tx_payload[79:72] = {3'd0, decoder_state_debug[12:8]};
            frame_tx_payload[87:80] = {4'd0, decoder_guards_debug};
            frame_tx_payload[95:88] = {3'd0, load_time, 1'b0};
            frame_tx_payload[103:96] = {3'd0, load_group};
            frame_tx_payload[111:104] = decoder_map_detail_debug;
            frame_tx_payload[119:112] = decoder_fault_conflict_debug;
            frame_tx_payload[127:120] = {4'd0, decoder_fault_time_debug};
            frame_tx_payload[135:128] = {3'd0, decoder_fault_group_debug};
            frame_tx_payload[143:136] = {4'd0, decoder_fault_beat_debug};
            frame_tx_payload[167:144] = decoder_fault_addr0_debug;
            frame_tx_payload[191:168] = decoder_fault_addr1_debug;
            frame_tx_payload[127:96] = decoder_early_gathers_debug;
            frame_tx_payload[143:128] = decoder_gather_checkpoint0_debug;
            frame_tx_payload[159:144] = decoder_gather_checkpoint1_debug;
            frame_tx_payload[191:160] = decoder_early_map_banks_debug;
            frame_tx_payload[223:192] = decoder_early_raw0_debug;
            frame_tx_payload[255:224] = decoder_early_raw1_debug;
            frame_tx_payload[271:256] = decoder_early_response_banks_debug;
            frame_tx_payload[279:272] = decoder_early_response_port1_debug;
            // Late-path qualification: final verify fold/parity/syndrome.
            // Appended to preserve the existing first-read probe offsets.
            frame_tx_payload[311:280] = decoder_verify_digest_debug;
            frame_tx_payload[315:312] = decoder_verify_parity_debug;
            frame_tx_payload[319:316] = decoder_verify_syndrome_debug;
            frame_tx_payload[327:320] = decoder_verify_address_debug;
            frame_tx_payload[331:328] = decoder_guards_debug;
            frame_tx_payload[343:336] = decoder_gather_count_debug;
            frame_tx_payload[375:344] = decoder_gather_digest_debug;
        end
    end

    always_ff @(posedge clk27) begin
        if (uart_rst) begin
            request_toggle <= 1'b0;
            request_latched <= 1'b0;
            request_sequence_latched <= 1'b0;
            result_toggle_meta <= 1'b0;
            result_toggle_sync <= 1'b0;
            result_toggle_seen <= 1'b0;
            frame_ready <= 1'b0;
            frame_tx_start <= 1'b0;
            parser_error_count <= 0;
        end else begin
            frame_ready <= 1'b0;
            frame_tx_start <= 1'b0;
            result_toggle_meta <= result_toggle_core;
            result_toggle_sync <= result_toggle_meta;
            if (frame_error)
                parser_error_count <= parser_error_count + 1'b1;
            if (frame_valid && !frame_tx_busy &&
                (frame_command == CMD_PING || frame_command == CMD_PROBE) &&
                (frame_length == 0 || frame_length == SYNDROME_BYTES)) begin
                frame_tx_start <= 1'b1;
                frame_ready <= 1'b1;
            end else if (frame_valid && !request_latched &&
                         frame_command == CMD_DECODE &&
                         frame_length == SYNDROME_BYTES) begin
                request_latched <= 1'b1;
                request_sequence_latched <= frame_sequence;
                request_toggle <= !request_toggle;
                // CRC-verified payload is already in the dual-clock RAM.
                // Release the framer now so PING/status can diagnose a core
                // that stalls; the sequence is held separately for the core.
                frame_ready <= 1'b1;
            end
            if (result_toggle_sync != result_toggle_seen && !frame_tx_busy) begin
                result_toggle_seen <= result_toggle_sync;
                frame_tx_start <= 1'b1;
                frame_ready <= 1'b1;
                request_latched <= 1'b0;
            end
        end
    end

    localparam logic [2:0] C_IDLE  = 3'd0;
    localparam logic [2:0] C_CLEAR = 3'd1;
    localparam logic [2:0] C_LOAD  = 3'd2;
    localparam logic [2:0] C_START = 3'd3;
    localparam logic [2:0] C_WAIT  = 3'd4;
    logic [2:0] control_state;
    (* ASYNC_REG = "TRUE" *) logic request_toggle_meta, request_toggle_sync;
    logic request_toggle_seen;
    logic [15:0] request_sequence_core;
    logic [3:0] load_time;
    logic [4:0] load_group;
    logic [31:0] syndrome_load_digest;

    logic shot_clear, syndrome_write_valid, start_valid, start_ready;
    logic done_valid, done_ready, success, deferred, decoder_error, decoder_busy;
    logic [3:0] syndrome_write_bits;
    logic [11:0] logical_class;
    logic [31:0] cycle_count;
    logic [15:0] sweeps_used;
    logic [2:0] profile_used;
    logic [12:0] decoder_state_debug;
    logic [3:0] decoder_guards_debug;
    logic [7:0] decoder_map_detail_debug;
    logic [7:0] decoder_fault_conflict_debug;
    logic [23:0] decoder_fault_addr0_debug;
    logic [23:0] decoder_fault_addr1_debug;
    logic [3:0] decoder_fault_time_debug;
    logic [4:0] decoder_fault_group_debug;
    logic [3:0] decoder_fault_beat_debug;
    logic [12:0] decoder_invalid_state_debug;
    logic [22:0] decoder_fault_descriptor0_debug;
    logic [22:0] decoder_fault_descriptor1_debug;
    logic [31:0] decoder_fault_banks_debug;
    logic [31:0] decoder_gather_digest_debug;
    logic [31:0] decoder_early_gathers_debug;
    logic [31:0] decoder_early_raw0_debug;
    logic [31:0] decoder_early_raw1_debug;
    logic [15:0] decoder_early_response_banks_debug;
    logic [7:0] decoder_early_response_port1_debug;
    logic [31:0] decoder_early_map_banks_debug;
    logic [27:0] decoder_early_map_coordinates_debug;
    logic [15:0] decoder_gather_checkpoint0_debug;
    logic [15:0] decoder_gather_checkpoint1_debug;
    logic [31:0] decoder_verify_digest_debug;
    logic [3:0] decoder_verify_parity_debug;
    logic [3:0] decoder_verify_syndrome_debug;
    logic [7:0] decoder_verify_address_debug;
    logic [7:0] decoder_gather_count_debug;

    assign shot_clear = control_state == C_CLEAR;
    assign syndrome_write_valid = control_state == C_LOAD;
    assign syndrome_write_bits = load_group[0] ?
                                 payload_read_data[7:4] : payload_read_data[3:0];
    assign start_valid = control_state == C_START;
    assign done_ready = control_state == C_WAIT;

    // The CDC payload RAM is intentionally an asynchronous stable snapshot.
    // Address the byte consumed by the current nibble directly; the old
    // synchronous-ROM prefetch (+1 on odd groups) skipped byte zero after the
    // RAM was changed to async read and shifted every nonzero syndrome.
    always_comb begin
        payload_read_en = 1'b0;
        payload_read_addr = 0;
        if (control_state == C_CLEAR) begin
            payload_read_en = 1'b1;
            payload_read_addr = 0;
        end else if (control_state == C_LOAD) begin
            payload_read_en = 1'b1;
            payload_read_addr = (load_time * 18 + load_group) >> 1;
        end
    end

    always_ff @(posedge clk51) begin
        if (core_rst) begin
            control_state <= C_IDLE;
            request_toggle_meta <= 1'b0;
            request_toggle_sync <= 1'b0;
            request_toggle_seen <= 1'b0;
            request_sequence_core <= 0;
            load_time <= 0;
            load_group <= 0;
            result_toggle_core <= 1'b0;
            result_sequence_core <= 0;
            result_status_core <= 0;
            result_logical_core <= 0;
            result_cycles_core <= 0;
            result_sweeps_core <= 0;
            result_profile_core <= 0;
            syndrome_load_digest <= 0;
        end else begin
            request_toggle_meta <= request_toggle;
            request_toggle_sync <= request_toggle_meta;
            case (control_state)
                C_IDLE: if (request_toggle_sync != request_toggle_seen) begin
                    request_toggle_seen <= request_toggle_sync;
                    request_sequence_core <= request_sequence_latched;
                    load_time <= 0;
                    load_group <= 0;
                    syndrome_load_digest <= 0;
                    control_state <= C_CLEAR;
                end
                C_CLEAR: control_state <= C_LOAD;
                C_LOAD: begin
                    // Debug-only ingress checksum, exposed by PROBE after
                    // the load to distinguish CDC/nibble faults from the
                    // decoder datapath.
                    syndrome_load_digest <= syndrome_load_digest ^
                        (({19'd0, load_time, load_group, syndrome_write_bits} *
                          32'h9E3779B1) + 32'h7F4A7C15);
                    if (load_group == 17) begin
                        load_group <= 0;
                        if (load_time == 12)
                            control_state <= C_START;
                        else
                            load_time <= load_time + 1'b1;
                    end else
                        load_group <= load_group + 1'b1;
                end
                C_START: begin
                    if (start_ready || decoder_error)
                        control_state <= C_WAIT;
                end
                // Decoder structural error has no done pulse (controller
                // enters S_ERROR). Return it instead of leaving UART request
                // latched forever.
                C_WAIT: if (done_valid || decoder_error) begin
                    result_sequence_core <= request_sequence_core;
                    result_status_core <= {
                        4'd0, BASIS_ID[0], decoder_error, deferred, success
                    };
                    result_logical_core <= logical_class;
                    result_cycles_core <= cycle_count;
                    result_sweeps_core <= sweeps_used;
                    result_profile_core <= profile_used;
                    result_toggle_core <= !result_toggle_core;
                    control_state <= C_IDLE;
                end
                default: control_state <= C_IDLE;
            endcase
        end
    end

    paper_gross144_s1w_four_lane_controller #(
        .BASIS_ID(BASIS_ID),
        .META_FILE("images/meta.memb"),
        .TEMPLATE_FILE("images/template_rows.memb"),
        .HASH_TIME_FILE("images/hash_time_bases.memb"),
        .ORBIT_CONFIG_FILE("images/orbit_config.memb"),
        .LOGICAL_QUADS_FILE("images/logical_quads.memb"),
        .LOGICAL_QUADS_BANK0_FILE("images/logical_quad_bank0.memb"),
        .LOGICAL_QUADS_BANK1_FILE("images/logical_quad_bank1.memb"),
        .LOGICAL_QUADS_BANK2_FILE("images/logical_quad_bank2.memb"),
        .LOGICAL_QUADS_BANK3_FILE("images/logical_quad_bank3.memb"),
        .TEMPLATE_SLOT_IMAGE_MODE(1),
        .HASH_WIDTH(20),
        .TEMPLATE_SLOT0_FILE("images/template_slot0.memb"),
        .TEMPLATE_SLOT1_FILE("images/template_slot1.memb"),
        .TEMPLATE_SLOT2_FILE("images/template_slot2.memb"),
        .TEMPLATE_SLOT3_FILE("images/template_slot3.memb"),
        // Inline emit parity is a diagnostic fast reject, not a final
        // syndrome proof: it sees edge-update signs, while acceptance needs
        // final variable signs.  Force streamed exact replay before logical
        // projection so hash collisions cannot become false accepts.
        .FORCE_EXACT_REPLAY(1),
        .INLINE_EXACT_CHECK(1),
        .EXACT_VERIFY_INTERVAL(1),
        .HASH_ONLY_ACCEPT(0),
        .PREFETCH_RECORDS(0),
        .ENABLE_MEMORY_GUARD(0),
        .ENABLE_DEBUG(0),
        .RELAY_MODE(0),
        .MAX_PROFILE(0),
        .FAST_MAX_SWEEPS(10),
        .FAST_HANDOFF(1),
        .READ_RESPONSE_STAGES(1),
        .LOGICAL_READ_RESPONSE_STAGES(1),
        .RISK_DEFER_FINAL_SWEEP(1),
        .MESSAGE_MAGNITUDE_BITS(5)
    ) u_decoder (
        .clk(clk51), .rst(core_rst), .shot_clear(shot_clear),
        .syndrome_write_valid(syndrome_write_valid),
        .syndrome_write_time(load_time), .syndrome_write_group(load_group),
        .syndrome_write_bits(syndrome_write_bits),
        .start_valid(start_valid), .start_ready(start_ready),
        .done_valid(done_valid), .done_ready(done_ready),
        .success(success), .deferred(deferred), .logical_class(logical_class),
        .cycle_count(cycle_count), .sweeps_used(sweeps_used),
        .profile_used(profile_used), .error(decoder_error), .busy(decoder_busy),
        .debug_state(decoder_state_debug), .debug_guards(decoder_guards_debug),
        .debug_map_detail(decoder_map_detail_debug),
        .debug_fault_conflict_banks(decoder_fault_conflict_debug),
        .debug_fault_addr0(decoder_fault_addr0_debug),
        .debug_fault_addr1(decoder_fault_addr1_debug),
        .debug_fault_time(decoder_fault_time_debug),
        .debug_fault_group(decoder_fault_group_debug),
        .debug_fault_beat(decoder_fault_beat_debug),
        .debug_invalid_state(decoder_invalid_state_debug),
        .debug_fault_descriptor0(decoder_fault_descriptor0_debug),
        .debug_fault_descriptor1(decoder_fault_descriptor1_debug),
        .debug_fault_banks(decoder_fault_banks_debug),
        .debug_gather_digest(decoder_gather_digest_debug),
        .debug_early_gathers(decoder_early_gathers_debug),
        .debug_early_raw0(decoder_early_raw0_debug),
        .debug_early_raw1(decoder_early_raw1_debug),
        .debug_early_response_banks(decoder_early_response_banks_debug),
        .debug_early_response_port1(decoder_early_response_port1_debug),
        .debug_early_map_banks(decoder_early_map_banks_debug),
        .debug_early_map_coordinates(decoder_early_map_coordinates_debug),
        .debug_gather_checkpoint0(decoder_gather_checkpoint0_debug),
        .debug_gather_checkpoint1(decoder_gather_checkpoint1_debug),
        .debug_verify_digest(decoder_verify_digest_debug),
        .debug_verify_parity(decoder_verify_parity_debug),
        .debug_verify_syndrome(decoder_verify_syndrome_debug),
        .debug_verify_address(decoder_verify_address_debug),
        .debug_gather_count(decoder_gather_count_debug)
    );

    assign led = decoder_error || frame_error ? parser_error_count[22] :
                 result_toggle_core;
endmodule

// Production X image: timing-clean 40.5 MHz four-lane S1W with bounded fast handoff.
// The `_51` top name is retained for historical Gowin/project compatibility.
module tang_nano_20k_paper_s1w_four_lane_uart_fast_51_top (
    input logic clk27, input logic rst_n, input logic uart_rx_pin,
    output logic uart_tx_pin, output logic led
);
    tang_nano_20k_paper_s1w_four_lane_uart_top #(
        .BASIS_ID(0)
    ) u_top (.*);
endmodule

// Production Z image: same timing-clean 40.5 MHz datapath with the Z-basis logical image.
// The `_51` top name is retained for historical Gowin/project compatibility.
module tang_nano_20k_paper_s1w_four_lane_uart_fast_z_51_top (
    input logic clk27, input logic rst_n, input logic uart_rx_pin,
    output logic uart_tx_pin, output logic led
);
    tang_nano_20k_paper_s1w_four_lane_uart_top #(
        .BASIS_ID(1)
    ) u_top (.*);
endmodule
