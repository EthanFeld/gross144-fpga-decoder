"""Wide compressed normalized-min-sum Stage-1 for streamed FPGA graphs.

The original S1 warm-up emits four-bit magnitudes.  On the paper Gross144
degree-35 graph this clips useful check evidence before it reaches a variable.
This decoder starts directly in normalized min-sum, retaining a configurable
five-bit magnitude in each compressed check record.  It keeps layered updates,
the 11-bit posterior contract, fixed normalization, and exact syndrome-only
acceptance; it needs no host decoder or physical-error truth input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .fixed_point import normalize_magnitude, saturating_add, saturating_sub
from .graph_model import Graph

# Exact static four-check clique cover of the Gross144 Z12 x Z6 quotient.
# Every tuple is pairwise variable-disjoint for every one of the 13 S1 check
# templates in both bases.  Hardware may therefore execute a tuple in
# parallel without changing layered min-sum semantics.
GROSS144_FOUR_LANE_GROUPS = (
    (0, 3, 37, 40), (1, 4, 38, 41), (2, 5, 36, 39),
    (6, 9, 43, 46), (7, 10, 44, 47), (8, 11, 42, 45),
    (12, 15, 49, 52), (13, 16, 50, 53), (14, 17, 48, 51),
    (18, 21, 55, 58), (19, 22, 56, 59), (20, 23, 54, 57),
    (24, 27, 61, 64), (25, 28, 62, 65), (26, 29, 60, 63),
    (30, 33, 67, 70), (31, 34, 68, 71), (32, 35, 66, 69),
)

# Exact four-lane cover that preserves the proven ``c, c + 36`` pair order.
# A minimum-distance perfect matching of the 36 pair nodes supplies the other
# independent pair in each group. This keeps four-way execution close to the
# lower-defer pair-layered schedule.
GROSS144_FOUR_LANE_PAIR_GROUPS = (
    (0, 36, 2, 38), (1, 37, 35, 71), (3, 39, 5, 41),
    (4, 40, 6, 42), (7, 43, 9, 45), (8, 44, 10, 46),
    (11, 47, 13, 49), (12, 48, 14, 50), (15, 51, 17, 53),
    (16, 52, 18, 54), (19, 55, 21, 57), (20, 56, 22, 58),
    (23, 59, 25, 61), (24, 60, 26, 62), (27, 63, 29, 65),
    (28, 64, 30, 66), (31, 67, 33, 69), (32, 68, 34, 70),
)


@dataclass(frozen=True)
class WideMinSumConfig:
    """S1W arithmetic contract; magnitude bits map to min1/min2 record width."""

    max_iterations: int = 20
    message_magnitude_bits: int = 5
    correction_shift: int = 3  # alpha = 1 - 2^-3 = 0.875
    message_offset: int = 0
    check_schedule: str = "forward"
    schedule_stride: int = 1
    stop_on_success: bool = True

    def validate(self) -> None:
        if self.max_iterations < 0:
            raise ValueError("max iterations must be non-negative")
        if not 4 <= self.message_magnitude_bits <= 8:
            raise ValueError("message magnitude width must be in [4, 8]")
        if self.correction_shift < 1:
            raise ValueError("normalization correction shift must be positive")
        if self.message_offset < 0:
            raise ValueError("message offset must be non-negative")
        if self.check_schedule not in (
            "forward", "reverse", "alternating", "alternating_reverse",
            "pair_alternating", "pair_alternating_reverse",
            "cyclic", "cyclic_reverse", "cyclic_alternating",
            "cyclic_alternating_reverse",
            "two_lane_forward", "two_lane_reverse", "two_lane_alternating",
            "two_lane_alternating_reverse",
            "four_lane_forward", "four_lane_reverse", "four_lane_alternating",
            "four_lane_alternating_reverse",
            "four_lane_pair_forward", "four_lane_pair_reverse",
            "four_lane_pair_alternating", "four_lane_pair_alternating_reverse",
            "four_lane_pair_cyclic", "four_lane_pair_cyclic_reverse",
            "four_lane_pair_cyclic_alternating",
            "four_lane_pair_cyclic_alternating_reverse",
        ):
            raise ValueError("unknown wide min-sum check schedule")
        if self.schedule_stride < 1:
            raise ValueError("check schedule stride must be positive")

    @property
    def message_magnitude_max(self) -> int:
        return (1 << self.message_magnitude_bits) - 1


@dataclass(frozen=True)
class WideMinSumResult:
    correction: tuple[int, ...]
    posterior: tuple[int, ...]
    syndrome: tuple[int, ...]
    success: bool
    iterations: int
    work: int


@dataclass(frozen=True)
class WideMinSumBatchResult:
    """Bit-exact batched form of :class:`WideMinSumResult` for Monte Carlo."""

    correction: np.ndarray
    posterior: np.ndarray
    syndrome: np.ndarray
    success: np.ndarray
    iterations: np.ndarray
    work: np.ndarray


def _target(graph: Graph, syndrome: Sequence[int] | None) -> tuple[int, ...]:
    target = tuple(graph.syndrome if syndrome is None else (int(value) for value in syndrome))
    if len(target) != len(graph.checks) or any(value not in (0, 1) for value in target):
        raise ValueError("syndrome must contain one binary bit per check")
    return target


def _syndrome(graph: Graph, correction: Sequence[int]) -> tuple[int, ...]:
    return tuple(sum(int(correction[var]) for var in check.neighbors) & 1
                 for check in graph.checks)


def _wide_check_update(
    extrinsic: Sequence[int], *, syndrome_bit: int, config: WideMinSumConfig,
) -> tuple[int, ...]:
    """One stable-order normalized-min-sum check update with compressed state."""

    if len(extrinsic) < 2:
        raise ValueError("wide min-sum requires check degree >= 2")
    magnitudes = [abs(int(value)) for value in extrinsic]
    argmin = min(range(len(magnitudes)), key=lambda index: (magnitudes[index], index))
    minimum = magnitudes[argmin]
    second_minimum = min(
        magnitude for index, magnitude in enumerate(magnitudes) if index != argmin
    )
    parity = int(syndrome_bit)
    for value in extrinsic:
        parity ^= int(value < 0)
    output: list[int] = []
    for index, value in enumerate(extrinsic):
        magnitude = second_minimum if index == argmin else minimum
        normalized = normalize_magnitude(
            max(0, magnitude - config.message_offset),
            correction_shift=config.correction_shift,
            max_magnitude=config.message_magnitude_max,
        )
        sign = parity ^ int(value < 0)
        output.append(-normalized if sign else normalized)
    return tuple(output)


def _iteration_checks(
    graph: Graph, iteration: int, schedule: str, stride: int = 1,
):
    """Return a ROM-addressable layered order for one sweep.

    All supported schedules are a forward/reverse counter plus, for the two
    alternating forms, one direction bit driven by sweep parity.  They need no
    permutation RAM and do not add check-update cycles in hardware.
    """

    two_lane = schedule.startswith("two_lane_")
    four_lane = schedule.startswith("four_lane_")
    reverse = schedule in (
        "reverse", "cyclic_reverse", "two_lane_reverse", "four_lane_reverse",
        "four_lane_pair_reverse",
        "four_lane_pair_cyclic_reverse",
    ) or (
        schedule == "alternating" and iteration % 2 == 0
    ) or (
        schedule == "alternating_reverse" and iteration % 2 == 1
    ) or (
        schedule == "pair_alternating" and (iteration - 1) % 4 >= 2
    ) or (
        schedule == "pair_alternating_reverse" and (iteration - 1) % 4 < 2
    ) or (
        schedule == "cyclic_alternating" and iteration % 2 == 0
    ) or (
        schedule == "cyclic_alternating_reverse" and iteration % 2 == 1
    ) or (
        schedule == "two_lane_alternating" and iteration % 2 == 0
    ) or (
        schedule == "two_lane_alternating_reverse" and iteration % 2 == 1
    ) or (
        schedule == "four_lane_alternating" and iteration % 2 == 0
    ) or (
        schedule == "four_lane_alternating_reverse" and iteration % 2 == 1
    ) or (
        schedule == "four_lane_pair_alternating" and iteration % 2 == 0
    ) or (
        schedule == "four_lane_pair_alternating_reverse" and iteration % 2 == 1
    ) or (
        schedule == "four_lane_pair_cyclic_alternating" and iteration % 2 == 0
    ) or (
        schedule == "four_lane_pair_cyclic_alternating_reverse" and iteration % 2 == 1
    )
    if four_lane:
        spatial_checks = 72
        if len(graph.checks) % spatial_checks:
            raise ValueError("four-lane schedule requires 72 spatial checks per time slice")
        time_bases = list(range(0, len(graph.checks), spatial_checks))
        groups = list(GROSS144_FOUR_LANE_PAIR_GROUPS if
                      schedule.startswith("four_lane_pair_") else
                      GROSS144_FOUR_LANE_GROUPS)
        if schedule.startswith("four_lane_pair_cyclic"):
            if stride % spatial_checks:
                raise ValueError("four-lane cyclic stride must be a multiple of 72")
            start = ((iteration - 1) * (stride // spatial_checks)) % len(time_bases)
            step = -1 if reverse else 1
            time_bases = [time_bases[(start + step * offset) % len(time_bases)]
                          for offset in range(len(time_bases))]
        elif reverse:
            time_bases.reverse()
        if reverse:
            groups.reverse()
        ordered = []
        for time_base in time_bases:
            for group in groups:
                coordinates = reversed(group) if reverse else group
                ordered.extend(graph.checks[time_base + coordinate]
                               for coordinate in coordinates)
        return tuple(ordered)
    if two_lane:
        # The S1 two-lane hardware launches spatial checks c and c+36
        # together. They have compiler-proven disjoint posterior sets, so
        # serialising each pair in this order is bit-exact to their concurrent
        # engine execution while preserving a deterministic software oracle.
        spatial_checks = 72
        pair_delta = 36
        if len(graph.checks) % spatial_checks:
            raise ValueError("two-lane schedule requires 72 spatial checks per time slice")
        ordered = []
        for time_base in range(0, len(graph.checks), spatial_checks):
            if reverse:
                for coordinate in range(pair_delta - 1, -1, -1):
                    ordered.append(graph.checks[time_base + coordinate + pair_delta])
                    ordered.append(graph.checks[time_base + coordinate])
            else:
                for coordinate in range(pair_delta):
                    ordered.append(graph.checks[time_base + coordinate])
                    ordered.append(graph.checks[time_base + coordinate + pair_delta])
        return tuple(ordered)
    if schedule.startswith("cyclic"):
        start = ((iteration - 1) * stride) % len(graph.checks)
        step = -1 if reverse else 1
        return (
            graph.checks[(start + step * offset) % len(graph.checks)]
            for offset in range(len(graph.checks))
        )
    return reversed(graph.checks) if reverse else graph.checks


def run_wide_layered_minsum(
    graph: Graph,
    prior_llr: Sequence[int],
    *,
    syndrome: Sequence[int] | None = None,
    config: WideMinSumConfig | None = None,
) -> WideMinSumResult:
    """Run S1W from channel priors with bounded fixed-point layered updates."""

    config = config or WideMinSumConfig()
    config.validate()
    target = _target(graph, syndrome)
    if len(prior_llr) != graph.num_variables:
        raise ValueError("prior width mismatch")
    posterior = [saturating_add(0, int(value)) for value in prior_llr]
    edge_messages = [[0 for _ in check.neighbors] for check in graph.checks]
    correction = tuple(int(value < 0) for value in posterior)
    actual = _syndrome(graph, correction)
    if actual == target and config.stop_on_success:
        return WideMinSumResult(correction, tuple(posterior), actual, True, 0, 0)

    for iteration in range(1, config.max_iterations + 1):
        for check in _iteration_checks(
            graph, iteration, config.check_schedule, config.schedule_stride,
        ):
            old = tuple(edge_messages[check.id])
            extrinsic = tuple(
                saturating_sub(posterior[variable], old_message)
                for variable, old_message in zip(check.neighbors, old)
            )
            new = _wide_check_update(
                extrinsic, syndrome_bit=target[check.id], config=config,
            )
            for edge, (variable, old_message, new_message) in enumerate(
                zip(check.neighbors, old, new)
            ):
                posterior[variable] = saturating_add(
                    saturating_sub(posterior[variable], old_message), new_message,
                )
                edge_messages[check.id][edge] = new_message
        correction = tuple(int(value < 0) for value in posterior)
        actual = _syndrome(graph, correction)
        if actual == target and config.stop_on_success:
            return WideMinSumResult(
                correction, tuple(posterior), actual, True, iteration,
                iteration * len(graph.checks),
            )
    return WideMinSumResult(
        correction, tuple(posterior), actual, actual == target,
        config.max_iterations, config.max_iterations * len(graph.checks),
    )


def _syndrome_batch(graph: Graph, correction: np.ndarray) -> np.ndarray:
    result = np.empty((correction.shape[0], len(graph.checks)), dtype=np.uint8)
    for check in graph.checks:
        result[:, check.id] = np.bitwise_and(
            np.sum(correction[:, check.neighbors], axis=1, dtype=np.int16), 1,
        )
    return result


def run_wide_layered_minsum_batch(
    graph: Graph,
    prior_llr: Sequence[int],
    syndromes: np.ndarray,
    *,
    config: WideMinSumConfig | None = None,
) -> WideMinSumBatchResult:
    """Run independent S1W shots in parallel with scalar-identical updates.

    Batch dimension is only a simulation accelerator. Every row follows the
    same layered check order, saturation, tie break, and early-stop rule as
    :func:`run_wide_layered_minsum`.
    """

    config = config or WideMinSumConfig()
    config.validate()
    target = np.asarray(syndromes, dtype=np.uint8)
    if target.ndim != 2 or target.shape[1] != len(graph.checks) or \
            np.any((target != 0) & (target != 1)):
        raise ValueError("syndromes must be a binary [shots, checks] array")
    if len(prior_llr) != graph.num_variables:
        raise ValueError("prior width mismatch")
    shots = target.shape[0]
    posterior = np.broadcast_to(
        np.asarray(prior_llr, dtype=np.int16), (shots, graph.num_variables),
    ).copy()
    np.clip(posterior, -1024, 1023, out=posterior)
    edge_messages = [np.zeros((shots, len(check.neighbors)), dtype=np.int16)
                     for check in graph.checks]
    correction = (posterior < 0).astype(np.uint8)
    actual = _syndrome_batch(graph, correction)
    success = np.all(actual == target, axis=1)
    iterations = np.zeros(shots, dtype=np.int16)

    for iteration in range(1, config.max_iterations + 1):
        active = np.flatnonzero(~success)
        if not len(active):
            break
        for check in _iteration_checks(
            graph, iteration, config.check_schedule, config.schedule_stride,
        ):
            neighbors = np.asarray(check.neighbors, dtype=np.intp)
            old = edge_messages[check.id][active].copy()
            values = posterior[active[:, None], neighbors]
            extrinsic = values.astype(np.int32) - old
            np.clip(extrinsic, -1024, 1023, out=extrinsic)
            magnitudes = np.abs(extrinsic)
            argmin = np.argmin(magnitudes, axis=1)
            minimum = magnitudes[np.arange(len(active)), argmin]
            # ``partition(..., 1)`` preserves repeated minima, exactly giving
            # min over all positions except the stable first argmin.
            second_minimum = np.partition(magnitudes, 1, axis=1)[:, 1]
            parity = target[active, check.id].astype(np.uint8)
            parity ^= np.bitwise_and(np.count_nonzero(extrinsic < 0, axis=1), 1).astype(np.uint8)
            message_magnitude = np.broadcast_to(minimum[:, None], extrinsic.shape).copy()
            message_magnitude[np.arange(len(active)), argmin] = second_minimum
            if config.message_offset:
                message_magnitude -= config.message_offset
                np.maximum(message_magnitude, 0, out=message_magnitude)
            message_magnitude -= message_magnitude >> config.correction_shift
            # Match ``normalize_magnitude`` and the RTL exactly: normalize the
            # raw 11-bit minimum first, then saturate into the check-record
            # magnitude field.  Clipping first changes every saturated value
            # (for example 100 becomes 28 rather than 31 at shift=3/bits=5).
            np.minimum(message_magnitude, config.message_magnitude_max, out=message_magnitude)
            sign = (extrinsic < 0) ^ parity[:, None].astype(bool)
            new = np.where(sign, -message_magnitude, message_magnitude).astype(np.int16)
            updated = values.astype(np.int32) - old + new
            np.clip(updated, -1024, 1023, out=updated)
            posterior[active[:, None], neighbors] = updated.astype(np.int16)
            edge_messages[check.id][active] = new
        correction = (posterior < 0).astype(np.uint8)
        actual = _syndrome_batch(graph, correction)
        newly_successful = (~success) & np.all(actual == target, axis=1)
        iterations[newly_successful] = iteration
        success |= newly_successful

    iterations[~success] = config.max_iterations
    return WideMinSumBatchResult(
        correction=correction,
        posterior=posterior,
        syndrome=actual,
        success=success,
        iterations=iterations,
        work=iterations.astype(np.int32) * len(graph.checks),
    )
