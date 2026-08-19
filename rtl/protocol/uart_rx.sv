// F02 asynchronous 8N1 UART receiver. Conservative integer baud divider.
module uart_rx #(
    parameter integer CLK_HZ = 27_000_000,
    parameter integer BAUD = 115_200
) (
    input  logic clk,
    input  logic rst,
    input  logic rx,
    output logic data_valid,
    output logic [7:0] data
);
    localparam integer BAUD_DIV = (CLK_HZ / BAUD < 2) ? 2 : CLK_HZ / BAUD;
    localparam integer COUNT_WIDTH = (BAUD_DIV < 2) ? 1 : $clog2(BAUD_DIV);
    localparam logic [1:0] S_IDLE = 2'd0, S_START = 2'd1, S_DATA = 2'd2, S_STOP = 2'd3;
    logic [1:0] state;
    logic [COUNT_WIDTH-1:0] counter;
    logic [2:0] bit_index;
    // The FTDI UART pin is asynchronous to the FPGA clock.  Synchronize it
    // before start/data sampling so a rare metastable edge cannot corrupt a
    // long framed image transfer without an endpoint-visible error.
    (* ASYNC_REG = "TRUE" *) logic rx_meta, rx_sync;

    always_ff @(posedge clk) begin
        if (rst) begin
            rx_meta <= 1'b1;
            rx_sync <= 1'b1;
        end else begin
            rx_meta <= rx;
            rx_sync <= rx_meta;
        end
    end

    always_ff @(posedge clk) begin
        data_valid <= 1'b0;
        if (rst) begin
            state <= S_IDLE;
            counter <= '0;
            bit_index <= 0;
            data <= 0;
        end else begin
            case (state)
                S_IDLE: if (!rx_sync) begin
                    counter <= BAUD_DIV / 2;
                    state <= S_START;
                end
                S_START: if (counter == 0) begin
                    if (!rx_sync) begin
                        counter <= BAUD_DIV - 1;
                        bit_index <= 0;
                        state <= S_DATA;
                    end else state <= S_IDLE;
                end else counter <= counter - 1'b1;
                S_DATA: if (counter == 0) begin
                    data[bit_index] <= rx_sync;
                    counter <= BAUD_DIV - 1;
                    if (bit_index == 7) state <= S_STOP;
                    else bit_index <= bit_index + 1'b1;
                end else counter <= counter - 1'b1;
                S_STOP: if (counter == 0) begin
                    if (rx_sync) data_valid <= 1'b1;
                    state <= S_IDLE;
                end else counter <= counter - 1'b1;
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
