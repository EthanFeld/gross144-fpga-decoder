create_clock -name clk27 -period 37.037 -waveform {0 18.5185} [get_ports {clk27}]
# The production endpoint uses mitten_clock_51's timing-clean 40.5 MHz rPLL
# output. UART remains on the 27 MHz oscillator; CDC paths are false.
create_clock -name clk_core -period 24.691358 -waveform {0 12.345679} [get_pins {*rpll_inst/CLKOUT}]
set_false_path -from [get_clocks {clk27}] -to [get_clocks {clk_core}]
set_false_path -from [get_clocks {clk_core}] -to [get_clocks {clk27}]
