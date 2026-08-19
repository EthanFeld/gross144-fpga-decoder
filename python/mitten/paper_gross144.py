"""Exact Gross144 Stage-1 fixture used by the Mitten paper comparison.

The paper's Gross144 comparison cites Relay-BP's public bicycle-bivariate
Stim circuits.  Its first stage filters detectors to the initialization basis.
This module freezes that public input contract, then feeds the resulting
detector-error model into the project's fixed-point FPGA Stage-1 model.

Only detector bits reach ``PaperGross144Stage1FpgaAdapter.decode``.  Logical
observables remain in the Stim sampler and are intentionally available only to
the benchmark scorer after a correction has been selected.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .automorphism import AutomorphismSelection, AutomorphismTrial, select_decorrelated_group_trials
from .graph_model import Graph, GroupActionMetadata
from .gross144 import build_gross144
from .hybrid_warmup import DEFAULT_LLR_SCALE, WarmupConfig, quantize_llr
from .release_workflow import ReleaseWorkflowConfig, ReleaseWorkflowResult, run_release_workflow
from .telescope import Stage
from .unified_stage_controller import GraphType, StageBudget
from .wide_minsum import (
    WideMinSumBatchResult,
    WideMinSumConfig,
    run_wide_layered_minsum,
    run_wide_layered_minsum_batch,
)


PAPER_RELAY_URL = "https://github.com/trmue/relay"
# Last commit on Relay's main branch before the Mitten paper's cited
# 2026-07-07 access date.  Fixture blobs are checked below as well, so a later
# checkout is accepted only when it still has the cited bytes.
PAPER_RELAY_COMMIT = "19d7023d476248858fc01bdf087ce673feaa4ef4"
PAPER_FIXTURE_DIR = Path("tests/testdata/bicycle_bivariate")
DATA_QUBITS = 144
CHECKS = 936
ROUNDS = 12
LOGICALS = 12

_FIXTURE_SHA256 = {
    (0.001, "X"): "0d836686deaaf6169cbab132f185c1202bb6e00c7f9637d7cade878375fd4d85",
    (0.001, "Z"): "c1074cb8ee82fa9a4dc009f880180d6e1d7bb7fa76ed1d1a2a51f33dc9a6cb5b",
    (0.002, "X"): "70a8fac201a54ea244595f99e4fa9a35d561cac620b91df8dfbeaaeed3fadf06",
    (0.002, "Z"): "ba31f0a382c7abf9c98fbed04eb9c4423b48e0ca86107dbe4b9de53ebd760732",
}


@dataclass(frozen=True)
class WideMinSumRescueProfile:
    """One deterministic defer-only S1W retry hardware profile."""

    stage: str
    llr_scale: int
    message_magnitude_bits: int
    correction_shift: int
    max_iterations: int
    message_offset: int = 0
    check_schedule: str = "forward"
    schedule_stride: int = 1

    def validate(self) -> None:
        if not self.stage or self.llr_scale < 1:
            raise ValueError("invalid wide min-sum rescue profile")
        WideMinSumConfig(
            max_iterations=self.max_iterations,
            message_magnitude_bits=self.message_magnitude_bits,
            correction_shift=self.correction_shift,
            message_offset=self.message_offset,
            check_schedule=self.check_schedule,
            schedule_stride=self.schedule_stride,
        ).validate()


# Fixed C/D/E portfolio.  Every profile sees only a prior S1 syndrome failure;
# no logical observable or host decoder selects its result.  The schedules are
# ROM-counter operations (direction/parity/start offset), so they diversify
# layered fixed points without adding check-update cycles.  All three share one
# six-bit arithmetic image; only the schedule controls change between retries.
DEFAULT_WIDE_RESCUE_PROFILES = (
    WideMinSumRescueProfile(
        "S1WT_C", 3, 6, 4, 30, check_schedule="alternating",
    ),
    WideMinSumRescueProfile(
        "S1WT_D", 3, 6, 4, 20, check_schedule="alternating_reverse",
    ),
    WideMinSumRescueProfile(
        "S1WT_E", 3, 6, 4, 24,
        check_schedule="cyclic_alternating", schedule_stride=72,
    ),
)


@dataclass(frozen=True)
class PaperGross144Stage1Config:
    """Fixed-point settings shared with the FPGA Stage-1 datapath."""

    physical_error_rate: float
    warmup_iterations: int = 2
    minsum_iterations: int = 10
    llr_scale: int = DEFAULT_LLR_SCALE
    s1_algorithm: str = "wide_minsum"
    message_magnitude_bits: int = 5
    normalization_correction_shift: int = 3
    message_offset: int = 0
    check_schedule: str = "pair_alternating"
    schedule_stride: int = 1
    wide_rescue_profiles: tuple[WideMinSumRescueProfile, ...] = DEFAULT_WIDE_RESCUE_PROFILES
    enable_s1a: bool = False

    def validate(self) -> None:
        if self.physical_error_rate not in (0.001, 0.002):
            raise ValueError("the frozen paper fixture supports p=0.001 or p=0.002")
        if self.s1_algorithm not in ("legacy_hybrid", "wide_minsum"):
            raise ValueError("unknown paper Stage-1 algorithm")
        if self.s1_algorithm == "legacy_hybrid" and self.warmup_iterations != 2:
            raise ValueError("the release FPGA workflow requires two warm-up iterations")
        if self.minsum_iterations < 0 or self.llr_scale < 1:
            raise ValueError("invalid Stage-1 fixed-point configuration")
        if self.s1_algorithm == "wide_minsum":
            WideMinSumConfig(
                max_iterations=self.minsum_iterations,
                message_magnitude_bits=self.message_magnitude_bits,
                correction_shift=self.normalization_correction_shift,
                message_offset=self.message_offset,
                check_schedule=self.check_schedule,
                schedule_stride=self.schedule_stride,
            ).validate()
            if self.enable_s1a:
                raise ValueError("S1A is not yet defined for direct wide min-sum")
            for profile in self.wide_rescue_profiles:
                profile.validate()


@dataclass(frozen=True)
class PaperGross144Stage1Layout:
    """Compiled public paper fixture and its hardware-input graph."""

    basis: str
    physical_error_rate: float
    circuit: Any
    graph: Graph
    prior_llr: tuple[float, ...]
    logical_signatures: tuple[tuple[int, ...], ...]
    automorphisms: AutomorphismSelection
    fixture_path: Path
    fixture_sha256: str
    filtered_circuit_sha256: str
    detector_error_model_sha256: str
    selected_detector_indices: tuple[int, ...]

    @property
    def max_check_degree(self) -> int:
        return max(len(check.neighbors) for check in self.graph.checks)


@dataclass(frozen=True)
class PaperGross144Stage1DecodeResult:
    accepted: bool
    predicted_logicals: tuple[int, ...]
    correction: tuple[int, ...]
    final_stage: str
    check_updates: int
    reason: str
    workflow: ReleaseWorkflowResult | None = None


@dataclass(frozen=True)
class PaperGross144Stage1WideBatchResult:
    """S1W Monte-Carlo rows, including an optional low-prior rescue pass."""

    correction: np.ndarray
    success: np.ndarray
    iterations: np.ndarray
    work: np.ndarray
    final_stage: tuple[str, ...]


def _p_token(p: float) -> str:
    if p == 0.001:
        return "0.001"
    if p == 0.002:
        return "0.002"
    raise ValueError("the frozen paper fixture supports p=0.001 or p=0.002")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fixture_path(relay_root: Path, *, p: float, basis: str) -> Path:
    if basis not in ("X", "Z"):
        raise ValueError("basis must be X or Z")
    fixture_dir = relay_root / PAPER_FIXTURE_DIR
    token = _p_token(p)
    matches = sorted(fixture_dir.glob(
        f"*bicycle_bivariate_144_12_12_memory_{basis},distance=12,rounds=12,"
        f"error_rate={token},noise_model=uniform_circuit,basis=CX,A=x^3+y+y^2,B=y^3+x+x^2.stim"
    ))
    if len(matches) != 1:
        raise FileNotFoundError(
            "missing cited Relay Gross144 fixture; expected one file under "
            f"{fixture_dir} for p={token}, basis={basis}"
        )
    return matches[0]


def _filter_detectors_by_basis(circuit: Any, basis: str) -> tuple[Any, tuple[int, ...]]:
    """Relay-BP's public initialization-basis filter, preserved verbatim in behavior.

    The data-qubit set is explicit for this frozen [[144,12,12]] fixture.  This
    removes only detectors; it does not alter noisy operations or observable
    definitions, so logical scoring remains on the original physical process.
    """

    import stim

    if basis not in ("X", "Z"):
        raise ValueError("basis must be X or Z")
    pauli_error = "Z" if basis == "X" else "X"
    flattened = circuit.flattened()
    noiseless = flattened.without_noise()
    reference_detectors, _ = noiseless.compile_detector_sampler().sample(
        1, separate_observables=True,
    )
    sensitive = np.zeros(len(reference_detectors[0]), dtype=bool)
    to_test = list(range(DATA_QUBITS))
    data_qubits = set(to_test)
    instruction_index = 0
    while to_test:
        for qubit in to_test:
            injected = stim.Circuit()
            injected += noiseless
            injected.insert(
                instruction_index,
                stim.CircuitInstruction(f"{pauli_error}_ERROR", [qubit], [1.0]),
            )
            detectors, _ = injected.compile_detector_sampler().sample(
                1, separate_observables=True,
            )
            sensitive[np.where(reference_detectors[0] != detectors[0])] = True
        to_test = []
        for instruction in noiseless[instruction_index:]:
            instruction_index += 1
            if instruction.name.startswith("R") or instruction.name.startswith("M"):
                to_test = list(data_qubits)
                break

    filtered = stim.Circuit()
    detector_index = 0
    for instruction in flattened:
        if instruction.name == "DETECTOR":
            keep = bool(sensitive[detector_index])
            detector_index += 1
            if not keep:
                continue
        filtered.append(instruction)
    return filtered, tuple(int(index) for index in np.flatnonzero(sensitive))


def _dem_to_layout(*, circuit: Any, basis: str, p: float, fixture_path: Path,
                   fixture_sha256: str, selected_detector_indices: tuple[int, ...]) -> PaperGross144Stage1Layout:
    # This is the same no-argument Stim DEM conversion used by Relay-BP's
    # CheckMatrices fixture path after its basis filter.  Do not request a
    # graph-like decomposition or an approximate channel conversion here.
    dem = circuit.detector_error_model()
    if circuit.num_detectors != CHECKS or dem.num_detectors != CHECKS:
        raise ValueError("paper basis-filtered detector count drifted from 936")
    if circuit.num_observables != LOGICALS:
        raise ValueError("paper fixture observable count drifted from 12")

    check_neighbors: list[list[int]] = [[] for _ in range(CHECKS)]
    priors: list[float] = []
    logical_columns: list[list[int]] = [[] for _ in range(LOGICALS)]
    for instruction in dem.flattened():
        if instruction.type != "error":
            continue
        arguments = instruction.args_copy()
        if len(arguments) != 1 or not 0.0 < arguments[0] < 0.5:
            raise ValueError("paper DEM contains an unsupported error prior")
        variable = len(priors)
        probability = float(arguments[0])
        priors.append(math.log((1.0 - probability) / probability))
        detector_count = 0
        for target in instruction.targets_copy():
            if target.is_relative_detector_id():
                check_neighbors[target.val].append(variable)
                detector_count += 1
            elif target.is_logical_observable_id():
                logical_columns[target.val].append(variable)
            elif target.is_separator():
                raise ValueError("paper S1 DEM unexpectedly contains decomposed hyperedges")
            else:
                raise ValueError("paper S1 DEM contains an unsupported target type")
        if detector_count == 0:
            raise ValueError("paper S1 DEM contains a logical-only error variable")
    graph = Graph.from_neighbors(len(priors), check_neighbors)
    if graph.edge_count() != 30_672 or graph.num_variables != 8_784 or \
            max(len(check.neighbors) for check in graph.checks) != 35:
        raise ValueError("paper S1 graph topology drifted from the frozen fixture")
    logical_signatures = tuple(
        tuple(int(variable in columns) for variable in range(graph.num_variables))
        for columns in logical_columns
    )
    graph, automorphisms = _compile_paper_automorphisms(
        graph, prior_llr=tuple(priors), basis=basis,
    )
    return PaperGross144Stage1Layout(
        basis=basis,
        physical_error_rate=p,
        circuit=circuit,
        graph=graph,
        prior_llr=tuple(priors),
        logical_signatures=logical_signatures,
        automorphisms=automorphisms,
        fixture_path=fixture_path,
        fixture_sha256=fixture_sha256,
        filtered_circuit_sha256=_sha256_bytes(str(circuit).encode("utf-8")),
        detector_error_model_sha256=_sha256_bytes(str(dem).encode("utf-8")),
        selected_detector_indices=selected_detector_indices,
    )


def _compile_paper_automorphisms(
    graph: Graph, *, prior_llr: tuple[float, ...], basis: str,
) -> tuple[Graph, AutomorphismSelection]:
    """Lift Gross144's four declared spatial translations through paper DEM time."""

    error_type = "Z" if basis == "X" else "X"
    base_graph, _ = build_gross144().graph(error_type)
    base = select_decorrelated_group_trials(base_graph, max_iterations=8)
    if len(base.trials) != 4 or len(graph.checks) != 13 * 72:
        raise ValueError("paper Gross144 S1 automorphism contract drifted")

    masks = [0] * graph.num_variables
    for check in graph.checks:
        for variable in check.neighbors:
            masks[variable] |= 1 << check.id
    key_to_variable: dict[tuple[int, float], int] = {}
    for variable, mask in enumerate(masks):
        key = (mask, round(prior_llr[variable], 12))
        if key in key_to_variable:
            raise ValueError("paper S1 DEM has an ambiguous automorphism response")
        key_to_variable[key] = variable

    variable_actions: list[tuple[int, ...]] = []
    check_actions: list[tuple[int, ...]] = []
    trials: list[AutomorphismTrial] = []
    for trial in base.trials:
        if len(trial.check_permutation) != 72:
            raise ValueError("Gross144 base action has unexpected check width")
        check_action = tuple(
            time_index * 72 + int(trial.check_permutation[check_id])
            for time_index in range(13)
            for check_id in range(72)
        )
        variable_action: list[int] = []
        for variable, mask in enumerate(masks):
            mapped_mask = 0
            remaining = mask
            while remaining:
                low_bit = remaining & -remaining
                detector = low_bit.bit_length() - 1
                time_index, check_id = divmod(detector, 72)
                mapped_mask |= 1 << (time_index * 72 + int(trial.check_permutation[check_id]))
                remaining ^= low_bit
            try:
                mapped = key_to_variable[(mapped_mask, round(prior_llr[variable], 12))]
            except KeyError as exc:
                raise ValueError("paper S1 translation maps outside the DEM image") from exc
            variable_action.append(mapped)
        action = tuple(variable_action)
        if len(set(action)) != graph.num_variables or not graph.preserves_graph(action, check_action):
            raise ValueError("paper S1 translation failed graph-preservation validation")
        variable_actions.append(action)
        check_actions.append(check_action)
        trials.append(AutomorphismTrial(
            trial_id=trial.trial_id,
            variable_permutation=action,
            check_permutation=check_action,
            max_iterations=trial.max_iterations,
            schedule_id=trial.schedule_id,
        ))
    selection = AutomorphismSelection(
        trials=tuple(trials),
        candidate_count=base.candidate_count,
        coset_count=base.coset_count,
        short_cycle_supports=(),
        trapping_set_supports=(),
        minimum_pairwise_decorrelation=base.minimum_pairwise_decorrelation,
        pairwise_decorrelation=base.pairwise_decorrelation,
    )
    return replace(graph, group=GroupActionMetadata(
        variable_permutations=tuple(variable_actions), check_permutations=tuple(check_actions),
    )), selection


