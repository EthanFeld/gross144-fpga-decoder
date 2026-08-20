from __future__ import annotations

from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.fixed_point import fixed_check_update  # noqa: E402
from gross144_decoder.graph_model import Graph  # noqa: E402
from gross144_decoder.wide_minsum import (  # noqa: E402
    GROSS144_FOUR_LANE_PAIR_GROUPS,
    WideMinSumConfig,
    _iteration_checks,
    _wide_check_update,
    run_wide_layered_minsum_batch,
    run_wide_layered_minsum,
)


def test_four_bit_wide_update_reproduces_release_normalized_min_sum() -> None:
    values = (23, -7, 4, -19, 10)
    config = WideMinSumConfig(message_magnitude_bits=4, correction_shift=2)
    assert _wide_check_update(values, syndrome_bit=1, config=config) == \
        fixed_check_update(values, syndrome_bit=1)


def test_wide_messages_allow_fixed_layered_convergence_without_truth_input() -> None:
    graph = Graph.from_neighbors(3, ((0, 1), (1, 2)), syndrome=(1, 1))
    result = run_wide_layered_minsum(
        graph, (8, 8, 8), config=WideMinSumConfig(max_iterations=4),
    )
    assert result.success
    assert result.syndrome == (1, 1)
    assert result.work <= 8


def test_wide_config_rejects_unrepresentable_record_width() -> None:
    try:
        WideMinSumConfig(message_magnitude_bits=3).validate()
    except ValueError as exc:
        assert "width" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid width accepted")


def test_batched_decoder_is_scalar_bit_exact() -> None:
    graph = Graph.from_neighbors(4, ((0, 1, 2), (1, 2, 3), (0, 3)), syndrome=(1, 0, 1))
    syndromes = np.array(((1, 0, 1), (0, 1, 1)), dtype=np.uint8)
    config = WideMinSumConfig(max_iterations=6, message_magnitude_bits=5, correction_shift=3)
    batch = run_wide_layered_minsum_batch(graph, (10, 7, 9, 8), syndromes, config=config)
    for index, syndrome in enumerate(syndromes):
        scalar = run_wide_layered_minsum(graph, (10, 7, 9, 8), syndrome=syndrome, config=config)
        assert bool(batch.success[index]) == scalar.success
        assert int(batch.iterations[index]) == scalar.iterations
        assert tuple(int(value) for value in batch.correction[index]) == scalar.correction


def test_batched_decoder_is_scalar_bit_exact_when_messages_saturate() -> None:
    """Guard the RTL normalization-before-clipping arithmetic order."""

    graph = Graph.from_neighbors(
        5,
        ((0, 1, 2), (1, 3, 4), (0, 2, 4)),
        syndrome=(1, 1, 0),
    )
    syndromes = np.array(((1, 1, 0), (0, 1, 1)), dtype=np.uint8)
    config = WideMinSumConfig(
        max_iterations=4, message_magnitude_bits=5, correction_shift=3,
    )
    # Priors well above the five-bit message field force saturation.  This is
    # the case that distinguishes normalize-then-clip from clip-then-normalize.
    priors = (100, 93, 87, 105, 96)
    batch = run_wide_layered_minsum_batch(graph, priors, syndromes, config=config)
    for index, syndrome in enumerate(syndromes):
        scalar = run_wide_layered_minsum(
            graph, priors, syndrome=syndrome, config=config,
        )
        assert bool(batch.success[index]) == scalar.success
        assert int(batch.iterations[index]) == scalar.iterations
        assert tuple(int(value) for value in batch.posterior[index]) == scalar.posterior
        assert tuple(int(value) for value in batch.correction[index]) == scalar.correction


