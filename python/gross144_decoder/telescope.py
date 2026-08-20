"""B08 deterministic decoder telescope controller."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from .candidate_score import Candidate, select_best
from .graph_model import Graph


class Stage(str, Enum):
    S0 = "S0"
    S1 = "S1"
    S1A = "S1A"
    S2 = "S2"
    S2R = "S2R"
    HOST = "HOST"


class StageStatus(str, Enum):
    SUCCESS = "SUCCESS"
    DEFER = "DEFER"
    FAIL_INTERNAL = "FAIL_INTERNAL"


@dataclass(frozen=True)
class StageResult:
    status: StageStatus
    candidate: Candidate | None = None
    work: int = 0
    reason: str = ""


@dataclass(frozen=True)
class TelescopeConfig:
    warmup_iterations: int = 2
    minsum_sweeps: int = 10
    automorphism_trials: int = 4
    relay_legs: int = 4
    total_work_budget: int = 100_000


@dataclass(frozen=True)
class TelescopeTrace:
    status: StageStatus
    selected_stage: Stage | None
    selected_candidate: Candidate | None
    stages: tuple[tuple[Stage, StageResult], ...]
    work: int


StageAdapter = Callable[[Graph, TelescopeConfig], StageResult]


def run_telescope(
    graph: Graph,
    *,
    config: TelescopeConfig | None = None,
    adapters: Mapping[Stage, StageAdapter] | None = None,
    zero_syndrome: bool | None = None,
) -> TelescopeTrace:
    config = config or TelescopeConfig()
    if config.warmup_iterations != 2:
        raise ValueError("release telescope requires exactly two warm-up iterations")
    if config.automorphism_trials != 4:
        raise ValueError("release telescope requires four automorphism trials")
    adapters = adapters or {}
    actual_zero = all(bit == 0 for bit in graph.syndrome) if zero_syndrome is None else zero_syndrome
    trace: list[tuple[Stage, StageResult]] = []
    work = 0
    if actual_zero:
        result = StageResult(StageStatus.SUCCESS, work=0, reason="zero syndrome")
        trace.append((Stage.S0, result))
        return TelescopeTrace(StageStatus.SUCCESS, Stage.S0, None, tuple(trace), 0)

    for stage in (Stage.S1, Stage.S1A, Stage.S2, Stage.S2R, Stage.HOST):
        adapter = adapters.get(stage)
        if adapter is None:
            result = StageResult(StageStatus.DEFER, work=0, reason="adapter absent")
        else:
            result = adapter(graph, config)
        work += result.work
        trace.append((stage, result))
        if work > config.total_work_budget:
            fail = StageResult(StageStatus.FAIL_INTERNAL, work=0, reason="work budget exceeded")
            trace.append((stage, fail))
            return TelescopeTrace(StageStatus.FAIL_INTERNAL, None, None, tuple(trace), work)
        if result.status == StageStatus.FAIL_INTERNAL:
            return TelescopeTrace(StageStatus.FAIL_INTERNAL, None, None, tuple(trace), work)
        if result.status == StageStatus.SUCCESS:
            if result.candidate is None or not result.candidate.syndrome_satisfied:
                fail = StageResult(StageStatus.FAIL_INTERNAL, reason="invalid success candidate")
                trace.append((stage, fail))
                return TelescopeTrace(StageStatus.FAIL_INTERNAL, None, None, tuple(trace), work)
            return TelescopeTrace(StageStatus.SUCCESS, stage, result.candidate,
                                  tuple(trace), work)
    return TelescopeTrace(StageStatus.DEFER, None, None, tuple(trace), work)
