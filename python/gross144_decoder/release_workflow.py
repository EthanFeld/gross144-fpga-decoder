"""Canonical software release workflow for the fast decoder stages."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Sequence

from .automorphism import (
    AutomorphismConfig,
    AutomorphismResult,
    AutomorphismSelection,
    run_automorphism_ensemble,
    select_decorrelated_group_trials,
)
from .candidate_score import make_candidate
from .gari import GariGraph
from .graph_model import Graph
from .hybrid_warmup import HybridDecodeResult, WarmupConfig, quantize_llr, run_hybrid
from .minsum_reference import FixedDecodeResult
from .relay_reference import RelayConfig, RelayResult, RelayStatus, run_relay, save_checkpoint
from .stage2_integration import (
    Stage2Cache,
    Stage2IntegrationConfig,
    Stage2IntegrationResult,
    run_stage2_integration,
)
from .telescope import Stage, StageAdapter, StageResult, StageStatus, TelescopeConfig
from .unified_stage_controller import (
    DEFAULT_STAGE_BUDGETS,
    GraphType,
    StageBudget,
    UnifiedStageControllerConfig,
    UnifiedStageControllerResult,
    run_unified_stage_controller,
)


@dataclass(frozen=True)
class ReleaseWorkflowConfig:
    """Frozen configuration for the single production software path."""

    graph_type: GraphType = GraphType.STATIC
    warmup: WarmupConfig = field(default_factory=WarmupConfig)
    automorphism_iterations: int = 8
    automorphism_prior_perturbations: tuple[tuple[float, ...], ...] = ()
    stage2: Stage2IntegrationConfig = field(default_factory=Stage2IntegrationConfig)
    relay: RelayConfig | None = None
    logical_signatures: tuple[tuple[int, ...], ...] = ()
    stage_budgets: tuple[StageBudget, ...] = DEFAULT_STAGE_BUDGETS
    total_work_budget: int = 100_000

    def validate(self) -> None:
        if not isinstance(self.graph_type, GraphType):
            raise ValueError("unsupported release graph type")
        self.warmup.validate()
        if self.warmup.warmup_iterations != 2:
            raise ValueError("release workflow requires two warm-up iterations")
        if self.automorphism_iterations < 0:
            raise ValueError("automorphism iterations must be non-negative")
        if self.automorphism_prior_perturbations and \
                len(self.automorphism_prior_perturbations) != 4:
            raise ValueError("release workflow requires four automorphism perturbations")
        self.stage2.validate()
        if self.relay is not None and len(self.relay.legs) != 4:
            raise ValueError("release workflow requires exactly four Relay legs")
        if self.total_work_budget < 0:
            raise ValueError("total work budget must be non-negative")

    def controller_config(self) -> UnifiedStageControllerConfig:
        return UnifiedStageControllerConfig(
            graph_type=self.graph_type,
            stage_budgets=self.stage_budgets,
            total_work_budget=self.total_work_budget,
            telescope=TelescopeConfig(
                warmup_iterations=self.warmup.warmup_iterations,
                minsum_sweeps=self.warmup.minsum_iterations,
                automorphism_trials=4,
                relay_legs=4,
                total_work_budget=self.total_work_budget,
            ),
        )


@dataclass(frozen=True)
class ReleaseWorkflowResult:
    """One trace plus the concrete fast-stage results that produced it."""

    controller: UnifiedStageControllerResult
    hybrid: HybridDecodeResult | None
    automorphism_selection: AutomorphismSelection | None
    automorphism: AutomorphismResult | None
    stage2: Stage2IntegrationResult | None
    relay: RelayResult | None


@dataclass
class _WorkflowState:
    hybrid: HybridDecodeResult | None = None
    automorphism_selection: AutomorphismSelection | None = None
    automorphism: AutomorphismResult | None = None
    stage2: Stage2IntegrationResult | None = None
    relay: RelayResult | None = None


def run_release_workflow(
    graph: Graph,
    prior_llr: Sequence[float | int],
    *,
    syndrome: Sequence[int] | None = None,
    config: ReleaseWorkflowConfig | None = None,
    gari: GariGraph | None = None,
    stage2_cache: Stage2Cache | None = None,
    host_adapter: StageAdapter | None = None,
    automorphism_selection: AutomorphismSelection | None = None,
) -> ReleaseWorkflowResult:
    """Run S0/S1/S1A/S2/S2R/HOST through the one release controller.

    S1 uses fixed-point two-warm-up compressed min-sum; S1A receives only
    compiler-declared, maximin-decorrelated group actions; S2 shares the
    resident GARI cache; and S2R restores one Relay checkpoint.  Later stages
    execute only after an earlier stage explicitly defers.
    """

    config = config or ReleaseWorkflowConfig()
    config.validate()
    target = tuple(graph.syndrome if syndrome is None else (int(bit) for bit in syndrome))
    if len(target) != len(graph.checks) or any(bit not in (0, 1) for bit in target):
        raise ValueError("syndrome must contain one binary bit per check")
    float_prior = tuple(float(value) for value in prior_llr)
    if len(float_prior) != graph.num_variables:
        raise ValueError("prior width mismatch")
    active_graph = graph if target == graph.syndrome else replace(graph, syndrome=target)
    fixed_prior = tuple(quantize_llr(value, scale=config.warmup.llr_scale)
                        for value in float_prior)
    state = _WorkflowState()
    adapters: dict[Stage, StageAdapter] = {}

    def run_s1(_graph: Graph, _telescope: TelescopeConfig) -> StageResult:
        state.hybrid = run_hybrid(
            active_graph, fixed_prior, syndrome=target, config=config.warmup,
        )
        candidate = make_candidate(
            active_graph, state.hybrid.minsum.correction,
            source_stage="S1", syndrome=target, prior_llr=float_prior,
            logical_signatures=config.logical_signatures or None,
        )
        if state.hybrid.minsum.success and candidate.valid and candidate.syndrome_satisfied:
            return StageResult(StageStatus.SUCCESS, candidate, state.hybrid.total_work)
        return StageResult(StageStatus.DEFER, work=state.hybrid.total_work,
                           reason="compressed Stage-1 produced no accepted candidate")

    def run_s1a(_graph: Graph, _telescope: TelescopeConfig) -> StageResult:
        if not active_graph.group.variable_permutations:
            return StageResult(StageStatus.DEFER, reason="no declared group actions")
        if automorphism_selection is not None:
            state.automorphism_selection = automorphism_selection
            if len(state.automorphism_selection.trials) != 4:
                return StageResult(StageStatus.FAIL_INTERNAL,
                                   reason="precompiled group selection must contain four trials")
        else:
            try:
                state.automorphism_selection = select_decorrelated_group_trials(
                    active_graph,
                    max_iterations=config.automorphism_iterations,
                    prior_perturbations=(config.automorphism_prior_perturbations or None),
                )
            except ValueError as exc:
                return StageResult(StageStatus.FAIL_INTERNAL,
                                   reason=f"group trial compilation failed: {exc}")
        def fpga_automorphism_decoder(
            transformed: Graph,
            transformed_prior: Sequence[float],
            *,
            syndrome: Sequence[int],
            max_iterations: int,
        ) -> FixedDecodeResult:
            """Run the same fixed-point S1 datapath after a group transform.

            Automorphism trials used to default to the floating-point
            reference.  The production workflow must instead retain the two
            fixed box-plus warm-up passes and the compressed fixed min-sum
            representation used by the FPGA baseline.
            """

            trial_prior = tuple(
                quantize_llr(value, scale=config.warmup.llr_scale)
                for value in transformed_prior
            )
            trial_config = replace(config.warmup, minsum_iterations=max_iterations)
            decoded = run_hybrid(
                transformed, trial_prior, syndrome=syndrome, config=trial_config,
            )
            minsum = decoded.minsum
            return FixedDecodeResult(
                minsum.correction, minsum.posterior, minsum.syndrome,
                minsum.success,
                config.warmup.warmup_iterations + minsum.iterations,
                (),
            )

        state.automorphism = run_automorphism_ensemble(
            active_graph, float_prior, syndrome=target,
            trials=state.automorphism_selection.trials,
            config=AutomorphismConfig(trials=4),
            logical_signatures=config.logical_signatures or None,
            decoder=fpga_automorphism_decoder,
        )
        if state.automorphism.selected_candidate is not None:
            return StageResult(StageStatus.SUCCESS, state.automorphism.selected_candidate,
                               state.automorphism.total_work)
        return StageResult(StageStatus.DEFER, work=state.automorphism.total_work,
                           reason="four group-selected automorphism trials deferred")

    adapters[Stage.S1] = run_s1
    adapters[Stage.S1A] = run_s1a

    if config.graph_type in (GraphType.CIRCUIT, GraphType.STATIC_RECOVERY):
        def run_s2(_graph: Graph, _telescope: TelescopeConfig) -> StageResult:
            if gari is None:
                return StageResult(StageStatus.DEFER, reason="GARI image unavailable")
            state.stage2 = run_stage2_integration(
                gari, fixed_prior, stage1_status=StageStatus.DEFER,
                syndrome=target, config=config.stage2, cache=stage2_cache,
            )
            return StageResult(state.stage2.status, state.stage2.candidate,
                               state.stage2.total_cycles, state.stage2.reason)

        def run_s2r(_graph: Graph, _telescope: TelescopeConfig) -> StageResult:
            if config.relay is None:
                return StageResult(StageStatus.DEFER, reason="Relay configuration unavailable")
            state.relay = run_relay(
                active_graph, save_checkpoint(fixed_prior, target, state_tag="release"),
                config.relay, logical_signatures=config.logical_signatures or None,
            )
            if state.relay.status == RelayStatus.SUCCESS and state.relay.selected_candidate is not None:
                return StageResult(StageStatus.SUCCESS, state.relay.selected_candidate,
                                   state.relay.total_work)
            return StageResult(StageStatus.DEFER, work=state.relay.total_work,
                               reason=f"Relay deferred: {state.relay.status.value}")

        adapters[Stage.S2] = run_s2
        adapters[Stage.S2R] = run_s2r
        if config.graph_type == GraphType.CIRCUIT and host_adapter is not None:
            adapters[Stage.HOST] = host_adapter

    controller = run_unified_stage_controller(
        active_graph, config=config.controller_config(), adapters=adapters,
    )
    return ReleaseWorkflowResult(
        controller, state.hybrid, state.automorphism_selection,
        state.automorphism, state.stage2, state.relay,
    )
