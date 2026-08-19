// Constant-time paired S1W RAM mapper.
//
// A compiler beat is already conflict-free within each check. Therefore port
// ownership need not be arbitrated: all lane-0 requests use DPB port 0, all
// lane-1 requests use port 1. This is safe for every c/c+36 pair and removes
// the generic eight-request allocator from the decoder clock-critical emit
// path. Runtime guards retain the proof assumptions as hardware assertions.
module paper_gross144_s1w_dual_port_lane_crossbar #(
    parameter integer BANKS = 4,
    parameter integer LANES = 4,
    parameter integer ADDR_WIDTH = 12,
    parameter integer DATA_WIDTH = 11
) (
    input  logic [LANES-1:0] lane0_valid,
    input  logic [LANES*2-1:0] lane0_banks,
    input  logic [LANES*ADDR_WIDTH-1:0] lane0_addresses,
    input  logic signed [LANES*DATA_WIDTH-1:0] lane0_write_data,
    input  logic [LANES-1:0] lane1_valid,
    input  logic [LANES*2-1:0] lane1_banks,
    input  logic [LANES*ADDR_WIDTH-1:0] lane1_addresses,
    input  logic signed [LANES*DATA_WIDTH-1:0] lane1_write_data,
    output logic [BANKS-1:0] port0_valid,
    output logic [BANKS*ADDR_WIDTH-1:0] port0_address,
    output logic signed [BANKS*DATA_WIDTH-1:0] port0_write_data,
    output logic [BANKS-1:0] port1_valid,
    output logic [BANKS*ADDR_WIDTH-1:0] port1_address,
    output logic signed [BANKS*DATA_WIDTH-1:0] port1_write_data,
    output logic mapping_error,
    output logic [BANKS-1:0] mapping_conflict_banks
);
    // Fully static route equations.  The previous procedural case/loop was
    // legal RTL, but Gowin sometimes collapsed one or more packed-vector
    // assignments during synthesis.  Each source slot is now a constant
    // part-select in a generated equation, so every physical bank port gets
    // its own independent address/data mux.
    wire [LANES-1:0] port0_route [0:BANKS-1];
    wire [LANES-1:0] port1_route [0:BANKS-1];
    wire port0_multi [0:BANKS-1];
    wire port1_multi [0:BANKS-1];
    wire port_alias [0:BANKS-1];
    genvar route_bank;
    generate
        for (route_bank = 0; route_bank < BANKS; route_bank = route_bank + 1) begin : g_static_route
            localparam logic [1:0] BANK_ID = route_bank;
            assign port0_route[route_bank][0] = lane0_valid[0] && (lane0_banks[1:0] == BANK_ID);
            assign port0_route[route_bank][1] = lane0_valid[1] && (lane0_banks[3:2] == BANK_ID);
            assign port0_route[route_bank][2] = lane0_valid[2] && (lane0_banks[5:4] == BANK_ID);
            assign port0_route[route_bank][3] = lane0_valid[3] && (lane0_banks[7:6] == BANK_ID);
            assign port1_route[route_bank][0] = lane1_valid[0] && (lane1_banks[1:0] == BANK_ID);
            assign port1_route[route_bank][1] = lane1_valid[1] && (lane1_banks[3:2] == BANK_ID);
            assign port1_route[route_bank][2] = lane1_valid[2] && (lane1_banks[5:4] == BANK_ID);
            assign port1_route[route_bank][3] = lane1_valid[3] && (lane1_banks[7:6] == BANK_ID);

            assign port0_valid[route_bank] = |port0_route[route_bank];
            assign port1_valid[route_bank] = |port1_route[route_bank];
            assign port0_address[route_bank*ADDR_WIDTH +: ADDR_WIDTH] =
                (port0_route[route_bank][0] ? lane0_addresses[0 +: ADDR_WIDTH] : '0) |
                (port0_route[route_bank][1] ? lane0_addresses[ADDR_WIDTH +: ADDR_WIDTH] : '0) |
                (port0_route[route_bank][2] ? lane0_addresses[2*ADDR_WIDTH +: ADDR_WIDTH] : '0) |
                (port0_route[route_bank][3] ? lane0_addresses[3*ADDR_WIDTH +: ADDR_WIDTH] : '0);
            assign port1_address[route_bank*ADDR_WIDTH +: ADDR_WIDTH] =
                (port1_route[route_bank][0] ? lane1_addresses[0 +: ADDR_WIDTH] : '0) |
                (port1_route[route_bank][1] ? lane1_addresses[ADDR_WIDTH +: ADDR_WIDTH] : '0) |
                (port1_route[route_bank][2] ? lane1_addresses[2*ADDR_WIDTH +: ADDR_WIDTH] : '0) |
                (port1_route[route_bank][3] ? lane1_addresses[3*ADDR_WIDTH +: ADDR_WIDTH] : '0);
            assign port0_write_data[route_bank*DATA_WIDTH +: DATA_WIDTH] =
                (port0_route[route_bank][0] ? lane0_write_data[0 +: DATA_WIDTH] : '0) |
                (port0_route[route_bank][1] ? lane0_write_data[DATA_WIDTH +: DATA_WIDTH] : '0) |
                (port0_route[route_bank][2] ? lane0_write_data[2*DATA_WIDTH +: DATA_WIDTH] : '0) |
                (port0_route[route_bank][3] ? lane0_write_data[3*DATA_WIDTH +: DATA_WIDTH] : '0);
            assign port1_write_data[route_bank*DATA_WIDTH +: DATA_WIDTH] =
                (port1_route[route_bank][0] ? lane1_write_data[0 +: DATA_WIDTH] : '0) |
                (port1_route[route_bank][1] ? lane1_write_data[DATA_WIDTH +: DATA_WIDTH] : '0) |
                (port1_route[route_bank][2] ? lane1_write_data[2*DATA_WIDTH +: DATA_WIDTH] : '0) |
                (port1_route[route_bank][3] ? lane1_write_data[3*DATA_WIDTH +: DATA_WIDTH] : '0);

            assign port0_multi[route_bank] =
                (port0_route[route_bank][0] && port0_route[route_bank][1]) ||
                (port0_route[route_bank][0] && port0_route[route_bank][2]) ||
                (port0_route[route_bank][0] && port0_route[route_bank][3]) ||
                (port0_route[route_bank][1] && port0_route[route_bank][2]) ||
                (port0_route[route_bank][1] && port0_route[route_bank][3]) ||
                (port0_route[route_bank][2] && port0_route[route_bank][3]);
            assign port1_multi[route_bank] =
                (port1_route[route_bank][0] && port1_route[route_bank][1]) ||
                (port1_route[route_bank][0] && port1_route[route_bank][2]) ||
                (port1_route[route_bank][0] && port1_route[route_bank][3]) ||
                (port1_route[route_bank][1] && port1_route[route_bank][2]) ||
                (port1_route[route_bank][1] && port1_route[route_bank][3]) ||
                (port1_route[route_bank][2] && port1_route[route_bank][3]);
            assign port_alias[route_bank] =
                port0_valid[route_bank] && port1_valid[route_bank] &&
                (port0_address[route_bank*ADDR_WIDTH +: ADDR_WIDTH] ==
                 port1_address[route_bank*ADDR_WIDTH +: ADDR_WIDTH]);
            assign mapping_conflict_banks[route_bank] =
                port0_multi[route_bank] || port1_multi[route_bank] || port_alias[route_bank];
        end
    endgenerate
    assign mapping_error = |mapping_conflict_banks;
endmodule
