"""N04 GARI Stage-2 integration, defer policy, and cache accounting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .candidate_score import Candidate, make_candidate
from .gari import CHECK_TYPES, GariConfig, GariGraph, GariResult, run_gari
from .telescope import Stage, StageResult, StageStatus, TelescopeConfig, TelescopeTrace, run_telescope


@dataclass(frozen=True)
class Stage2Profile:
    """Residency profile selected by the release mode."""

    name: str = "STATIC_RELEASE"
    required_sections: tuple[str, ...] = CHECK_TYPES
    cold_load_cycles: int = 64
    warm_load_cycles: int = 8

    def validate(self) -> None:
        if not self.name:
            raise ValueError("Stage-2 profile name must be non-empty")
        if not self.required_sections or any(
            section not in CHECK_TYPES for section in self.required_sections
        ):
            raise ValueError("Stage-2 profile contains an unsupported section")
        if len(set(self.required_sections)) != len(self.required_sections):
            raise ValueError("Stage-2 profile sections must be unique")
        if self.cold_load_cycles < 0 or self.warm_load_cycles < 0:
            raise ValueError("Stage-2 load costs must be non-negative")


@dataclass
class Stage2Cache:
    """One resident graph image; a different profile/image causes a cold load."""

    resident_key: str | None = None
    cold_loads: int = 0
    warm_hits: int = 0

    def load(self, key: str) -> bool:
        warm = self.resident_key == key
        if warm:
            self.warm_hits += 1
        else:
            self.cold_loads += 1
            self.resident_key = key
        return warm


@dataclass(frozen=True)
class Stage2IntegrationConfig:
    profile: Stage2Profile = field(default_factory=Stage2Profile)
    max_iterations: int = 2
    auxiliary_parallelism: int = 2
    page_size: int = 0
    trial_id: int = 0
    logical_signatures: tuple[tuple[int, ...], ...] = ()

    def validate(self) -> None:
        self.profile.validate()
        if self.max_iterations < 0:
            raise ValueError("Stage-2 max_iterations must be non-negative")
        if self.auxiliary_parallelism < 1 or self.page_size < 0:
            raise ValueError("invalid Stage-2 schedule configuration")
        if self.trial_id < 0:
            raise ValueError("Stage-2 trial_id must be non-negative")


@dataclass(frozen=True)
class Stage2IntegrationResult:
    status: StageStatus
    candidate: Candidate | None
    stage2_ran: bool
    cache_hit: bool
    loaded_sections: tuple[str, ...]
    load_cycles: int
    decode_cycles: int
    total_cycles: int
    checks_processed: int
    page_fetches: int
    gari_result: GariResult | None = None
    reason: str = ""


def run_stage2_integration(
    gari: GariGraph,
    prior_llr: Sequence[int],
    *,
    stage1_status: StageStatus,
    stage1_candidate: Candidate | None = None,
    syndrome: Sequence[int] | None = None,
    config: Stage2IntegrationConfig | None = None,
    cache: Stage2Cache | None = None,
) -> Stage2IntegrationResult:
    """Run GARI only after defer, inverse-map, and B07-validate the result."""

    config = config or Stage2IntegrationConfig()
    try:
        config.validate()
    except ValueError as exc:
        return _internal(str(exc), stage2_ran=False)
    if stage1_status == StageStatus.SUCCESS:
        if stage1_candidate is None or not (
            stage1_candidate.valid and stage1_candidate.syndrome_satisfied
        ):
            return _internal("Stage-1 reported invalid success", stage2_ran=False)
        return Stage2IntegrationResult(
            StageStatus.SUCCESS, stage1_candidate, False, False, (), 0, 0, 0, 0, 0,
            reason="Stage-1 success short-circuit",
        )
    if stage1_status == StageStatus.FAIL_INTERNAL:
        return _internal("Stage-1 internal failure", stage2_ran=False)
    if stage1_status != StageStatus.DEFER:
        return _internal("unsupported Stage-1 status", stage2_ran=False)

    try:
        loaded_sections = _loaded_sections(gari, config.profile)
        key = _residency_key(gari, config.profile)
        cache = cache or Stage2Cache()
        cache_hit = cache.load(key)
        load_cycles = config.profile.warm_load_cycles if cache_hit \
            else config.profile.cold_load_cycles
        result = run_gari(
            gari,
            prior_llr,
            syndrome=syndrome,
            config=GariConfig(
                main_schedule="serial",
                max_iterations=config.max_iterations,
                auxiliary_parallelism=config.auxiliary_parallelism,
                page_size=config.page_size,
            ),
        )
    except (AssertionError, TypeError, ValueError, KeyError) as exc:
        return _internal(str(exc), stage2_ran=True)

    total_cycles = load_cycles + result.cycle_count
    if result.success and result.original_syndrome is not None and gari.original_graph is not None and \
            result.original_syndrome != gari.original_graph.syndrome:
        return Stage2IntegrationResult(
            StageStatus.FAIL_INTERNAL, None, True, cache_hit, loaded_sections,
            load_cycles, result.cycle_count, total_cycles, result.checks_processed,
            result.page_fetches, result, "inverse correction failed original syndrome",
        )
    if not result.success:
        return Stage2IntegrationResult(
            StageStatus.DEFER, None, True, cache_hit, loaded_sections,
            load_cycles, result.cycle_count, total_cycles, result.checks_processed,
            result.page_fetches, result, "GARI produced no accepted candidate",
        )

    target_graph = gari.original_graph or gari.to_graph()
    correction = result.original_correction if gari.original_graph is not None \
        else gari.inverse_correction(result.correction)
    candidate = make_candidate(
        target_graph,
        correction,
        source_stage="S2",
        trial_id=config.trial_id,
        syndrome=target_graph.syndrome,
        logical_signatures=config.logical_signatures or None,
    )
    if not (candidate.valid and candidate.syndrome_satisfied):
        return Stage2IntegrationResult(
            StageStatus.FAIL_INTERNAL, None, True, cache_hit, loaded_sections,
            load_cycles, result.cycle_count, total_cycles, result.checks_processed,
            result.page_fetches, result, "B07 rejected inverse-mapped GARI candidate",
        )
    return Stage2IntegrationResult(
        StageStatus.SUCCESS, candidate, True, cache_hit, loaded_sections,
        load_cycles, result.cycle_count, total_cycles, result.checks_processed,
        result.page_fetches, result,
    )


def make_stage2_adapter(
    gari: GariGraph,
    prior_llr: Sequence[int],
    *,
    config: Stage2IntegrationConfig | None = None,
    cache: Stage2Cache | None = None,
) -> Callable[[object, TelescopeConfig], StageResult]:
    """Create a B08 adapter; B08 calls it only after earlier stages defer."""

    def adapter(_graph, _telescope_config: TelescopeConfig) -> StageResult:
        result = run_stage2_integration(
            gari, prior_llr, stage1_status=StageStatus.DEFER,
            config=config, cache=cache,
        )
        return StageResult(result.status, result.candidate, result.total_cycles, result.reason)

    return adapter


def run_telescope_with_stage2(
    graph,
    gari: GariGraph,
    prior_llr: Sequence[int],
    *,
    stage1_adapter,
    stage1a_adapter=None,
    config: Stage2IntegrationConfig | None = None,
    cache: Stage2Cache | None = None,
    telescope_config: TelescopeConfig | None = None,
) -> TelescopeTrace:
    """Run the B08 trace with N04 occupying S2 and preserving short-circuiting."""

    adapters = {Stage.S1: stage1_adapter, Stage.S2: make_stage2_adapter(
        gari, prior_llr, config=config, cache=cache,
    )}
    if stage1a_adapter is not None:
        adapters[Stage.S1A] = stage1a_adapter
    return run_telescope(
        graph, config=telescope_config, adapters=adapters, zero_syndrome=False,
    )


def _loaded_sections(gari: GariGraph, profile: Stage2Profile) -> tuple[str, ...]:
    present = tuple(section for section in CHECK_TYPES
                    if any(check.check_type == section for check in gari.checks))
    missing = tuple(section for section in present
                    if section not in profile.required_sections)
    if missing:
        raise ValueError(f"active GARI sections omitted by profile: {missing}")
    return tuple(section for section in profile.required_sections if section in present)


def _residency_key(gari: GariGraph, profile: Stage2Profile) -> str:
    payload = {"source": gari.source_model, "profile": profile.name,
               "sections": profile.required_sections}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _internal(reason: str, *, stage2_ran: bool) -> Stage2IntegrationResult:
    return Stage2IntegrationResult(
        StageStatus.FAIL_INTERNAL, None, stage2_ran, False, (), 0, 0, 0, 0, 0,
        reason=reason,
    )