def load_paper_gross144_stage1_layout(
    relay_root: Path | str,
    *,
    p: float,
    basis: str,
) -> PaperGross144Stage1Layout:
    """Load and verify one cited Relay fixture, then compile its exact S1 image."""

    import stim

    root = Path(relay_root)
    path = _fixture_path(root, p=p, basis=basis)
    raw = path.read_bytes()
    digest = _sha256_bytes(raw)
    expected = _FIXTURE_SHA256[(p, basis)]
    if digest != expected:
        raise ValueError(
            f"Relay fixture digest mismatch for p={p}, basis={basis}: "
            f"expected {expected}, got {digest}"
        )
    circuit, selected = _filter_detectors_by_basis(stim.Circuit(raw.decode("utf-8")), basis)
    return _dem_to_layout(
        circuit=circuit, basis=basis, p=p, fixture_path=path, fixture_sha256=digest,
        selected_detector_indices=selected,
    )


def full_paper_dem_profile(relay_root: Path | str, *, p: float, basis: str) -> dict[str, int]:
    """Return immutable topology facts for the paper's full-correlated DEM.

    This is intentionally a profile, not an attempted downgrade of S2 into a
    binary small-degree image.  It makes the Stage-2 hardware gap explicit in
    the same fixture used for the LER measurement.
    """

    import stim

    root = Path(relay_root)
    path = _fixture_path(root, p=p, basis=basis)
    raw = path.read_bytes()
    if _sha256_bytes(raw) != _FIXTURE_SHA256[(p, basis)]:
        raise ValueError("Relay fixture digest mismatch while profiling full DEM")
    dem = stim.Circuit(raw.decode("utf-8")).detector_error_model()
    degrees = [0] * dem.num_detectors
    errors = 0
    edges = 0
    for instruction in dem.flattened():
        if instruction.type != "error":
            continue
        errors += 1
        for target in instruction.targets_copy():
            if target.is_relative_detector_id():
                degrees[target.val] += 1
                edges += 1
    return {
        "variables": errors,
        "checks": dem.num_detectors,
        "edges": edges,
        "max_check_degree": max(degrees),
    }