def test_batched_decoder_matches_scalar_for_hardware_cheap_schedules_and_offset() -> None:
    graph = Graph.from_neighbors(
        5,
        ((0, 1, 2), (1, 3, 4), (0, 2, 4)),
        syndrome=(1, 1, 0),
    )
    syndromes = np.array(((1, 1, 0), (0, 1, 1)), dtype=np.uint8)
    priors = (100, 93, 87, 105, 96)
    for schedule in (
        "reverse", "alternating", "alternating_reverse",
        "pair_alternating", "pair_alternating_reverse",
        "cyclic", "cyclic_reverse", "cyclic_alternating",
        "cyclic_alternating_reverse",
    ):
        config = WideMinSumConfig(
            max_iterations=5,
            message_magnitude_bits=5,
            correction_shift=3,
            message_offset=3,
            check_schedule=schedule,
            schedule_stride=2,
        )
        batch = run_wide_layered_minsum_batch(graph, priors, syndromes, config=config)
        for index, syndrome in enumerate(syndromes):
            scalar = run_wide_layered_minsum(
                graph, priors, syndrome=syndrome, config=config,
            )
            assert bool(batch.success[index]) == scalar.success
            assert int(batch.iterations[index]) == scalar.iterations
            assert tuple(int(value) for value in batch.posterior[index]) == scalar.posterior
            assert tuple(int(value) for value in batch.correction[index]) == scalar.correction


def test_two_lane_schedule_matches_compiler_pair_order() -> None:
    """Software oracle serialises each c/c+36 hardware batch canonically."""

    graph = Graph.from_neighbors(2, tuple((0, 1) for _ in range(72)))
    forward = [check.id for check in _iteration_checks(graph, 1, "two_lane_forward")]
    reverse = [check.id for check in _iteration_checks(graph, 1, "two_lane_reverse")]
    assert forward[:8] == [0, 36, 1, 37, 2, 38, 3, 39]
    assert reverse[:8] == [71, 35, 70, 34, 69, 33, 68, 32]
    assert sorted(forward) == list(range(72))
    assert sorted(reverse) == list(range(72))
    WideMinSumConfig(check_schedule="two_lane_alternating").validate()


def test_four_lane_schedule_is_exact_static_clique_cover_order() -> None:
    graph = Graph.from_neighbors(2, tuple((0, 1) for _ in range(144)))
    forward = [check.id for check in _iteration_checks(graph, 1, "four_lane_forward")]
    reverse = [check.id for check in _iteration_checks(graph, 1, "four_lane_reverse")]
    assert forward[:8] == [0, 3, 37, 40, 1, 4, 38, 41]
    assert reverse[:8] == [141, 138, 107, 104, 143, 140, 106, 103]
    assert sorted(forward) == list(range(144))
    assert sorted(reverse) == list(range(144))
    WideMinSumConfig(check_schedule="four_lane_alternating_reverse").validate()


def test_four_lane_pair_schedule_preserves_proven_pair_order() -> None:
    graph = Graph.from_neighbors(2, tuple((0, 1) for _ in range(144)))
    forward = [check.id for check in _iteration_checks(
        graph, 1, "four_lane_pair_forward",
    )]
    reverse = [check.id for check in _iteration_checks(
        graph, 1, "four_lane_pair_reverse",
    )]
    expected = [base + coordinate for base in (0, 72)
                for group in GROSS144_FOUR_LANE_PAIR_GROUPS
                for coordinate in group]
    assert forward == expected
    assert reverse == list(reversed(expected))
    assert sorted(forward) == list(range(144))
    WideMinSumConfig(check_schedule="four_lane_pair_alternating_reverse").validate()


def test_four_lane_pair_cyclic_schedule_rotates_whole_time_slices() -> None:
    graph = Graph.from_neighbors(2, tuple((0, 1) for _ in range(216)))
    ordered = [check.id for check in _iteration_checks(
        graph, 2, "four_lane_pair_cyclic", stride=72,
    )]
    assert ordered[0] == 72
    assert sorted(ordered) == list(range(216))
    reverse = [check.id for check in _iteration_checks(
        graph, 2, "four_lane_pair_cyclic_reverse", stride=72,
    )]
    assert reverse[0] == 72 + GROSS144_FOUR_LANE_PAIR_GROUPS[-1][-1]
    assert sorted(reverse) == list(range(216))
