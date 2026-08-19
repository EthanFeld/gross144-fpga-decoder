// F01 byte-stream frame parser/transmitter.
// Frame: "MT", version, command, sequence LE, payload length LE, payload, CRC32 LE.
module uart_framer #(
    parameter integer MAX_PAYLOAD = 256,
    parameter integer STORE_RX_PAYLOAD = 1
) (
    input  logic clk,
    input  logic rst,
    input  logic rx_valid,
    output logic rx_ready,
    input  logic [7:0] rx_data,
    output logic frame_valid,
    input  logic frame_ready,
    output logic [7:0] frame_command,
    output logic [15:0] frame_sequence,
    output logic [15:0] frame_length,
    output logic [MAX_PAYLOAD*8-1:0] frame_payload,
    output logic rx_payload_byte_valid,
    output logic [15:0] rx_payload_byte_index,
    output logic [7:0] rx_payload_byte_data,
    output logic frame_error,
    input  logic tx_start,
    input  logic [7:0] tx_command,
    input  logic [15:0] tx_sequence,
    input  logic [15:0] tx_length,
    input  logic [MAX_PAYLOAD*8-1:0] tx_payload,
    output logic tx_busy,
    output logic tx_valid,
    input  logic tx_ready,
    output logic [7:0] tx_data
);
    localparam logic [2:0] R_MAGIC0 = 3'd0;
    localparam logic [2:0] R_MAGIC1 = 3'd1;
    localparam logic [2:0] R_HEADER = 3'd2;
    localparam logic [2:0] R_PAYLOAD = 3'd3;
    localparam logic [2:0] R_CRC = 3'd4;
    logic [2:0] rx_state;
    logic [2:0] rx_header_index;
    logic [1:0] rx_crc_index;
    logic [15:0] rx_length_reg, rx_payload_index;
    logic [31:0] rx_crc_reg, rx_received_crc;

    logic [15:0] tx_phase;
    logic [7:0] tx_command_reg;
    logic [15:0] tx_sequence_reg, tx_length_reg;
    logic [MAX_PAYLOAD*8-1:0] tx_payload_reg;
    logic [31:0] tx_crc_reg;
    // CRC used to unroll all payload bytes in tx_start's input-to-register
    // path.  Serialize it before transmission; UART baud time makes this
    // setup latency invisible while removing the endpoint timing cone.
    logic tx_crc_active;
    logic [15:0] tx_crc_index;
    logic [31:0] tx_crc_work;
    logic [MAX_PAYLOAD*8-1:0] tx_crc_payload_shift;
    logic [7:0] tx_crc_byte;

    function automatic [31:0] crc32_next(input [31:0] crc, input [7:0] data);
        integer crc_loop;
        reg [31:0] work;
        begin
            work = crc ^ data;
            for (crc_loop = 0; crc_loop < 8; crc_loop = crc_loop + 1)
                work = work[0] ? ((work >> 1) ^ 32'hEDB88320) : (work >> 1);
            crc32_next = work;
        end
    endfunction

    always @* begin
        rx_ready = !frame_valid;
        tx_valid = tx_busy && !tx_crc_active;
        tx_data = 0;
        if (tx_valid) begin
            case (tx_phase)
                0: tx_data = 8'h4D;
                1: tx_data = 8'h54;
                2: tx_data = 8'h01;
                3: tx_data = tx_command_reg;
                4: tx_data = tx_sequence_reg[7:0];
                5: tx_data = tx_sequence_reg[15:8];
                6: tx_data = tx_length_reg[7:0];
                7: tx_data = tx_length_reg[15:8];
                default: begin
                    if (tx_phase < 8 + tx_length_reg)
                        tx_data = tx_payload_reg[(tx_phase-8)*8 +: 8];
                    else
                        tx_data = tx_crc_reg[(tx_phase-8-tx_length_reg)*8 +: 8];
                end
            endcase
        end
    end

    always @* begin
        case (tx_crc_index)
            0: tx_crc_byte = 8'd1;
            1: tx_crc_byte = tx_command_reg;
            2: tx_crc_byte = tx_sequence_reg[7:0];
            3: tx_crc_byte = tx_sequence_reg[15:8];
            4: tx_crc_byte = tx_length_reg[7:0];
            5: tx_crc_byte = tx_length_reg[15:8];
            default: tx_crc_byte = tx_crc_payload_shift[7:0];
        endcase
    end

    always @(posedge clk) begin
        frame_error <= 1'b0;
        rx_payload_byte_valid <= 1'b0;
        if (rst) begin
            rx_state <= R_MAGIC0;
            frame_valid <= 1'b0;
            frame_command <= 0;
            frame_sequence <= 0;
            frame_length <= 0;
            frame_payload <= 0;
            rx_payload_byte_index <= 0;
            rx_payload_byte_data <= 0;
            rx_header_index <= 0;
            rx_crc_index <= 0;
            rx_crc_reg <= 32'hFFFFFFFF;
            rx_received_crc <= 0;
            tx_busy <= 1'b0;
            tx_phase <= 0;
            tx_crc_reg <= 0;
            tx_crc_active <= 1'b0;
            tx_crc_index <= 0;
            tx_crc_work <= 0;
            tx_crc_payload_shift <= 0;
        end else begin
            if (frame_valid) begin
                if (frame_ready) begin
                    frame_valid <= 1'b0;
                    rx_state <= R_MAGIC0;
                end
            end else if (rx_valid && rx_ready) begin
                case (rx_state)
                    R_MAGIC0: if (rx_data == 8'h4D) rx_state <= R_MAGIC1;
                    R_MAGIC1: begin
                        if (rx_data == 8'h54) begin
                            rx_state <= R_HEADER;
                            rx_header_index <= 0;
                            rx_crc_reg <= 32'hFFFFFFFF;
                        end else if (rx_data == 8'h4D) begin
                            rx_state <= R_MAGIC1;
                        end else begin
                            rx_state <= R_MAGIC0;
                        end
                    end
                    R_HEADER: begin
                        rx_crc_reg <= crc32_next(rx_crc_reg, rx_data);
                        case (rx_header_index)
                            0: if (rx_data != 8'd1) begin rx_state <= R_MAGIC0; frame_error <= 1'b1; end
                            1: frame_command <= rx_data;
                            2: frame_sequence[7:0] <= rx_data;
                            3: frame_sequence[15:8] <= rx_data;
                            4: rx_length_reg[7:0] <= rx_data;
                            5: begin
                                if ({rx_data, rx_length_reg[7:0]} > MAX_PAYLOAD) begin
                                    rx_state <= R_MAGIC0; frame_error <= 1'b1;
                                end else begin
                                    rx_length_reg <= {rx_data, rx_length_reg[7:0]};
                                    frame_length <= {rx_data, rx_length_reg[7:0]};
                                    rx_payload_index <= 0;
                                    rx_crc_index <= 0;
                                    if ({rx_data, rx_length_reg[7:0]} == 0) rx_state <= R_CRC;
                                    else rx_state <= R_PAYLOAD;
                                end
                            end
                            default: ;
                        endcase
                        if (rx_state == R_HEADER && rx_header_index != 5)
                            rx_header_index <= rx_header_index + 1'b1;
                    end
                    R_PAYLOAD: begin
                        if (STORE_RX_PAYLOAD)
                            frame_payload[rx_payload_index*8 +: 8] <= rx_data;
                        rx_payload_byte_valid <= 1'b1;
                        rx_payload_byte_index <= rx_payload_index;
                        rx_payload_byte_data <= rx_data;
                        rx_crc_reg <= crc32_next(rx_crc_reg, rx_data);
                        if (rx_payload_index == rx_length_reg - 1) begin
                            rx_crc_index <= 0;
                            rx_state <= R_CRC;
                        end else begin
                            rx_payload_index <= rx_payload_index + 1'b1;
                        end
                    end
                    R_CRC: begin
                        rx_received_crc[rx_crc_index*8 +: 8] <= rx_data;
                        if (rx_crc_index == 3) begin
                            if (~rx_crc_reg == {rx_data, rx_received_crc[23:0]}) begin
                                frame_valid <= 1'b1;
                            end else begin
                                frame_error <= 1'b1;
                                rx_state <= R_MAGIC0;
                            end
                        end else begin
                            rx_crc_index <= rx_crc_index + 1'b1;
                        end
                    end
                    default: rx_state <= R_MAGIC0;
                endcase
            end

            if (tx_crc_active) begin
                tx_crc_work <= crc32_next(tx_crc_work, tx_crc_byte);
                if (tx_crc_index == 16'd5 + tx_length_reg) begin
                    tx_crc_reg <= ~crc32_next(tx_crc_work, tx_crc_byte);
                    tx_crc_active <= 1'b0;
                    tx_phase <= 0;
                end else begin
                    tx_crc_index <= tx_crc_index + 1'b1;
                    if (tx_crc_index >= 16'd6)
                        tx_crc_payload_shift <= tx_crc_payload_shift >> 8;
                end
            end else if (tx_busy) begin
                if (tx_valid && tx_ready) begin
                    if (tx_phase == 8 + tx_length_reg + 4 - 1)
                        tx_busy <= 1'b0;
                    else
                        tx_phase <= tx_phase + 1'b1;
                end
            end else if (tx_start) begin
                if (tx_length > MAX_PAYLOAD) begin
                    frame_error <= 1'b1;
                end else begin
                    tx_command_reg <= tx_command;
                    tx_sequence_reg <= tx_sequence;
                    tx_length_reg <= tx_length;
                    tx_payload_reg <= tx_payload;
                    tx_crc_work <= 32'hFFFFFFFF;
                    tx_crc_payload_shift <= tx_payload;
                    tx_crc_index <= 0;
                    tx_crc_active <= 1'b1;
                    tx_busy <= 1'b1;
                end
            end
        end
    end
endmodule