class PaperGross144Stage1FpgaAdapter:
    """Bit-accurate fixed-point FPGA Stage-1 model on the cited paper input.

    This deliberately implements only S1.  An S1 defer is surfaced to the
    caller, where the apples-to-apples LER harness counts it as an FPGA failure
    unless a later, separately-qualified FPGA stage is present.  It never
    substitutes a host decoder or samples a hidden physical-error vector.
    """

    def __init__(self, relay_root: Path | str, *,
                 config: PaperGross144Stage1Config):
        config.validate()
        self.config = config
        self.layouts = {
            basis: load_paper_gross144_stage1_layout(relay_root, p=config.physical_error_rate,
                                                       basis=basis)
            for basis in ("X", "Z")
        }

    @property
    def configuration_sha256(self) -> str:
        payload = (
            f"p={self.config.physical_error_rate};warmup={self.config.warmup_iterations};"
            f"minsum={self.config.minsum_iterations};scale={self.config.llr_scale};"
            f"algorithm={self.config.s1_algorithm};magbits={self.config.message_magnitude_bits};"
            f"normshift={self.config.normalization_correction_shift};"
            f"offset={self.config.message_offset};schedule={self.config.check_schedule}:"
            f"{self.config.schedule_stride};"
            f"rescue={'/'.join(f'{p.stage}:{p.llr_scale}:{p.message_magnitude_bits}:{p.correction_shift}:{p.max_iterations}:{p.message_offset}:{p.check_schedule}:{p.schedule_stride}' for p in self.config.wide_rescue_profiles)};"
            f"s1a={int(self.config.enable_s1a)};relay={PAPER_RELAY_COMMIT}"
        )
        return _sha256_bytes(payload.encode("utf-8"))

    def _workflow_config(self, layout: PaperGross144Stage1Layout) -> ReleaseWorkflowConfig:
        sweeps = self.config.warmup_iterations + self.config.minsum_iterations
        # S1A remains opt-in because the exact degree-35 streamed engine is
        # RTL-verified but is not yet integrated into the board top.
        budgets = (
            StageBudget(Stage.S1, sweeps * len(layout.graph.checks)),
            StageBudget(Stage.S1A, 4 * (2 + 8) * len(layout.graph.checks)
                        if self.config.enable_s1a else 0),
        )
        return ReleaseWorkflowConfig(
            graph_type=GraphType.STATIC,
            warmup=WarmupConfig(
                warmup_iterations=self.config.warmup_iterations,
                minsum_iterations=self.config.minsum_iterations,
                llr_scale=self.config.llr_scale,
            ),
            logical_signatures=layout.logical_signatures,
            stage_budgets=budgets,
            total_work_budget=sum(item.max_work for item in budgets),
        )

    def decode(self, detectors: Sequence[int], *, basis: str) -> PaperGross144Stage1DecodeResult:
        if basis not in self.layouts:
            raise ValueError("basis must be X or Z")
        layout = self.layouts[basis]
        syndrome = tuple(int(value) for value in detectors)
        if len(syndrome) != CHECKS or any(value not in (0, 1) for value in syndrome):
            raise ValueError("paper Stage-1 detector width/value mismatch")
        if not any(syndrome):
            return PaperGross144Stage1DecodeResult(
                True, (0,) * LOGICALS, (0,) * layout.graph.num_variables,
                "S0", 0, "zero detector syndrome",
            )
        graph = replace(layout.graph, syndrome=syndrome)
        if self.config.s1_algorithm == "wide_minsum":
            fixed_prior = tuple(
                quantize_llr(value, scale=self.config.llr_scale)
                for value in layout.prior_llr
            )
            decoded = run_wide_layered_minsum(
                graph, fixed_prior, syndrome=syndrome,
                config=WideMinSumConfig(
                    max_iterations=self.config.minsum_iterations,
                    message_magnitude_bits=self.config.message_magnitude_bits,
                    correction_shift=self.config.normalization_correction_shift,
                    message_offset=self.config.message_offset,
                    check_schedule=self.config.check_schedule,
                    schedule_stride=self.config.schedule_stride,
                ),
            )
            total_work = decoded.work
            final_stage = "S1W"
            for profile in self.config.wide_rescue_profiles:
                if decoded.success:
                    break
                rescue_prior = tuple(
                    quantize_llr(value, scale=profile.llr_scale)
                    for value in layout.prior_llr
                )
                decoded = run_wide_layered_minsum(
                    graph, rescue_prior, syndrome=syndrome,
                    config=WideMinSumConfig(
                        max_iterations=profile.max_iterations,
                        message_magnitude_bits=profile.message_magnitude_bits,
                        correction_shift=profile.correction_shift,
                        message_offset=profile.message_offset,
                        check_schedule=profile.check_schedule,
                        schedule_stride=profile.schedule_stride,
                    ),
                )
                total_work += decoded.work
                final_stage = profile.stage if decoded.success else "defer"
            if not decoded.success:
                return PaperGross144Stage1DecodeResult(
                    False, (0,) * LOGICALS, (0,) * layout.graph.num_variables,
                    "defer", total_work, "wide normalized min-sum did not satisfy syndrome",
                )
            correction = decoded.correction
            logicals = tuple(
                sum(bit * logical[index] for index, bit in enumerate(correction)) & 1
                for logical in layout.logical_signatures
            )
            return PaperGross144Stage1DecodeResult(
                True, logicals, correction, final_stage, total_work, "",
            )
        if not self.config.enable_s1a:
            graph = replace(graph, group=GroupActionMetadata())
        workflow = run_release_workflow(
            graph, layout.prior_llr, syndrome=syndrome, config=self._workflow_config(layout),
            automorphism_selection=layout.automorphisms if self.config.enable_s1a else None,
        )
        candidate = workflow.controller.selected_candidate
        if candidate is None:
            return PaperGross144Stage1DecodeResult(
                False, (0,) * LOGICALS, (0,) * layout.graph.num_variables,
                "defer", workflow.controller.total_work, workflow.controller.reason, workflow,
            )
        correction = tuple(int(value) for value in candidate.correction)
        logicals = tuple(
            sum(bit * logical[index] for index, bit in enumerate(correction)) & 1
            for logical in layout.logical_signatures
        )
        return PaperGross144Stage1DecodeResult(
            True, logicals, correction, candidate.source_stage,
            workflow.controller.total_work, "", workflow,
        )

    def decode_wide_batch(self, detectors: np.ndarray, *, basis: str) -> PaperGross144Stage1WideBatchResult:
        """Batch S1W benchmark helper; decoder sees detector bits only."""

        if self.config.s1_algorithm != "wide_minsum":
            raise ValueError("batched path is defined only for wide min-sum")
        if basis not in self.layouts:
            raise ValueError("basis must be X or Z")
        syndrome = np.asarray(detectors, dtype=np.uint8)
        if syndrome.ndim != 2 or syndrome.shape[1] != CHECKS or \
                np.any((syndrome != 0) & (syndrome != 1)):
            raise ValueError("paper Stage-1 batch detector width/value mismatch")
        layout = self.layouts[basis]
        fixed_prior = tuple(
            quantize_llr(value, scale=self.config.llr_scale)
            for value in layout.prior_llr
        )
        primary = run_wide_layered_minsum_batch(
            layout.graph, fixed_prior, syndrome,
            config=WideMinSumConfig(
                max_iterations=self.config.minsum_iterations,
                message_magnitude_bits=self.config.message_magnitude_bits,
                correction_shift=self.config.normalization_correction_shift,
                message_offset=self.config.message_offset,
                check_schedule=self.config.check_schedule,
                schedule_stride=self.config.schedule_stride,
            ),
        )
        correction = primary.correction.copy()
        success = primary.success.copy()
        iterations = primary.iterations.copy()
        work = primary.work.copy()
        final_stage = np.where(success, "S1W", "defer").astype(object)
        retry = np.flatnonzero(~success)
        for profile in self.config.wide_rescue_profiles:
            if not len(retry):
                break
            rescue_prior = tuple(
                quantize_llr(value, scale=profile.llr_scale)
                for value in layout.prior_llr
            )
            rescue = run_wide_layered_minsum_batch(
                layout.graph, rescue_prior, syndrome[retry],
                config=WideMinSumConfig(
                    max_iterations=profile.max_iterations,
                    message_magnitude_bits=profile.message_magnitude_bits,
                    correction_shift=profile.correction_shift,
                    message_offset=profile.message_offset,
                    check_schedule=profile.check_schedule,
                    schedule_stride=profile.schedule_stride,
                ),
            )
            correction[retry] = rescue.correction
            success[retry] = rescue.success
            iterations[retry] += rescue.iterations
            work[retry] += rescue.work
            final_stage[retry[rescue.success]] = profile.stage
            retry = retry[~rescue.success]
        return PaperGross144Stage1WideBatchResult(
            correction=correction,
            success=success,
            iterations=iterations,
            work=work,
            final_stage=tuple(str(value) for value in final_stage),
        )


