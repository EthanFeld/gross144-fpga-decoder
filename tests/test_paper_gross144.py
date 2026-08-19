from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from mitten.paper_gross144 import (
    PaperGross144Stage1Config,
    PaperGross144Stage1FpgaAdapter,
    full_paper_dem_profile,
    load_paper_gross144_stage1_layout,
    rtl_cycle_model,
)
from mitten.paper_gross144_component_templates import (  # noqa: E402
    compile_paper_stage1_component_templates,
    paper_stage1_cycle_plan,
    paper_stage1_storage_plan,
)


RELAY_ROOT = ROOT / "build" / "relay"


def test_optimized_wide_s1_defaults_keep_the_bounded_schedule_contract() -> None:
    config = PaperGross144Stage1Config(physical_error_rate=0.002)
    assert config.minsum_iterations == 10
    assert config.check_schedule == "pair_alternating"
    assert [
        (profile.max_iterations, profile.check_schedule, profile.schedule_stride)
        for profile in config.wide_rescue_profiles
    ] == [
        (30, "alternating", 1),
        (20, "alternating_reverse", 1),
        (24, "cyclic_alternating", 72),
    ]
    assert config.minsum_iterations + sum(
        profile.max_iterations for profile in config.wide_rescue_profiles
    ) == 84
    config.validate()


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_paper_stage1_fixture_is_frozen_and_fits_streamed_degree_contract() -> None:
    layout = load_paper_gross144_stage1_layout(RELAY_ROOT, p=0.001, basis="X")
    assert layout.graph.num_variables == 8784
    assert len(layout.graph.checks) == 936
    assert layout.graph.edge_count() == 30672
    assert layout.max_check_degree == 35
    assert len(layout.automorphisms.trials) == 4
    assert all(layout.graph.preserves_graph(
        trial.variable_permutation, trial.check_permutation,
    ) for trial in layout.automorphisms.trials)
    cycles = rtl_cycle_model(layout, sweeps=8, banks=4, clock_hz=45_000_000.0)
    assert cycles["cycles_per_window"] == 260944
    assert cycles["cycle_exact"]
    assert cycles["model_kind"] == "rtl_audited_full_s1w_controller"


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_paper_full_dem_profile_exposes_stage2_not_stage1_limits() -> None:
    profile = full_paper_dem_profile(RELAY_ROOT, p=0.001, basis="X")
    assert profile == {
        "variables": 67824,
        "checks": 1728,
        "edges": 391464,
        "max_check_degree": 242,
    }


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_paper_adapter_never_accepts_hidden_truth_input() -> None:
    adapter = PaperGross144Stage1FpgaAdapter(
        RELAY_ROOT, config=PaperGross144Stage1Config(physical_error_rate=0.001),
    )
    result = adapter.decode([0] * 936, basis="X")
    assert result.accepted
    assert result.predicted_logicals == (0,) * 12


@pytest.mark.skipif(not RELAY_ROOT.exists(), reason="Relay paper fixture checkout is optional")
def test_paper_s1_quotient_image_reconstructs_exact_graph_without_bandwidth_loss() -> None:
    for basis in ("X", "Z"):
        image = compile_paper_stage1_component_templates(RELAY_ROOT, p=0.001, basis=basis)
        assert (image.variables, image.checks, image.edges) == (8784, 936, 30672)
        assert (image.group_order, image.variable_orbits) == (72, 122)
        assert (len(image.detector_time_templates), image.max_template_degree) == (13, 35)
        # ceil(35/4) is a hard lower bound. The quotient schedule reaches it.
        assert image.max_banked_cycles == 9
        assert image.two_lane_pair_coordinate_delta == 36
        for row, banked_row in zip(image.detector_time_templates,
                                   image.banked_detector_time_templates):
            assert sorted((orbit, anchor) for cycle in banked_row
                          for _bank, _edge, orbit, anchor in cycle) == sorted(row)
            assert all(len({bank for bank, _edge, _orbit, _anchor in cycle}) == len(cycle)
                       for cycle in banked_row)
        plan = paper_stage1_storage_plan(image)
        assert plan == {
            "address_bits": 14,
            "explicit_topology_bits": 429408,
            "template_topology_bits": 9694,
            "topology_compression_ratio": 429408 / 9694,
            "posterior_bits": 96624,
            "warmup_message_bits": 153360,
            "compressed_check_record_bits": 43776,
            "posterior_bram_blocks": 8,
            "warmup_message_bram_blocks": 12,
            "compressed_record_bram_blocks": 3,
            "template_bram_blocks": 1,
            "total_decoder_bram_blocks": 24,
        }
        cycles = paper_stage1_cycle_plan(image)
        assert cycles == {
            "clock_hz": 45_000_000.0,
            "warmup_iterations": 2,
            "minsum_iterations": 6,
            "warmup_cycles_per_pass": 79056,
            "minsum_cycles_per_pass": 17712,
            "cycles_per_window": 264384,
            "microseconds_per_syndrome_round": 489.59999999999997,
            "syndrome_rounds_per_second": 2042.483660130719,
        }
