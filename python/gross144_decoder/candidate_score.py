"""B07 common candidate schema, logical class, and deterministic selection."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from .graph_model import Graph
from .minsum_reference import compute_syndrome


STAGE_PRIORITY = {"S0": 0, "S1": 1, "S1A": 2, "S2": 3, "S2R": 4, "HOST": 5}


@dataclass(frozen=True)
class Candidate:
    valid: bool
    syndrome_satisfied: bool
    negative_log_likelihood_or_weight: float
    logical_class: int
    source_stage: str
    trial_id: int
    correction_digest: str
    correction: tuple[int, ...] = ()

    def sort_key(self) -> tuple[float, int, int, str]:
        return (self.negative_log_likelihood_or_weight,
                STAGE_PRIORITY.get(self.source_stage, 100), self.trial_id,
                self.correction_digest)


def correction_digest(correction: Sequence[int]) -> str:
    packed = bytearray((len(correction) + 7) // 8)
    for index, bit in enumerate(correction):
        packed[index // 8] |= int(bit) << (index % 8)
    return hashlib.sha256(bytes(packed)).hexdigest()


def logical_class_from_correction(
    correction: Sequence[int], logical_signatures: Sequence[Sequence[int]] | None
) -> int:
    if not logical_signatures:
        return 0
    logical_class = 0
    for index, signature in enumerate(logical_signatures):
        if len(signature) != len(correction):
            raise ValueError("logical signature width mismatch")
        parity = sum(int(bit) * int(error) for bit, error in zip(signature, correction)) & 1
        logical_class |= parity << index
    return logical_class


def make_candidate(
    graph: Graph,
    correction: Sequence[int],
    *,
    source_stage: str,
    trial_id: int = 0,
    syndrome: Sequence[int] | None = None,
    prior_llr: Sequence[float] | None = None,
    logical_signatures: Sequence[Sequence[int]] | None = None,
) -> Candidate:
    values = tuple(int(bit) for bit in correction)
    if len(values) != graph.num_variables or any(bit not in (0, 1) for bit in values):
        return Candidate(False, False, math.inf, 0, source_stage, trial_id, "", values)
    target = tuple(graph.syndrome if syndrome is None else syndrome)
    actual = compute_syndrome(graph, values)
    satisfied = actual == target
    if prior_llr is None:
        score = float(sum(values))
    else:
        if len(prior_llr) != len(values):
            raise ValueError("prior_llr width mismatch")
        # Constant-free negative log-likelihood: bit one pays positive LLR.
        score = float(sum(value * float(llr) for value, llr in zip(values, prior_llr)))
    return Candidate(satisfied, satisfied, score if satisfied else math.inf,
                     logical_class_from_correction(values, logical_signatures),
                     source_stage, trial_id, correction_digest(values), values)


def select_best(candidates: Iterable[Candidate]) -> Candidate | None:
    valid = [candidate for candidate in candidates
             if candidate.valid and candidate.syndrome_satisfied]
    return min(valid, key=Candidate.sort_key) if valid else None