def rtl_cycle_model(layout: PaperGross144Stage1Layout, *, sweeps: int,
                    banks: int, clock_hz: float) -> dict[str, float | int | str | bool]:
    """Report S1W cycle cost, separating primitive estimates from full RTL.

    The four-bank Gross144 production controller was audited with its compact
    X p=0.2% image: one complete update-plus-syndrome-replay sweep takes
    32,618 clock cycles.  The old expression counted only the check primitive
    and silently omitted the controller's synchronous template/RAM pipeline,
    record transitions, and replay FSM.  It was therefore a lower bound, not
    a decoder latency measurement.

    The audited number applies to the canonical 936-check, four-bank S1W
    layout, whose topology is independent of physical error rate.  Other
    configurations retain the primitive estimate but are explicitly labelled
    as such so callers cannot present it as board/RTL latency.
    """

    if sweeps < 1 or banks < 1 or clock_hz <= 0.0:
        raise ValueError("invalid RTL cycle model arguments")
    primitive_cycles_per_sweep = sum(
        2 * math.ceil(len(check.neighbors) / banks) + 2 for check in layout.graph.checks
    )
    canonical_four_bank = banks == 4 and len(layout.graph.checks) == 936 and \
        sorted(len(check.neighbors) for check in layout.graph.checks) == \
        [16] * 72 + [25] * 72 + [35] * 792
    if canonical_four_bank:
        cycles_per_sweep = 32_618
        model_kind = "rtl_audited_full_s1w_controller"
        cycle_exact = True
    else:
        cycles_per_sweep = primitive_cycles_per_sweep
        model_kind = "check_engine_lower_bound"
        cycle_exact = False
    cycles_per_window = cycles_per_sweep * sweeps
    seconds_per_window = cycles_per_window / clock_hz
    return {
        "banks": banks,
        "clock_hz": clock_hz,
        "model_kind": model_kind,
        "cycle_exact": cycle_exact,
        "primitive_cycles_per_sweep": primitive_cycles_per_sweep,
        "cycles_per_sweep": cycles_per_sweep,
        "cycles_per_window": cycles_per_window,
        "seconds_per_window": seconds_per_window,
        "microseconds_per_syndrome_round": seconds_per_window * 1e6 / ROUNDS,
        "syndrome_rounds_per_second": ROUNDS / seconds_per_window,
    }
