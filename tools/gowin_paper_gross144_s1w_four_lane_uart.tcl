# Canonical production build: Tang Nano 20K, Gross144 S1W, four-lane UART.
# The basis is the only supported build choice; all timing/debug variants are
# intentionally outside this release flow.
set root [file normalize [file join [file dirname [info script]] ..]]
set basis X
if {[info exists ::env(MITTEN_BASIS)]} {
    set basis [string toupper $::env(MITTEN_BASIS)]
}
if {$basis ni {X Z}} {
    error "MITTEN_BASIS must be X or Z"
}
set basis_lower [string tolower $basis]
set project_name paper_gross144_s1w_four_lane_uart_production_${basis_lower}
set project_dir [file join $root build $project_name]
file mkdir $project_dir

create_project -name $project_name -dir $project_dir \
    -pn GW2AR-LV18QN88C8/I7 -device_version C -force

set image_dir [file join $project_dir $project_name images]
file mkdir $image_dir
foreach image {
    meta.memb template_rows.memb hash_time_bases.memb orbit_config.memb
    logical_quads.memb logical_quad_bank0.memb logical_quad_bank1.memb
    logical_quad_bank2.memb logical_quad_bank3.memb
    template_slot0.memb template_slot1.memb template_slot2.memb
    template_slot3.memb
} {
    file copy -force \
        [file join $root build generated paper_gross144_s1w_${basis_lower}_p002 $image] \
        [file join $image_dir $image]
}

foreach source {
    {rtl top tang_nano_20k_paper_s1w_four_lane_uart_top.sv}
    {rtl common mitten_clock_51.sv}
    {rtl protocol uart_rx.sv}
    {rtl protocol uart_tx.sv}
    {rtl protocol uart_framer.sv}
    {rtl memory uart_payload_cdc_ram.sv}
    {rtl decoder paper_gross144_s1w_four_lane_schedule.sv}
    {rtl decoder gross144_component_banked_address.sv}
    {rtl decoder paper_gross144_s1w_dual_port_lane_crossbar.sv}
    {rtl decoder component_check_four_bank_engine.sv}
    {rtl decoder paper_gross144_s1w_paired_check_engine.sv}
    {rtl decoder paper_gross144_residual_hash.sv}
    {rtl memory paper_gross144_s1w_template_rom.sv}
    {rtl memory paper_gross144_s1w_syndrome_group_ram.sv}
    {rtl memory paper_gross144_s1w_orbit_config_rom.sv}
    {rtl memory paper_gross144_s1w_logical_quad_rom.sv}
    {rtl memory paper_gross144_s1w_check_record_ram.sv}
    {rtl memory paper_gross144_s1w_check_record_dual_read_ram.sv}
    {rtl memory paper_gross144_s1w_posterior_bank_dual.sv}
    {rtl decoder paper_gross144_s1w_four_lane_controller.sv}
} {
    add_file [file join $root {*}$source]
}
add_file [file join $root constraints tang_nano_20k.cst]
add_file [file join $root constraints tang_nano_20k_paper_s1w_four_lane_uart_51.sdc]

if {$basis eq "X"} {
    set_option -top_module tang_nano_20k_paper_s1w_four_lane_uart_fast_51_top
} else {
    set_option -top_module tang_nano_20k_paper_s1w_four_lane_uart_fast_z_51_top
}
set_option -verilog_std sysv2017
set_option -global_freq 40.5
set_option -frequency 40.5
set_option -output_base_name $project_name
run all
puts "MITTEN_PRODUCTION_BUILD_${basis}_DONE"
exit
