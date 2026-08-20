"""Readable floating-point layered normalized min-sum reference."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Sequence

from .graph_model import Graph
from .fixed_point import fixed_check_update, saturating_add, saturating_sub


@dataclass(frozen=True)
class CheckUpdate:
    old_messages: tuple[float, ...]
    extrinsic: tuple[float, ...]
    new_messages: tuple[float, ...]


@dataclass(frozen=True)
class IterationTrace:
    iteration: int
    posterior: tuple[float, ...]
    correction: tuple[int, ...]
    syndrome: tuple[int, ...]
    check_updates: tuple[CheckUpdate, ...]


@dataclass(frozen=True)
class DecodeResult:
    correction: tuple[int, ...]
    posterior: tuple[float, ...]
    syndrome: tuple[int, ...]
    success: bool
    iterations: int
    trace: tuple[IterationTrace, ...]


@dataclass(frozen=True)
class FixedCheckUpdate:
    old_messages: tuple[int, ...]
    extrinsic: tuple[int, ...]
    new_messages: tuple[int, ...]
    posterior: tuple[int, ...]


@dataclass(frozen=True)
class FixedIterationTrace:
    iteration: int
    posterior: tuple[int, ...]
    correction: tuple[int, ...]
    syndrome: tuple[int, ...]
    check_updates: tuple[FixedCheckUpdate, ...]


@dataclass(frozen=True)
class FixedDecodeResult:
    correction: tuple[int, ...]
    posterior: tuple[int, ...]
    syndrome: tuple[int, ...]
    success: bool
    iterations: int
    trace: tuple[FixedIterationTrace, ...]


def _normalized_magnitude(value: float, normalization: float) -> float:
    if not 0.0 <= normalization <= 1.0:
        raise ValueError("normalization must be between zero and one")
    # Explicit floor makes ties and Python/RTL integer conversion deterministic.
    return float(floor(abs(value) * normalization + 1e-12))


def check_update(
    extrinsic: Sequence[float],
    *,
    syndrome_bit: int = 0,
    normalization: float = 0.75,
) -> tuple[float, ...]:
    """Return normalized min-sum check-to-variable messages in edge order."""

    if syndrome_bit not in (0, 1):
        raise ValueError("syndrome_bit must be binary")
    if not extrinsic:
        return ()
    result: list[float] = []
    for edge, _ in enumerate(extrinsic):
        others = [value for index, value in enumerate(extrinsic) if index != edge]
        if not others:
            result.append(0.0)
            continue
        negative = syndrome_bit
        for value in others:
            negative ^= int(value < 0.0)
        magnitude = min(_normalized_magnitude(value, normalization) for value in others)
        result.append(-magnitude if negative else magnitude)
    return tuple(result)


def compute_syndrome(graph: Graph, correction: Sequence[int]) -> tuple[int, ...]:
    if len(correction) != graph.num_variables:
        raise ValueError("correction length must equal variable count")
    if any(int(bit) not in (0, 1) for bit in correction):
        raise ValueError("correction must be binary")
    return tuple(sum(int(correction[var]) for var in check.neighbors) & 1
                 for check in graph.checks)


def layered_min_sum(
    graph: Graph,
    prior_llr: Sequence[float],
    *,
    syndrome: Sequence[int] | None = None,
    normalization: float = 0.75,
    max_iterations: int = 10,
) -> DecodeResult:
    """Decode with deterministic row/neighbor order and layered updates.

    Positive LLR favors correction bit zero. A result is successful only when
    its hard decision exactly matches supplied syndrome.
    """

    if len(prior_llr) != graph.num_variables:
        raise ValueError("prior_llr length must equal variable count")
    if max_iterations < 0:
        raise ValueError("max_iterations must be non-negative")
    target = tuple(graph.syndrome if syndrome is None else syndrome)
    if len(target) != len(graph.checks) or any(bit not in (0, 1) for bit in target):
        raise ValueError("syndrome must contain one binary bit per check")

    posterior = [float(value) for value in prior_llr]
    edge_messages: list[list[float]] = [
        [0.0 for _ in check.neighbors] for check in graph.checks
    ]
    traces: list[IterationTrace] = []

    def snapshot(iteration: int, updates: list[CheckUpdate]) -> IterationTrace:
        correction = tuple(int(value < 0.0) for value in posterior)
        return IterationTrace(
            iteration=iteration,
            posterior=tuple(posterior),
            correction=correction,
            syndrome=compute_syndrome(graph, correction),
            check_updates=tuple(updates),
        )

    initial_correction = tuple(int(value < 0.0) for value in posterior)
    initial_syndrome = compute_syndrome(graph, initial_correction)
    if initial_syndrome == target:
        return DecodeResult(initial_correction, tuple(posterior), initial_syndrome,
                            True, 0, ())

    for iteration in range(1, max_iterations + 1):
        updates: list[CheckUpdate] = []
        for check in graph.checks:
            old = tuple(edge_messages[check.id])
            extrinsic = tuple(posterior[var] - old_edge
                              for var, old_edge in zip(check.neighbors, old))
            new = check_update(extrinsic, syndrome_bit=target[check.id],
                               normalization=normalization)
            for edge, (var, old_edge, new_edge) in enumerate(
                    zip(check.neighbors, old, new)):
                posterior[var] = posterior[var] - old_edge + new_edge
                edge_messages[check.id][edge] = new_edge
            updates.append(CheckUpdate(old, extrinsic, new))
        traces.append(snapshot(iteration, updates))
        correction = traces[-1].correction
        actual = traces[-1].syndrome
        if actual == target:
            return DecodeResult(correction, tuple(posterior), actual, True,
                                iteration, tuple(traces))

    correction = tuple(int(value < 0.0) for value in posterior)
    actual = compute_syndrome(graph, correction)
    return DecodeResult(correction, tuple(posterior), actual, False,
                        max_iterations, tuple(traces))


def fixed_point_layered_min_sum(
    graph: Graph,
    prior_llr: Sequence[int],
    *,
    syndrome: Sequence[int] | None = None,
    max_iterations: int = 10,
) -> FixedDecodeResult:
    """B03 full-edge fixed-point reference using release widths."""

    if len(prior_llr) != graph.num_variables:
        raise ValueError("prior_llr length must equal variable count")
    if max_iterations < 0:
        raise ValueError("max_iterations must be non-negative")
    target = tuple(graph.syndrome if syndrome is None else syndrome)
    if len(target) != len(graph.checks) or any(bit not in (0, 1) for bit in target):
        raise ValueError("syndrome must contain one binary bit per check")

    posterior = [saturating_add(int(value), 0) for value in prior_llr]
    edge_messages: list[list[int]] = [[0 for _ in check.neighbors] for check in graph.checks]
    traces: list[FixedIterationTrace] = []

    def correction_now() -> tuple[int, ...]:
        return tuple(int(value < 0) for value in posterior)

    initial_correction = correction_now()
    initial_syndrome = compute_syndrome(graph, initial_correction)
    if initial_syndrome == target:
        return FixedDecodeResult(initial_correction, tuple(posterior), initial_syndrome,
                                 True, 0, ())

    for iteration in range(1, max_iterations + 1):
        updates: list[FixedCheckUpdate] = []
        for check in graph.checks:
            old = tuple(edge_messages[check.id])
            extrinsic = tuple(saturating_sub(posterior[var], old_edge)
                              for var, old_edge in zip(check.neighbors, old))
            new = fixed_check_update(extrinsic, syndrome_bit=target[check.id])
            for edge, (var, old_edge, new_edge) in enumerate(
                    zip(check.neighbors, old, new)):
                posterior[var] = saturating_add(
                    saturating_sub(posterior[var], old_edge), new_edge
                )
                edge_messages[check.id][edge] = new_edge
            updates.append(FixedCheckUpdate(old, extrinsic, new,
                                            tuple(posterior[var] for var in check.neighbors)))
        correction = correction_now()
        actual = compute_syndrome(graph, correction)
        traces.append(FixedIterationTrace(iteration, tuple(posterior), correction,
                                          actual, tuple(updates)))
        if actual == target:
            return FixedDecodeResult(correction, tuple(posterior), actual, True,
                                     iteration, tuple(traces))

    correction = correction_now()
    actual = compute_syndrome(graph, correction)
    return FixedDecodeResult(correction, tuple(posterior), actual, False,
                             max_iterations, tuple(traces))
