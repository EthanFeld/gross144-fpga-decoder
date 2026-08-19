module tb_tang_nano_20k_paper_s1w_four_lane_uart_top;
    logic clk27 = 0;
    logic rst_n = 0;
    logic uart_rx_pin = 1;
    logic uart_tx_pin, led;
    logic response_byte_valid;
    logic [7:0] response_byte;
    logic [7:0] response_bytes [0:31];
    logic [3:0] syndrome_groups [0:233];
    string syndrome_file;
    integer byte_index, response_count;
    integer profile_enable, profile_active, profile_finished;
    integer state_counts [0:13];
    integer run_cycles, run_gather_issue, run_gather_response;
    integer run_emit_capture, run_emit_advance, run_emit_commit;
    integer run_emit_phase0, run_emit_phase1, run_emit_buffer;
    integer verify_issue, verify_response, verify_last;
    integer logical_issue, record_issue, record_response;
    integer debug_record_cycles;
    integer profile_i;

    always #5 clk27 = ~clk27;

    tang_nano_20k_paper_s1w_four_lane_uart_top dut (.*);

    // Simulation-only cycle ledger. Enabled with +PROFILE_CYCLES.
    always @(posedge dut.clk51) begin
        if (profile_enable && !profile_finished) begin
            if (dut.u_decoder.state != 14'h0001)
                profile_active = 1;
            if (profile_active) begin
                if ($test$plusargs("DEBUG_RECORD") &&
                    dut.u_decoder.state == 14'h0004 && debug_record_cycles < 24) begin
                    $display("RECORD TRACE t=%0t issue=%0d en=%b enb=%b valid=%b validb=%b resp=%0d/%0d addr=%0d/%0d",
                             $time, dut.u_decoder.record_issue_lane,
                             dut.u_decoder.record_read_en, dut.u_decoder.record_read_en_b,
                             dut.u_decoder.record_read_valid, dut.u_decoder.record_read_valid_b,
                             dut.u_decoder.record_response_lane,
                             dut.u_decoder.record_response_lane_b,
                             dut.u_decoder.record_read_addr,
                             dut.u_decoder.record_read_addr_b);
                    debug_record_cycles = debug_record_cycles + 1;
                end
                case (dut.u_decoder.state)
                    14'h0001: state_counts[0] = state_counts[0] + 1;
                    14'h0002: state_counts[1] = state_counts[1] + 1;
                    14'h0004: state_counts[2] = state_counts[2] + 1;
                    14'h0008: state_counts[3] = state_counts[3] + 1;
                    14'h0010: state_counts[4] = state_counts[4] + 1;
                    14'h0020: state_counts[5] = state_counts[5] + 1;
                    14'h0040: state_counts[6] = state_counts[6] + 1;
                    14'h0080: state_counts[7] = state_counts[7] + 1;
                    14'h0100: state_counts[8] = state_counts[8] + 1;
                    14'h0200: state_counts[9] = state_counts[9] + 1;
                    14'h0400: state_counts[10] = state_counts[10] + 1;
                    14'h0800: state_counts[11] = state_counts[11] + 1;
                    14'h1000: state_counts[12] = state_counts[12] + 1;
                    default: state_counts[13] = state_counts[13] + 1;
                endcase
                if (dut.u_decoder.state == 14'h0010) begin
                    run_cycles = run_cycles + 1;
                    if (dut.u_decoder.gather_issue_valid) run_gather_issue = run_gather_issue + 1;
                    if (dut.u_decoder.gather_response_valid) run_gather_response = run_gather_response + 1;
                    if (dut.u_decoder.emit_capture_valid) run_emit_capture = run_emit_capture + 1;
                    if (dut.u_decoder.emit_advance) run_emit_advance = run_emit_advance + 1;
                    if (dut.u_decoder.emit_commit) run_emit_commit = run_emit_commit + 1;
                    if (dut.u_decoder.emit_buffer_valid) run_emit_buffer = run_emit_buffer + 1;
                    if (dut.u_decoder.emit_buffer_valid && !dut.u_decoder.emit_phase)
                        run_emit_phase0 = run_emit_phase0 + 1;
                    if (dut.u_decoder.emit_buffer_valid && dut.u_decoder.emit_phase)
                        run_emit_phase1 = run_emit_phase1 + 1;
                end
                if (dut.u_decoder.verify_issue_valid) verify_issue = verify_issue + 1;
                if (dut.u_decoder.verify_response_valid) verify_response = verify_response + 1;
                if (dut.u_decoder.verify_response_last) verify_last = verify_last + 1;
                if (dut.u_decoder.logical_issue_valid) logical_issue = logical_issue + 1;
                if (dut.u_decoder.record_read_en) record_issue = record_issue + 1;
                if (dut.u_decoder.record_read_valid) record_response = record_response + 1;
                if (dut.u_decoder.state == 14'h0400)
                    profile_finished = 1;
            end
        end
    end

    // Decode the physical TX waveform, including start/stop timing.  This
    // catches top-level handshaking failures that an internal done check does
    // not observe.
    uart_rx #(.CLK_HZ(27_000_000), .BAUD(3_000_000)) response_monitor (
        .clk(clk27), .rst(!rst_n), .rx(uart_tx_pin),
        .data_valid(response_byte_valid), .data(response_byte)
    );

    always @(negedge clk27) begin
        if (response_byte_valid && response_count < 32) begin
            response_bytes[response_count] = response_byte;
            response_count = response_count + 1;
        end
    end

    function automatic [31:0] crc32_next(input [31:0] crc, input [7:0] data);
        integer bit_index;
        reg [31:0] work;
        begin
            work = crc ^ data;
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1)
                work = work[0] ? ((work >> 1) ^ 32'hEDB88320) : (work >> 1);
            crc32_next = work;
        end
    endfunction

    task automatic send_byte(input [7:0] value);
        integer bit_index;
        begin
            uart_rx_pin = 0;
            repeat (9) @(posedge clk27);
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
                uart_rx_pin = value[bit_index];
                repeat (9) @(posedge clk27);
            end
            uart_rx_pin = 1;
            repeat (9) @(posedge clk27);
        end
    endtask

    task automatic send_crc_byte(input [7:0] value, inout [31:0] crc);
        begin
            send_byte(value);
            crc = crc32_next(crc, value);
        end
    endtask

    task automatic send_decode_frame(input [15:0] frame_sequence);
        reg [31:0] frame_crc;
        reg [7:0] frame_payload_byte;
        integer frame_byte_index;
        begin
            send_byte(8'h4d);
            send_byte(8'h54);
            frame_crc = 32'hffffffff;
            send_crc_byte(8'h01, frame_crc);
            send_crc_byte(8'h31, frame_crc);
            send_crc_byte(frame_sequence[7:0], frame_crc);
            send_crc_byte(frame_sequence[15:8], frame_crc);
            send_crc_byte(8'd117, frame_crc);
            send_crc_byte(8'd0, frame_crc);
            for (frame_byte_index = 0; frame_byte_index < 117; frame_byte_index = frame_byte_index + 1) begin
                frame_payload_byte = {syndrome_groups[frame_byte_index*2+1],
                                       syndrome_groups[frame_byte_index*2]};
                send_crc_byte(frame_payload_byte, frame_crc);
            end
            frame_crc = ~frame_crc;
            send_byte(frame_crc[7:0]);
            send_byte(frame_crc[15:8]);
            send_byte(frame_crc[23:16]);
            send_byte(frame_crc[31:24]);
        end
    endtask

    initial begin : stimulus
        reg [31:0] crc;
        reg [31:0] response_crc;
        reg [7:0] payload_byte;
        integer timeout_cycles, response_index;
        integer expected_logical, expected_sweeps, expected_cycles, check_cycles;
        integer expected_profile, expected_basis, expected_status;
        profile_enable = $test$plusargs("PROFILE_CYCLES");
        profile_active = 0;
        profile_finished = 0;
        run_cycles = 0;
        run_gather_issue = 0;
        run_gather_response = 0;
        run_emit_capture = 0;
        run_emit_advance = 0;
        run_emit_commit = 0;
        run_emit_phase0 = 0;
        run_emit_phase1 = 0;
        run_emit_buffer = 0;
        verify_issue = 0;
        verify_response = 0;
        verify_last = 0;
        logical_issue = 0;
        record_issue = 0;
        record_response = 0;
        debug_record_cycles = 0;
        for (profile_i = 0; profile_i < 14; profile_i = profile_i + 1)
            state_counts[profile_i] = 0;
        for (byte_index = 0; byte_index < 234; byte_index = byte_index + 1)
            syndrome_groups[byte_index] = 4'b0;
        if (!$value$plusargs("SYNDROME=%s", syndrome_file) &&
            !$test$plusargs("ZERO_SYNDROME"))
            syndrome_file =
                "build/generated/paper_gross144_rtl_vectors/x/syndrome_groups_000000.memb";
        if (!$value$plusargs("EXPECTED_LOGICAL=%h", expected_logical))
            expected_logical = 12'haaa;
        if (!$value$plusargs("EXPECTED_SWEEPS=%d", expected_sweeps))
            expected_sweeps = 2;
        check_cycles = $value$plusargs("EXPECTED_CYCLES=%d", expected_cycles);
        if (!$value$plusargs("EXPECTED_PROFILE=%d", expected_profile))
            expected_profile = 0;
        if (!$value$plusargs("EXPECTED_BASIS=%d", expected_basis))
            expected_basis = 0;
        if (!$value$plusargs("EXPECTED_STATUS=%h", expected_status))
            expected_status = 1;
        if (!$test$plusargs("ZERO_SYNDROME"))
            $readmemb(syndrome_file, syndrome_groups);
        response_count = 0;
        repeat (8) @(posedge clk27);
        rst_n = 1;
        // Production UART reset is an independent 16-bit 27 MHz boot
        // counter (~2.43 ms); wait for it before sending the first frame.
        repeat (70000) @(posedge clk27);

        send_decode_frame(16'h1234);

        timeout_cycles = 0;
        // Worst-case portfolio traversal is below 2 M core clocks.  Leave
        // margin so rescue-profile vectors test the complete endpoint too.
        while (dut.result_toggle_core == 0 && timeout_cycles < 3000000) begin
            @(posedge clk27);
            timeout_cycles = timeout_cycles + 1;
        end
        if (dut.result_toggle_core == 0) begin
            if ($test$plusargs("DEBUG"))
                $display("UART TOP DEBUG rst=%b frame_valid=%b frame_ready=%b frame_err=%b cmd=%h len=%0d req_latched=%b req_toggle=%b state=%0d load_time=%0d load_group=%0d core_req_meta=%b core_req_sync=%b core_req_seen=%b start=%b ready=%b done=%b busy=%b dec_err=%b parser_err=%0d",
                         dut.uart_rst, dut.frame_valid, dut.frame_ready,
                         dut.frame_error, dut.frame_command, dut.frame_length,
                         dut.request_latched, dut.request_toggle,
                         dut.control_state, dut.load_time, dut.load_group,
                         dut.request_toggle_meta, dut.request_toggle_sync,
                         dut.request_toggle_seen, dut.start_valid,
                         dut.start_ready, dut.done_valid, dut.decoder_busy,
                         dut.decoder_error, dut.parser_error_count);
                $display("UART TOP RECORD DEBUG issue=%0d/%b addr=%0d/%0d valid=%b/%b response=%0d/%0d state=%h active0=%h active1=%h active2=%h active3=%h",
                         dut.u_decoder.record_issue_lane,
                         dut.u_decoder.record_read_en,
                         dut.u_decoder.record_read_addr,
                         dut.u_decoder.record_read_addr_b,
                         dut.u_decoder.record_read_valid,
                         dut.u_decoder.record_read_valid_b,
                         dut.u_decoder.record_response_lane,
                         dut.u_decoder.record_response_lane_b,
                         dut.u_decoder.state,
                         dut.u_decoder.active_record[0],
                         dut.u_decoder.active_record[1],
                         dut.u_decoder.active_record[2],
                         dut.u_decoder.active_record[3]);
            $fatal(1, "UART endpoint decode timeout");
        end
        if (dut.result_status_core[2:0] !== expected_status[2:0] ||
            dut.result_logical_core !== expected_logical[11:0] ||
            dut.result_sweeps_core !== expected_sweeps[15:0] ||
            (check_cycles && dut.result_cycles_core !== expected_cycles[31:0]) ||
            dut.result_profile_core !== expected_profile[2:0])
            $fatal(1, "UART endpoint mismatch status=%h logical=%h sweeps=%0d cycles=%0d",
                   dut.result_status_core, dut.result_logical_core,
                   dut.result_sweeps_core, dut.result_cycles_core);

        timeout_cycles = 0;
        while (response_count < 23 && timeout_cycles < 5000) begin
            @(posedge clk27);
            timeout_cycles = timeout_cycles + 1;
        end
        if (response_count != 23)
            $fatal(1, "UART response timeout/count=%0d", response_count);
        if (response_bytes[0] !== 8'h4d || response_bytes[1] !== 8'h54 ||
            response_bytes[2] !== 8'h01 || response_bytes[3] !== 8'hb1 ||
            response_bytes[4] !== 8'h34 || response_bytes[5] !== 8'h12 ||
            response_bytes[6] !== 8'd11 || response_bytes[7] !== 8'd0 ||
            response_bytes[8][2:0] !== expected_status[2:0] ||
            response_bytes[9][2:0] !== expected_profile[2:0] ||
            {response_bytes[11], response_bytes[10][3:0]} !== expected_logical[11:0] ||
            (check_cycles && {response_bytes[15], response_bytes[14], response_bytes[13],
             response_bytes[12]} !== expected_cycles[31:0]) ||
            {response_bytes[17], response_bytes[16]} !== expected_sweeps[15:0] ||
            response_bytes[18] !== expected_basis[7:0])
            $fatal(1, "UART response content mismatch");
        response_crc = 32'hffffffff;
        for (response_index = 2; response_index <= 18;
             response_index = response_index + 1)
            response_crc = crc32_next(response_crc, response_bytes[response_index]);
        response_crc = ~response_crc;
        if ({response_bytes[22], response_bytes[21], response_bytes[20],
             response_bytes[19]} !== response_crc)
            $fatal(1, "UART response CRC mismatch");

        if ($test$plusargs("ERROR_RECOVERY")) begin
            // Force an isolated structural-error state after valid shot A,
            // then exercise the same shot_clear pulse used by C_CLEAR.
            // This must leave the decoder ready for valid shot C.
            force dut.u_decoder.structural_error_pending = 1'b1;
            @(posedge dut.clk51);
            release dut.u_decoder.structural_error_pending;
            if (dut.decoder_error !== 1'b1)
                $fatal(1, "forced S_ERROR did not assert decoder error");
            force dut.u_decoder.shot_clear = 1'b1;
            @(posedge dut.clk51);
            #1;
            release dut.u_decoder.shot_clear;
            @(posedge dut.clk51);
            #1;
            if (dut.u_decoder.state !== 14'h0001 ||
                dut.start_ready !== 1'b1 || dut.decoder_error !== 1'b0)
                $fatal(1, "S_ERROR recovery failed state=%h ready=%b error=%b",
                       dut.u_decoder.state, dut.start_ready, dut.decoder_error);
            response_count = 0;
            send_decode_frame(16'h1235);
            timeout_cycles = 0;
            while (dut.result_toggle_core != 1'b0 && timeout_cycles < 3000000) begin
                @(posedge clk27);
                timeout_cycles = timeout_cycles + 1;
            end
            if (dut.result_toggle_core != 1'b0 ||
                dut.result_status_core[2:0] !== expected_status[2:0] ||
                dut.result_logical_core !== expected_logical[11:0])
                $fatal(1, "post-recovery shot failed status=%h logical=%h",
                       dut.result_status_core, dut.result_logical_core);
            $display("ERROR RECOVERY PASS: valid -> forced error -> shot_clear -> valid");
        end
        $display("PAPER GROSS144 FOUR-LANE UART TOP PASS cycles=%0d logical=%h profile=%0d sweeps=%0d",
                 dut.result_cycles_core, dut.result_logical_core,
                 dut.result_profile_core, dut.result_sweeps_core);
        if (profile_enable) begin
            $display("PROFILE states idle=%0d init=%0d record_load=%0d start=%0d run=%0d sweep_decide=%0d verify_setup=%0d verify_run=%0d logical_setup=%0d logical_run=%0d done=%0d group_setup=%0d remap=%0d other=%0d",
                     state_counts[0], state_counts[1], state_counts[2], state_counts[3],
                     state_counts[4], state_counts[5], state_counts[6], state_counts[7],
                     state_counts[8], state_counts[9], state_counts[10], state_counts[11],
                     state_counts[12], state_counts[13]);
            $display("PROFILE run cycles=%0d gather_issue=%0d gather_response=%0d emit_capture=%0d emit_advance=%0d emit_commit=%0d buffer=%0d phase0=%0d phase1=%0d",
                     run_cycles, run_gather_issue, run_gather_response,
                     run_emit_capture, run_emit_advance, run_emit_commit,
                     run_emit_buffer, run_emit_phase0, run_emit_phase1);
            $display("PROFILE verify issue=%0d response=%0d last=%0d logical_issue=%0d record_issue=%0d record_response=%0d",
                     verify_issue, verify_response, verify_last,
                     logical_issue, record_issue, record_response);
        end
        $finish;
    end
endmodule
