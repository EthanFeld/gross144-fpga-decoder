// GW2AR-18C rPLL wrapper: 27 MHz * 12 / 8 = 40.5 MHz.
// VCO = 40.5 MHz * 16 = 648 MHz, inside the GW2AR-18C operating range.
//
// 40.5 MHz is the timing-clean production point. The historical module/file
// names retain `_51` for project/tool compatibility; board metadata is the
// clock source of truth.
module gross144_clock_51 (
    input  logic clk27,
    output logic clk51,
    output logic locked
);
`ifdef GROSS144_SIM
    assign clk51 = clk27;
    assign locked = 1'b1;
`else
    wire gnd = 1'b0;
    wire vcc = 1'b1;
    wire clkoutp_unused, clkoutd_unused, clkoutd3_unused;

    rPLL rpll_inst (
        .CLKOUT(clk51), .CLKOUTP(clkoutp_unused),
        .CLKOUTD(clkoutd_unused), .CLKOUTD3(clkoutd3_unused),
        .LOCK(locked), .CLKIN(clk27), .CLKFB(gnd),
        .FBDSEL({6{gnd}}), .IDSEL({6{gnd}}), .ODSEL({6{gnd}}),
        .DUTYDA({4{gnd}}), .PSDA({4{gnd}}), .FDLY({4{vcc}}),
        .RESET(gnd), .RESET_P(gnd)
    );

    defparam rpll_inst.FCLKIN = "27";
    defparam rpll_inst.DYN_IDIV_SEL = "false";
    defparam rpll_inst.IDIV_SEL = 7;
    defparam rpll_inst.DYN_FBDIV_SEL = "false";
    defparam rpll_inst.FBDIV_SEL = 11;
    defparam rpll_inst.DYN_ODIV_SEL = "false";
    defparam rpll_inst.ODIV_SEL = 16;
    defparam rpll_inst.PSDA_SEL = "0000";
    defparam rpll_inst.DYN_DA_EN = "false";
    defparam rpll_inst.DUTYDA_SEL = "1000";
    defparam rpll_inst.CLKOUT_FT_DIR = 1'b1;
    defparam rpll_inst.CLKOUTP_FT_DIR = 1'b1;
    defparam rpll_inst.CLKOUT_DLY_STEP = 0;
    defparam rpll_inst.CLKOUTP_DLY_STEP = 0;
    defparam rpll_inst.CLKFB_SEL = "internal";
    defparam rpll_inst.CLKOUT_BYPASS = "false";
    defparam rpll_inst.CLKOUTP_BYPASS = "false";
    defparam rpll_inst.CLKOUTD_BYPASS = "false";
    defparam rpll_inst.DYN_SDIV_SEL = 2;
    defparam rpll_inst.CLKOUTD_SRC = "CLKOUT";
    defparam rpll_inst.CLKOUTD3_SRC = "CLKOUT";
    defparam rpll_inst.DEVICE = "GW2AR-18C";
`endif
endmodule
