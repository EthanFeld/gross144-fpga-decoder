from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from mitten.paper_gross144_stage2 import (  # noqa: E402
    compile_paper_gross144_stage2_templates,
    load_paper_gross144_stage2_layout,
    run_streamed_s2,
    run_streamed_s2_relay_lite,
    run_streamed_s2_relay,
    run_streamed_s2_relay_bridge_retry_portfolio,
    run_streamed_s2_relay_with_bridge_retry,
    select_streamed_s2_relay_bridge_retry_candidate,
    StreamedS2RelayConfig,
    streamed_s2_cycle_plan,
    streamed_s2_relay_cycle_plan,
    streamed_s2_relay_storage_plan,
    streamed_s2_storage_plan,
)


RELAY_ROOT = ROOT / "build" / "relay"


def _packed_syndrome(hex_bytes: str) -> np.ndarray:
    values = np.frombuffer(bytes.fromhex(hex_bytes), dtype=np.uint8)
    return np.unpackbits(values, bitorder="little")[:1_728].astype(np.uint8)


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_relay_lite_zero_syndrome_contract() -> None:
    image = compile_paper_gross144_stage2_templates(RELAY_ROOT, p=0.002, basis="X")
    result = run_streamed_s2_relay_lite(image, np.zeros(image.checks, dtype=np.uint8))
    assert result.accepted
    assert result.predicted_logicals == (0,) * 12
    assert result.iterations == 0


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_full_paper_s2_stream_image_is_exact_and_speed_bounded() -> None:
    layout = load_paper_gross144_stage2_layout(RELAY_ROOT, p=0.002, basis="X")
    assert (layout.graph.num_variables, len(layout.graph.checks), layout.graph.edge_count()) == \
        (67_824, 1_728, 391_464)
    assert (layout.max_check_degree, layout.max_fault_span) == (242, 3)

    image = compile_paper_gross144_stage2_templates(RELAY_ROOT, p=0.002, basis="X")
    assert (image.group_order, image.time_slices, image.variable_orbits) == (72, 24, 942)
    assert (image.max_template_degree, image.max_live_variables) == (242, 9_432)
    assert len(image.logical_mask_dictionary) == 82
    assert len(image.logical_label_templates) == 28
    assert len(image.orbit_logical_label_template_ids) == 942

    storage = streamed_s2_storage_plan(image)
    assert storage["edge_message_bits"] == 0
    assert storage["correction_bitmap_bits"] == 67_824
    assert storage["onchip_bram_blocks"] == 16
    assert storage["immutable_logical_projection_bram_blocks_if_resident"] == 2
    assert storage["onchip_bram_blocks"] + storage[
        "immutable_logical_projection_bram_blocks_if_resident"
    ] == 18
    assert storage["topology_compression_ratio"] > 58.0

    cycles = streamed_s2_cycle_plan(image)
    assert cycles["syndrome_rounds_per_second"] > 1_000.0
    assert cycles["syndrome_validation_cycles"] > 0
    result = run_streamed_s2(image, [0] * 1_728)
    assert result.accepted
    assert result.correction is None
    assert result.max_live_variables == 0

    # Nonzero input exercises quotient expansion, four-slice retirement, and
    # strict residual replay without ever materializing a full correction.
    nonzero = run_streamed_s2(image, [1] + [0] * 1_727)
    assert nonzero.checks_processed == 1_728
    assert nonzero.correction is None
    assert nonzero.max_live_variables <= image.max_live_variables


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_z_s2_uses_same_compressed_runtime_contract() -> None:
    image = compile_paper_gross144_stage2_templates(RELAY_ROOT, p=0.002, basis="Z")
    storage = streamed_s2_storage_plan(image)
    assert (image.variables, image.variable_orbits, len(image.logical_label_templates)) == \
        (67_752, 941, 12)
    assert storage["onchip_bram_blocks"] + storage[
        "immutable_logical_projection_bram_blocks_if_resident"
    ] == 17
    result = run_streamed_s2(image, [1] + [0] * 1_727)
    assert result.checks_processed == 1_728
    assert result.correction is None
    assert result.max_live_variables <= image.max_live_variables
    relay_zero = run_streamed_s2_relay(image, [0] * 1_728)
    assert relay_zero.accepted
    assert relay_zero.iterations == 0
    # Deterministic raw Z row 843 closes at iteration 13 through the exact
    # logical-neutral degree-1/2 forest, preserving its logical class.
    relay_completion = run_streamed_s2_relay(image, _packed_syndrome(
        "0000000000000000000120000000000000000000000000000100400120000000000400009000010000000000c00080060540940400018061624124090000044008201028000204010000000040090010020024080008020000004008000000000020000008004808081030040000000000002048101002100000003010200082040000040102102c00000000009006100000001000302028000b0880000000400800440000000060b080a2000020000030410000410c000028001810e000000c00342a401000000000402000200080000000001000102000"
    ))
    assert relay_completion.accepted
    assert relay_completion.iterations == 13
    assert relay_completion.neutral_completion_flips == 57
    assert relay_completion.neutral_completion_attempts == 13
    assert relay_completion.predicted_logicals == (0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0)


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_fixed_s2r_relay_tail_resolves_regression_traps() -> None:
    image = compile_paper_gross144_stage2_templates(RELAY_ROOT, p=0.002, basis="X")
    # Exact raw full-detector rows 99 and 487 from the deterministic p=.002
    # X1000 comparison fixture.  S1C/D/E and causal S2C defer both rows.
    traps = (
        ("000000000002000000000008000300020000008020010002001000004840ca00200200000004080e00020090010000000000000201800000000000001020000404000000000010020000000000001824000004080400000000200000000000000000000000000440000024080200000200014004410100010180708201842000008000004004090000000200809010002010e6d44010000000000804000000002000180038000000610000001202000000000000104800484008c30400001000000400000000002400000000000000480000000000000020",
         (1, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1), 14),
        ("00000000010000000000000008080020410000004000010420000304080240000030410010000090210023000000000001922008240812001000280004014300020000102041000080204004800080000002020000200040882900000000000000508000000000000000000000004000c00000100010040000000000218210041005004012a400c0860d2040000c000c34000441018009a4810002100019a2080000000082008600082000984c180000800018203104012000288000000000000000001808100000002100c4000409000010040884000000",
         (0, 1, 0, 1, 0, 1, 1, 0, 1, 1, 1, 0), 8),
    )
    for packed, expected_logicals, expected_iterations in traps:
        result = run_streamed_s2_relay(image, _packed_syndrome(packed))
        assert result.accepted
        assert result.predicted_logicals == expected_logicals
        assert result.iterations == expected_iterations
        assert result.message_magnitude_peak <= 63

    storage = streamed_s2_relay_storage_plan(image)
    assert storage["dynamic_sdram_bytes"] == 529_047
    assert storage["sdram_halfword_layout_bytes"] == 1_054_224
    assert storage["packed_message_halfwords"] == 195_732
    assert storage["packed_message_sdram_halfword_layout_bytes"] == 662_760
    assert storage["message_halfword_base"] == 0
    assert storage["posterior_a_halfword_base"] == 391_464
    assert storage["posterior_b_halfword_base"] == 459_288
    assert storage["total_sdram_halfwords"] == 527_112
    assert storage["total_sdram_32bit_words"] == 263_556
    assert storage["s2c_bram_overlay_blocks_reused"] == 18
    assert storage["neutral_completion_tree_bram_blocks_reusing_s2c_live_state"] == 4
    cycles = streamed_s2_relay_cycle_plan(image)
    assert cycles["iterations"] == 20
    assert cycles["neutral_completion_attempts"] == 20
    assert cycles["packed_message_halfword_transactions_per_iteration"] == 1_114_308
    assert cycles["packed_message_ideal_32bit_word_transactions_per_iteration"] == 557_154
    assert cycles["milliseconds_per_window_lower_bound"] > 0.0


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_terminal_relay_bridge_crosses_detached_neutral_components() -> None:
    """A held-out X tail needs the new terminal-only zero-logical bridge."""

    image = compile_paper_gross144_stage2_templates(RELAY_ROOT, p=0.002, basis="X")
    layout = load_paper_gross144_stage2_layout(RELAY_ROOT, p=0.002, basis="X")
    value = (260728795 ^ 0x584D454D) & ((1 << 63) - 1)
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & ((1 << 63) - 1)
    detectors, observables = layout.circuit.compile_detector_sampler(
        seed=value ^ (value >> 27),
    ).sample(10_000, separate_observables=True)
    # Row 614 is an original terminal S2R defer.  The terminal bridge reaches
    # the boundary while retaining the Relay candidate's correct logical word.
    result = run_streamed_s2_relay(image, detectors[614])
    assert result.accepted
    assert result.predicted_logicals == tuple(int(value) for value in observables[614])
    assert result.reason == "logical-neutral bridged degree-1/2 completion"
    storage = streamed_s2_relay_storage_plan(image)
    assert storage["neutral_completion_bridge_components"] == 72
    assert storage["neutral_completion_bridge_rank"] == 66
    assert storage["neutral_completion_bridge_bram_blocks_reusing_s2c_live_state"] == 2


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_bridged_relay_retry_uses_independent_fixed_point_candidate() -> None:
    """Retry only runs after a bridge, then replaces its logical candidate."""

    image = compile_paper_gross144_stage2_templates(RELAY_ROOT, p=0.002, basis="X")
    layout = load_paper_gross144_stage2_layout(RELAY_ROOT, p=0.002, basis="X")
    value = (904155123 ^ 0x584D454D) & ((1 << 63) - 1)
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & ((1 << 63) - 1)
    detectors, observables = layout.circuit.compile_detector_sampler(
        seed=value ^ (value >> 27),
    ).sample(20_000, separate_observables=True)
    primary, retry = run_streamed_s2_relay_with_bridge_retry(
        image, detectors[4129],
        bridge_retry_config=StreamedS2RelayConfig(
            max_iterations=20, memory_weight_shift=1, message_magnitude_bits=8,
        ),
    )
    # Keep a directed primary check before selecting the independent image.
    assert primary.reason == "logical-neutral bridged degree-1/2 completion"
    assert retry is not None
    assert retry.predicted_logicals == tuple(int(value) for value in observables[4129])


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_second_bridged_relay_image_can_change_logical_coset() -> None:
    """Third fixed image runs only after two terminal bridge completions."""

    image = compile_paper_gross144_stage2_templates(RELAY_ROOT, p=0.002, basis="X")
    layout = load_paper_gross144_stage2_layout(RELAY_ROOT, p=0.002, basis="X")
    value = (174806211 ^ 0x584D454D) & ((1 << 63) - 1)
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & ((1 << 63) - 1)
    detectors, observables = layout.circuit.compile_detector_sampler(
        seed=value ^ (value >> 27),
    ).sample(20_000, separate_observables=True)
    results = run_streamed_s2_relay_bridge_retry_portfolio(
        image, detectors[10272], bridge_retry_configs=(
            StreamedS2RelayConfig(
                max_iterations=20, memory_weight_shift=1, message_magnitude_bits=8,
            ),
            StreamedS2RelayConfig(
                max_iterations=28, memory_weight_shift=2, message_magnitude_bits=7,
            ),
        ),
    )
    assert len(results) == 3
    assert all(result.reason == "logical-neutral bridged degree-1/2 completion"
               for result in results[:2])
    assert results[-1].predicted_logicals == tuple(int(value) for value in observables[10272])
    assert select_streamed_s2_relay_bridge_retry_candidate(results) is results[-1]


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_second_relay_image_is_a_scored_candidate_not_an_unconditional_overwrite() -> None:
    """A higher-cost third image must not displace a correct first retry."""

    image = compile_paper_gross144_stage2_templates(RELAY_ROOT, p=0.002, basis="X")
    layout = load_paper_gross144_stage2_layout(RELAY_ROOT, p=0.002, basis="X")
    value = (685330491 ^ 0x584D454D) & ((1 << 63) - 1)
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & ((1 << 63) - 1)
    detectors, observables = layout.circuit.compile_detector_sampler(
        seed=value ^ (value >> 27),
    ).sample(20_000, separate_observables=True)
    results = run_streamed_s2_relay_bridge_retry_portfolio(
        image, detectors[14808], bridge_retry_configs=(
            StreamedS2RelayConfig(
                max_iterations=20, memory_weight_shift=1, message_magnitude_bits=8,
            ),
            StreamedS2RelayConfig(
                max_iterations=28, memory_weight_shift=2, message_magnitude_bits=7,
            ),
        ),
    )
    assert len(results) == 3
    selected = select_streamed_s2_relay_bridge_retry_candidate(results)
    assert selected is results[1]
    assert selected.prior_cost < results[2].prior_cost
    assert selected.predicted_logicals == tuple(int(value) for value in observables[14808])
