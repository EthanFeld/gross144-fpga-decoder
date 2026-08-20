"""K01 sequential Relay-BP control/reference model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence

from .candidate_score import Candidate, make_candidate, select_best
from .fixed_point import saturating_add
from .graph_model import Graph
from .minsum_reference import FixedDecodeResult, fixed_point_layered_min_sum


class RelayStatus(str, Enum):
    SUCCESS = "SUCCESS"
    NO_VALID_LEGS = "NO_VALID_LEGS"
    NO_QUORUM = "NO_QUORUM"
    TIE = "TIE"


@dataclass(frozen=True)
class RelayCheckpoint:
    prior_llr: tuple[int, ...]
    syndrome: tuple[int, ...]
    state_tag: str = "baseline"


@dataclass(frozen=True)
class RelayLegConfig:
    leg_id: int
    perturbation: tuple[int, ...] = ()
    max_iterations: int = 2
    enabled: bool = True


@dataclass(frozen=True)
class RelayConfig:
    legs: tuple[RelayLegConfig, ...]
    quorum: int = 1


@dataclass(frozen=True)
class RelayLegResult:
    leg_id: int
    success: bool
    candidate: Candidate | None
    score: float
    logical_class: int | None
    work: int
    reason: str


@dataclass(frozen=True)
class RelayResult:
    status: RelayStatus
    selected_candidate: Candidate | None
    winning_logical_class: int | None
    vote_counts: tuple[tuple[int, int], ...]
    legs: tuple[RelayLegResult, ...]
    total_work: int


Decoder = Callable[[Graph, Sequence[int], Sequence[int], int, RelayLegConfig], FixedDecodeResult]


def save_checkpoint(prior_llr: Sequence[int], syndrome: Sequence[int], *,
                    state_tag: str = "baseline") -> RelayCheckpoint:
    return RelayCheckpoint(tuple(int(value) for value in prior_llr),
                           tuple(int(bit) for bit in syndrome), state_tag)


def run_relay(
    graph: Graph,
    checkpoint: RelayCheckpoint,
    config: RelayConfig,
    *,
    logical_signatures: Sequence[Sequence[int]] | None = None,
    decoder: Decoder | None = None,
) -> RelayResult:
    _validate_config(graph, checkpoint, config, logical_signatures)
    decoder = decoder or _default_decoder
    leg_results: list[RelayLegResult] = []
    valid_candidates: list[Candidate] = []
    total_work = 0
    for leg in config.legs:
        if not leg.enabled:
            leg_results.append(RelayLegResult(
                leg.leg_id, False, None, float("inf"), None, 0, "disabled"
            ))
            continue
        leg_prior = checkpoint.prior_llr if not leg.perturbation else tuple(
            saturating_add(base, delta)
            for base, delta in zip(checkpoint.prior_llr, leg.perturbation)
        )
        decoded = decoder(graph, leg_prior, checkpoint.syndrome, leg.max_iterations, leg)
        work = decoded.iterations * len(graph.checks)
        total_work += work
        if not decoded.success:
            leg_results.append(RelayLegResult(
                leg.leg_id, False, None, float("inf"), None, work, "decode failure"
            ))
            continue
        candidate = make_candidate(
            graph, decoded.correction, source_stage="S2R", trial_id=leg.leg_id,
            syndrome=checkpoint.syndrome, prior_llr=leg_prior,
            logical_signatures=logical_signatures,
        )
        if not candidate.valid or not candidate.syndrome_satisfied:
            leg_results.append(RelayLegResult(
                leg.leg_id, False, None, float("inf"), None, work,
                "candidate syndrome failure"
            ))
            continue
        valid_candidates.append(candidate)
        leg_results.append(RelayLegResult(
            leg.leg_id, True, candidate,
            candidate.negative_log_likelihood_or_weight, candidate.logical_class,
            work, "success"
        ))

    if not valid_candidates:
        return RelayResult(RelayStatus.NO_VALID_LEGS, None, None, (),
                           tuple(leg_results), total_work)
    counts: dict[int, int] = {}
    for candidate in valid_candidates:
        counts[candidate.logical_class] = counts.get(candidate.logical_class, 0) + 1
    vote_counts = tuple(sorted(counts.items()))
    best_votes = max(counts.values())
    if best_votes < config.quorum:
        return RelayResult(RelayStatus.NO_QUORUM, None, None, vote_counts,
                           tuple(leg_results), total_work)
    winners = tuple(logical_class for logical_class, count in counts.items()
                    if count == best_votes)
    if len(winners) != 1:
        return RelayResult(RelayStatus.TIE, None, None, vote_counts,
                           tuple(leg_results), total_work)
    winning_class = winners[0]
    selected = select_best(candidate for candidate in valid_candidates
                           if candidate.logical_class == winning_class)
    return RelayResult(RelayStatus.SUCCESS, selected, winning_class, vote_counts,
                       tuple(leg_results), total_work)


def _default_decoder(graph: Graph, prior_llr: Sequence[int], syndrome: Sequence[int],
                     max_iterations: int, _leg: RelayLegConfig) -> FixedDecodeResult:
    return fixed_point_layered_min_sum(
        graph, prior_llr, syndrome=syndrome, max_iterations=max_iterations
    )


def _validate_config(graph: Graph, checkpoint: RelayCheckpoint, config: RelayConfig,
                     logical_signatures: Sequence[Sequence[int]] | None) -> None:
    if not config.legs:
        raise ValueError("relay requires at least one leg")
    if config.quorum < 1 or config.quorum > len(config.legs):
        raise ValueError("quorum must be within leg count")
    if len(checkpoint.prior_llr) != graph.num_variables:
        raise ValueError("checkpoint prior width mismatch")
    if len(checkpoint.syndrome) != len(graph.checks) or any(
        bit not in (0, 1) for bit in checkpoint.syndrome
    ):
        raise ValueError("checkpoint syndrome mismatch")
    ids = [leg.leg_id for leg in config.legs]
    if ids != list(range(len(ids))) or len(set(ids)) != len(ids):
        raise ValueError("leg IDs must be ordered and unique")
    for leg in config.legs:
        if leg.max_iterations < 0:
            raise ValueError("leg max_iterations must be non-negative")
        if leg.perturbation and len(leg.perturbation) != graph.num_variables:
            raise ValueError("leg perturbation width mismatch")
    if logical_signatures is not None:
        for signature in logical_signatures:
            if len(signature) != graph.num_variables:
                raise ValueError("logical signature width mismatch")
