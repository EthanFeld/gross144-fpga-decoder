// Bank/address generator for a compact Gross144 circuit-component posterior
// store.  Each variable orbit owns 72 Z12xZ6 coordinates.  A compiler-frozen
// 2-bit orbit colour selects one of four banks as (colour + x) mod 4; every
// bank then stores exactly 18 coordinates per orbit.
//
// The component template stores (orbit, anchor_coordinate).  The surrounding
// check controller supplies the translated check coordinate and its ROM-held
// orbit colour.  This avoids a 9k-entry variable-address table and maps the
// largest Z image (122 * 18 = 2196 entries/bank) into four native 4k DPBs.
module gross144_component_banked_address #(
    parameter integer ORBIT_COUNT = 122,
    parameter integer ORBIT_WIDTH = 7,
    parameter integer BANK_ADDRESS_WIDTH = 12
) (
    input  logic [ORBIT_WIDTH-1:0] orbit_id,
    input  logic [1:0] orbit_bank_color,
    input  logic [6:0] anchor_coordinate,
    input  logic [6:0] check_coordinate,
    output logic [1:0] bank,
    output logic [BANK_ADDRESS_WIDTH-1:0] bank_address,
    output logic invalid
);
    logic [4:0] anchor_x, check_x;
    logic [5:0] translated_x;
    logic [3:0] anchor_y, check_y;
    logic [4:0] translated_y;
    logic [2:0] residue;
    logic [5:0] x_delta;
    logic [3:0] x_group;
    logic [2:0] bank_sum;
    logic [13:0] orbit_times_18;
    logic [11:0] x_group_times_6;
    logic [12:0] address_sum;

    // Coordinate radix is Z12xZ6.  Use explicit small-range decode instead
    // of reciprocal multiply logic: this is shallower on Gowin LUTs and
    // avoids synthesis-dependent width treatment in the bank-address cone.
    // For 0 <= value < 72, floor(value / 6) is exactly
    // (value * 43) >> 8.  The old twelve-way comparator ladder was functionally
    // correct but formed the routed critical path in every live bank mapper.
    // Constant multiply-by-43 maps to a short shift/add cone on GW2AR.
    function automatic [4:0] coordinate_x(input logic [6:0] value);
        logic [13:0] scaled;
        begin
            scaled = value * 7'd43;
            coordinate_x = scaled >> 8;
        end
    endfunction

    always_comb begin
        anchor_x = coordinate_x(anchor_coordinate);
        anchor_y = anchor_coordinate -
                   ((anchor_x << 2) + (anchor_x << 1));
        check_x = coordinate_x(check_coordinate);
        check_y = check_coordinate -
                  ((check_x << 2) + (check_x << 1));
        translated_x = {1'b0, anchor_x} + {1'b0, check_x};
        translated_y = {1'b0, anchor_y} + {1'b0, check_y};
        if (translated_x >= 5'd12)
            translated_x = translated_x - 5'd12;
        if (translated_y >= 4'd6)
            translated_y = translated_y - 4'd6;

        bank_sum = {1'b0, orbit_bank_color} + {1'b0, translated_x[1:0]};
        bank = bank_sum[1:0];
        // Modulo four subtraction recovers the x residue belonging to this
        // physical bank. The remaining quotient is always 0, 1, or 2.
        residue = ({1'b0, bank} + 3'd4 - {1'b0, orbit_bank_color}) & 3'd3;
        x_delta = translated_x - {3'd0, residue};
        x_group = x_delta >> 2;
        orbit_times_18 = ({7'd0, orbit_id} << 4) + ({7'd0, orbit_id} << 1);
        x_group_times_6 = ({10'd0, x_group} << 2) + ({10'd0, x_group} << 1);
        address_sum = {1'b0, orbit_times_18[11:0]} +
                      {1'b0, x_group_times_6} +
                      {{8{1'b0}}, translated_y};
        bank_address = address_sum[11:0];
        invalid = (orbit_id >= ORBIT_COUNT) ||
                  (anchor_coordinate >= 7'd72) || (check_coordinate >= 7'd72) ||
                  (x_group > 4'd2);
    end
endmodule
