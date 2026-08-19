// F02 asynchronous 8N1 UART transmitter.
module uart_tx #(
    parameter integer CLK_HZ = 27_000_000,
    parameter integer BAUD = 115_200
) (
    input  logic clk,
    input  logic rst,
    input  logic start,
    input  logic [7:0] data,
    output logic busy,
    output logic tx
);
    localparam integer BAUD_DIV = (CLK_HZ / BAUD < 2) ? 2 : CLK_HZ / BAUD;
    localparam integer COUNT_WIDTH = (BAUD_DIV < 2) ? 1 : $clog2(BAUD_DIV);
    logic [COUNT_WIDTH-1:0] counter;
    logic [3:0] bit_index;
    logic [7:0] data_reg;

    always_comb begin
        if (!busy) tx = 1'b1;
        else if (bit_index == 0) tx = 1'b0;
        else if (bit_index <= 8) tx = data_reg[bit_index-1];
        else tx = 1'b1;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            counter <= 0;
            bit_index <= 0;
            data_reg <= 0;
        end else if (!busy) begin
            if (start) begin
                data_reg <= data;
                busy <= 1'b1;
                bit_index <= 0;
                counter <= BAUD_DIV - 1;
            end
        end else if (counter == 0) begin
            counter <= BAUD_DIV - 1;
            if (bit_index == 9) busy <= 1'b0;
            else bit_index <= bit_index + 1'b1;
        end else counter <= counter - 1'b1;
    end
endmodule
