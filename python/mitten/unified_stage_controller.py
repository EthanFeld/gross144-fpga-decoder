"""Q01 unified telescope stage FSM with budgets and structured outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from .candidate_score import Candidate
from .graph_model import Graph
from .telescope import Stage, StageAdapter, StageResult, StageStatus, TelescopeConfig


class GraphType(str, Enum):
    STATIC = "STATIC"
    # A block-code graph that uses the normal FPGA recovery tail after the
    # fast static stages defer.  This is deliberately distinct from CIRCUIT:
    # it has no detector-time semantics and never exposes a HOST stage.
    STATIC_RECOVERY = "STATIC_RECOVERY"
    CIRCUIT = "CIRCUIT"


@dataclass(frozen=True)
class StageBudget:
    stage: Stage
    max_work: int

    def validate(self) -> None:
        if self.max_work < 0:
            raise ValueError(f"negative budget for {self.stage.value}")


DEFAULT_STAGE_BUDGETS = (
    StageBudget(Stage.S1, 10_000),
    StageBudget(Stage.S1A, 10_000),
    StageBudget(Stage.S2, 100_000),
    StageBudget(Stage.S2R, 100_000),
    StageBudget(Stage.HOST, 10_000),
)


@dataclass(frozen=True)
class UnifiedStageControllerConfig:
    graph_type: GraphType = GraphType.STATIC
    stage_budgets: tuple[StageBudget, ...] = DEFAULT_STAGE_BUDGETS
    total_work_budget: int = 100_000
    telescope: TelescopeConfig = field(default_factory=TelescopeConfig)

    def validate(self) -> None:
        if not isinstance(self.graph_type, GraphType):
            raise ValueError("unsupported graph type")
        if self.total_work_budget < 0:
            raise ValueError("total work budget must be non-negative")
        if self.telescope.warmup_iterations != 2:
            raise ValueError("release controller requires two warm-up iterations")
        if self.telescope.automorphism_trials != 4:
            raise ValueError("release controller requires four automorphism trials")
        if len({budget.stage for budget in self.stage_budgets}) != len(self.stage_budgets):
            raise ValueError("duplicate stage budget")
        for budget in self.stage_budgets:
            budget.validate()


@dataclass(frozen=True)
class StageExecution:
    stage: Stage
    status: StageStatus
    reported_work: int
    budget: int
    timed_out: bool = False
    internal_error: bool = False
    candidate: Candidate | None = None
    reason: str = ""


@dataclass(frozen=True)
class UnifiedStageControllerResult:
    graph_type: GraphType
    status: StageStatus
    selected_stage: Stage | None
    selected_candidate: Candidate | None
    stages: tuple[StageExecution, ...]
    total_work: int
    timeout_stage: Stage | None = None
    internal_error_stage: Stage | None = None
    reason: str = ""


def run_unified_stage_controller(
    graph: Graph,
    *,
    config: UnifiedStageControllerConfig | None = None,
    adapters: Mapping[Stage, StageAdapter] | None = None,
    zero_syndrome: bool | None = None,
) -> UnifiedStageControllerResult:
    """Execute the one-place Q01 stage order and return an immutable trace."""

    config = config or UnifiedStageControllerConfig()
    config.validate()
    adapters = adapters or {}
    budgets = {budget.stage: budget.max_work for budget in config.stage_budgets}
    order = _stage_order(config.graph_type)
    records: list[StageExecution] = []
    total_work = 0

    actual_zero = all(bit == 0 for bit in graph.syndrome) if zero_syndrome is None else zero_syndrome
    if actual_zero:
        return UnifiedStageControllerResult(
            config.graph_type, StageStatus.SUCCESS, Stage.S0, None,
            (StageExecution(Stage.S0, StageStatus.SUCCESS, 0, 0, reason="zero syndrome"),), 0,
        )

    for stage in order:
        budget = budgets.get(stage)
        if budget is None:
            return UnifiedStageControllerResult(
                config.graph_type, StageStatus.FAIL_INTERNAL, None, None,
                tuple(records), total_work, internal_error_stage=stage,
                reason=f"missing budget for {stage.value}",
            )
        adapter = adapters.get(stage)
        if adapter is None:
            result = StageResult(StageStatus.DEFER, reason="adapter absent")
        else:
            try:
                result = adapter(graph, config.telescope)
            except Exception as exc:  # stage boundary converts faults to FAIL_INTERNAL
                record = StageExecution(
                    stage, StageStatus.FAIL_INTERNAL, 0, budget,
                    internal_error=True, reason=f"adapter exception: {exc}",
                )
                records.append(record)
                return UnifiedStageControllerResult(
                    config.graph_type, StageStatus.FAIL_INTERNAL, None, None,
                    tuple(records), total_work, internal_error_stage=stage,
                    reason=record.reason,
                )

        if not isinstance(result, StageResult) or result.work < 0:
            record = StageExecution(
                stage, StageStatus.FAIL_INTERNAL, 0, budget,
                internal_error=True, reason="malformed stage result",
            )
            records.append(record)
            return UnifiedStageControllerResult(
                config.graph_type, StageStatus.FAIL_INTERNAL, None, None,
                tuple(records), total_work, internal_error_stage=stage,
                reason=record.reason,
            )
        total_work += result.work
        if result.work > budget or total_work > config.total_work_budget:
            reason = (f"{stage.value} stage budget exceeded" if result.work > budget
                      else "total work budget exceeded")
            records.append(StageExecution(
                stage, StageStatus.FAIL_INTERNAL, result.work, budget,
                timed_out=True, candidate=None, reason=reason,
            ))
            return UnifiedStageControllerResult(
                config.graph_type, StageStatus.FAIL_INTERNAL, None, None,
                tuple(records), total_work, timeout_stage=stage, reason=reason,
            )

        if result.status == StageStatus.FAIL_INTERNAL:
            record = StageExecution(
                stage, StageStatus.FAIL_INTERNAL, result.work, budget,
                internal_error=True, reason=result.reason,
            )
            records.append(record)
            return UnifiedStageControllerResult(
                config.graph_type, StageStatus.FAIL_INTERNAL, None, None,
                tuple(records), total_work, internal_error_stage=stage,
                reason=result.reason,
            )
        if result.status == StageStatus.SUCCESS:
            if result.candidate is None or not (
                result.candidate.valid and result.candidate.syndrome_satisfied
            ):
                reason = "stage returned invalid success candidate"
                records.append(StageExecution(
                    stage, StageStatus.FAIL_INTERNAL, result.work, budget,
                    internal_error=True, reason=reason,
                ))
                return UnifiedStageControllerResult(
                    config.graph_type, StageStatus.FAIL_INTERNAL, None, None,
                    tuple(records), total_work, internal_error_stage=stage,
                    reason=reason,
                )
            # Once accepted, the candidate is final and no later stage runs.
            records.append(StageExecution(
                stage, StageStatus.SUCCESS, result.work, budget,
                candidate=result.candidate, reason=result.reason,
            ))
            return UnifiedStageControllerResult(
                config.graph_type, StageStatus.SUCCESS, stage, result.candidate,
                tuple(records), total_work,
            )
        if result.status != StageStatus.DEFER:
            reason = "unsupported stage status"
            records.append(StageExecution(
                stage, StageStatus.FAIL_INTERNAL, result.work, budget,
                internal_error=True, reason=reason,
            ))
            return UnifiedStageControllerResult(
                config.graph_type, StageStatus.FAIL_INTERNAL, None, None,
                tuple(records), total_work, internal_error_stage=stage,
                reason=reason,
            )
        records.append(StageExecution(
            stage, StageStatus.DEFER, result.work, budget, reason=result.reason,
        ))

    if config.graph_type == GraphType.STATIC:
        reason = "static path terminated after S1A"
    elif config.graph_type == GraphType.STATIC_RECOVERY:
        reason = "all static recovery stages deferred"
    else:
        reason = "all circuit stages deferred"
    return UnifiedStageControllerResult(
        config.graph_type, StageStatus.DEFER, None, None,
        tuple(records), total_work, reason=reason,
    )


def _stage_order(graph_type: GraphType) -> tuple[Stage, ...]:
    if graph_type == GraphType.STATIC:
        return (Stage.S1, Stage.S1A)
    if graph_type == GraphType.STATIC_RECOVERY:
        return (Stage.S1, Stage.S1A, Stage.S2, Stage.S2R)
    if graph_type == GraphType.CIRCUIT:
        return (Stage.S1, Stage.S1A, Stage.S2, Stage.S2R, Stage.HOST)
    raise ValueError("unsupported graph type")
