"""L01 fixed-point sum-product warm-up and compressed-state conversion."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Sequence

from .check_record import Check9Record, compress_outgoing
from .fixed_point import (
    MESSAGE_BITS,
    MESSAGE_MAG_MAX,
    POSTERIOR_BITS,
    fixed_check_update,
    saturate_signed,
    saturating_add,
    saturating_sub,
)
from .graph_model import Graph
from .sumproduct_reference import BoxPlusLUT, fixed_boxplus_check_update, generate_boxplus_lut


RELEASE_WARMUP_ITERATIONS = 2
DEFAULT_LUT_SCALE = 4
DEFAULT_LLR_SCALE = 4


@dataclass(frozen=True)
class WarmupConfig:
    """Frozen L01 release configuration and diagnostic controls."""

    warmup_iterations: int = RELEASE_WARMUP_ITERATIONS
    minsum_iterations: int = 6
    posterior_bits: int = POSTERIOR_BITS
    message_bits: int = MESSAGE_BITS
    lut_scale: int = DEFAULT_LUT_SCALE
    llr_scale: int = DEFAULT_LLR_SCALE
    diagnostic_zero_warmup: bool = False
    stop_on_success: bool = True

    def validate(self) -> None:
        if self.warmup_iterations not in (0, RELEASE_WARMUP_ITERATIONS):
            raise ValueError("release warm-up must be exactly two iterations")
        if self.warmup_iterations == 0 and not self.diagnostic_zero_warmup:
            raise ValueError("zero warm-up is diagnostic-only")
        if self.minsum_iterations < 0:
            raise ValueError("min-sum iterations must be non-negative")
        if self.posterior_bits != POSTERIOR_BITS:
            raise ValueError("release posterior width is fixed at 11 bits")
        if self.message_bits != MESSAGE_BITS:
            raise ValueError("release message width is fixed at 5 bits")
        if self.lut_scale < 1 or self.llr_scale < 1:
            raise ValueError("scales must be positive")

    def lut(self) -> BoxPlusLUT:
        self.validate()
        return generate_boxplus_lut(MESSAGE_MAG_MAX, self.lut_scale)


@dataclass(frozen=True)
class WarmupIteration:
    iteration: int
    posterior: tuple[int, ...]
    correction: tuple[int, ...]
    syndrome: tuple[int, ...]


@dataclass(frozen=True)
class WarmupState:
    posterior: tuple[int, ...]
    edge_messages: tuple[tuple[int, ...], ...]
    trace: tuple[WarmupIteration, ...]
    posterior_saturation_events: int
    message_saturation_events: int
    work: int


@dataclass(frozen=True)
class ConvertedState:
    posterior: tuple[int, ...]
    edge_messages: tuple[tuple[int, ...], ...]
    check_records: tuple[Check9Record | None, ...]
    packed_records: tuple[int | None, ...]
    saturation_events: int


@dataclass(frozen=True)
class CompressedDecodeResult:
    correction: tuple[int, ...]
    posterior: tuple[int, ...]
    syndrome: tuple[int, ...]
    success: bool
    iterations: int
    work: int


@dataclass(frozen=True)
class HybridDecodeResult:
    warmup: WarmupState
    converted: ConvertedState
    minsum: CompressedDecodeResult
    total_work: int


def quantize_llr(value: float, *, scale: int = DEFAULT_LLR_SCALE) -> int:
    """Convert a floating LLR to the frozen signed posterior representation."""

    if scale < 1:
        raise ValueError("scale must be positive")
    scaled = float(value) * scale
    rounded = int(floor(scaled + 0.5)) if scaled >= 0 else int(-floor(-scaled + 0.5))
    return saturate_signed(rounded, POSTERIOR_BITS)


def _validate_inputs(
    graph: Graph,
    prior_llr: Sequence[int],
    syndrome: Sequence[int] | None,
) -> tuple[int, ...]:
    if len(prior_llr) != graph.num_variables:
        raise ValueError("prior length must equal graph variable count")
    target = tuple(graph.syndrome if syndrome is None else syndrome)
    if len(target) != len(graph.checks) or any(int(bit) not in (0, 1) for bit in target):
        raise ValueError("syndrome must contain one binary bit per check")
    return target


def _correction(posterior: Sequence[int]) -> tuple[int, ...]:
    return tuple(int(value < 0) for value in posterior)


def _syndrome(graph: Graph, correction: Sequence[int]) -> tuple[int, ...]:
    return tuple(sum(int(correction[var]) for var in check.neighbors) & 1
                 for check in graph.checks)


def run_sum_product_warmup(
    graph: Graph,
    prior_llr: Sequence[int],
    *,
    syndrome: Sequence[int] | None = None,
    config: WarmupConfig | None = None,
) -> WarmupState:
    """Run the deterministic fixed-point box-plus warm-up portion."""

    config = config or WarmupConfig()
    config.validate()
    target = _validate_inputs(graph, prior_llr, syndrome)
    posterior = [saturate_signed(int(value), config.posterior_bits) for value in prior_llr]
    posterior_saturation_events = sum(
        int(int(value) != posterior[index]) for index, value in enumerate(prior_llr)
    )
    edge_messages = [[0 for _ in check.neighbors] for check in graph.checks]
    trace: list[WarmupIteration] = []
    message_saturation_events = 0
    lut = config.lut()

    for iteration in range(1, config.warmup_iterations + 1):
        for check in graph.checks:
            old = tuple(edge_messages[check.id])
            extrinsic: list[int] = []
            for var, old_edge in zip(check.neighbors, old):
                raw = posterior[var] - old_edge
                bounded = saturate_signed(raw, config.posterior_bits)
                posterior_saturation_events += int(raw != bounded)
                extrinsic.append(bounded)
            new = fixed_boxplus_check_update(
                extrinsic, syndrome_bit=target[check.id], lut=lut
            )
            message_saturation_events += sum(
                int(abs(value) >= MESSAGE_MAG_MAX) for value in new
            )
            for edge, (var, old_edge, new_edge) in enumerate(
                zip(check.neighbors, old, new)
            ):
                raw = posterior[var] - old_edge + new_edge
                bounded = saturate_signed(raw, config.posterior_bits)
                posterior_saturation_events += int(raw != bounded)
                posterior[var] = bounded
                edge_messages[check.id][edge] = new_edge
        correction = _correction(posterior)
        trace.append(WarmupIteration(
            iteration, tuple(posterior), correction, _syndrome(graph, correction)
        ))

    return WarmupState(
        tuple(posterior), tuple(tuple(row) for row in edge_messages), tuple(trace),
        posterior_saturation_events, message_saturation_events,
        config.warmup_iterations * len(graph.checks),
    )


def convert_to_compressed_state(
    graph: Graph,
    state: WarmupState,
    *,
    syndrome: Sequence[int] | None = None,
) -> ConvertedState:
    """Convert warm-up extrinsics into deterministic normalized min-sum state."""

    target = _validate_inputs(graph, state.posterior, syndrome)
    if len(state.edge_messages) != len(graph.checks):
        raise ValueError("warm-up edge state check count mismatch")
    converted: list[tuple[int, ...]] = []
    records: list[Check9Record | None] = []
    packed: list[int | None] = []
    saturation_events = 0
    for check in graph.checks:
        old = state.edge_messages[check.id]
        if len(old) != len(check.neighbors):
            raise ValueError("warm-up edge state degree mismatch")
        extrinsic: list[int] = []
        for var, old_edge in zip(check.neighbors, old):
            raw = state.posterior[var] - old_edge
            bounded = saturate_signed(raw, POSTERIOR_BITS)
            saturation_events += int(raw != bounded)
            extrinsic.append(bounded)
        outgoing = fixed_check_update(extrinsic, syndrome_bit=target[check.id])
        converted.append(tuple(outgoing))
        if len(check.neighbors) == 9:
            record = compress_outgoing(outgoing)
            records.append(record)
            packed.append(record.pack())
        else:
            records.append(None)
            packed.append(None)
    return ConvertedState(
        state.posterior, tuple(converted), tuple(records), tuple(packed), saturation_events
    )


def run_compressed_minsum(
    graph: Graph,
    state: ConvertedState,
    *,
    syndrome: Sequence[int] | None = None,
    max_iterations: int = 6,
    stop_on_success: bool = True,
) -> CompressedDecodeResult:
    """Continue from converted state using the release layered min-sum update."""

    if max_iterations < 0:
        raise ValueError("max_iterations must be non-negative")
    target = _validate_inputs(graph, state.posterior, syndrome)
    if len(state.edge_messages) != len(graph.checks):
        raise ValueError("converted edge state check count mismatch")
    posterior = list(state.posterior)
    edge_messages = [list(row) for row in state.edge_messages]
    correction = _correction(posterior)
    actual = _syndrome(graph, correction)
    success_latched = actual == target
    if actual == target and stop_on_success:
        return CompressedDecodeResult(correction, tuple(posterior), actual, True, 0, 0)

    for iteration in range(1, max_iterations + 1):
        if success_latched:
            continue
        for check in graph.checks:
            old = tuple(edge_messages[check.id])
            extrinsic = tuple(
                saturating_sub(posterior[var], old_edge)
                for var, old_edge in zip(check.neighbors, old)
            )
            new = fixed_check_update(extrinsic, syndrome_bit=target[check.id])
            for edge, (var, old_edge, new_edge) in enumerate(
                zip(check.neighbors, old, new)
            ):
                posterior[var] = saturating_add(
                    saturating_sub(posterior[var], old_edge), new_edge
                )
                edge_messages[check.id][edge] = new_edge
        correction = _correction(posterior)
        actual = _syndrome(graph, correction)
        if actual == target:
            success_latched = True
            if stop_on_success:
                return CompressedDecodeResult(
                    correction, tuple(posterior), actual, True, iteration,
                    iteration * len(graph.checks)
                )
    return CompressedDecodeResult(
        correction, tuple(posterior), actual, success_latched, max_iterations,
        max_iterations * len(graph.checks)
    )


def run_hybrid(
    graph: Graph,
    prior_llr: Sequence[int],
    *,
    syndrome: Sequence[int] | None = None,
    config: WarmupConfig | None = None,
) -> HybridDecodeResult:
    config = config or WarmupConfig()
    warmup = run_sum_product_warmup(
        graph, prior_llr, syndrome=syndrome, config=config
    )
    converted = convert_to_compressed_state(graph, warmup, syndrome=syndrome)
    minsum = run_compressed_minsum(
        graph, converted, syndrome=syndrome, max_iterations=config.minsum_iterations,
        stop_on_success=config.stop_on_success,
    )
    return HybridDecodeResult(warmup, converted, minsum, warmup.work + minsum.work)
