"""Frozen 12-round circuit-memory path for the Gross ``[[144,12,12]]`` code.

This is an open, explicitly specified memory experiment.  It keeps ideal
encoded-state preparation outside the noise boundary, applies the FPGA
depolarizing model only during syndrome extraction/final readout, and decodes
only detector bits.  The temporal decoder is a lift of the existing fixed
point S1 -> S1A -> GARI -> Relay workflow; it is not a matching/OSD fallback.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Any, Sequence

import numpy as np

from .automorphism import (
    AutomorphismSelection,
    AutomorphismTrial,
    select_decorrelated_group_trials,
)
from .gari import GariGraph
from .graph_model import Graph, GroupActionMetadata
from .gross144 import (
    GROSS144_PARAMETERS,
    Gross144Code,
    _gross144_relay_perturbations,
    _workflow_iterations,
    build_gross144,
)
from .hybrid_warmup import DEFAULT_LLR_SCALE, WarmupConfig
from .relay_reference import RelayConfig, RelayLegConfig
from .release_workflow import ReleaseWorkflowConfig, ReleaseWorkflowResult, run_release_workflow
from .stage2_integration import Stage2Cache, Stage2IntegrationConfig
from .telescope import Stage
from .unified_stage_controller import GraphType, StageBudget


BENCHMARK_NAME = "GROSS144_12ROUND_MEMORY_V1"
MEMORY_ROUNDS = 12
PHYSICAL_ERROR_RATE = 0.002
DATA_QUBITS = 144
CHECKS_PER_TYPE = 72
LOGICALS = 12
X_ANCILLA_OFFSET = DATA_QUBITS
Z_ANCILLA_OFFSET = X_ANCILLA_OFFSET + CHECKS_PER_TYPE


@dataclass(frozen=True)
class Gross144MemoryConfig:
    """Frozen FPGA-equivalent stage settings for the temporal memory graph."""

    name: str = "fixed_point_fpga_temporal_s1_s1a_gari_relay_gross144_v1"
    physical_error_rate: float = PHYSICAL_ERROR_RATE
    rounds: int = MEMORY_ROUNDS
    warmup_iterations: int = 2
    compressed_minsum_iterations: int = 6
    automorphism_minsum_iterations: int = 8
    gari_iterations: int = 4
    relay_iterations: int = 4
    relay_legs: int = 4
    relay_dither_amplitude: int = 9
    relay_dither_lfsr_seed: int = 0x1D872B41
    relay_dither_lfsr_polynomial: int = 0x80200003
    relay_dither_salt: int = 8
    llr_scale: int = DEFAULT_LLR_SCALE

    def validate(self) -> None:
        if not 0.0 < self.physical_error_rate < 0.5:
            raise ValueError("physical error rate must be in (0, 0.5)")
        if self.rounds != MEMORY_ROUNDS:
            raise ValueError("Gross144 memory benchmark is frozen at twelve rounds")
        if self.warmup_iterations != 2 or self.relay_legs != 4:
            raise ValueError("FPGA release requires two warm-up iterations and four Relay legs")
        if min(self.compressed_minsum_iterations, self.automorphism_minsum_iterations,
               self.gari_iterations, self.relay_iterations) < 0:
            raise ValueError("iteration limits must be non-negative")
        if not 1 <= self.relay_dither_amplitude <= 15 or self.llr_scale < 1:
            raise ValueError("invalid fixed-point configuration")

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Gross144MemoryDecodeResult:
    accepted: bool
    correction: tuple[int, ...]
    predicted_logicals: tuple[int, ...]
    input_syndrome: tuple[int, ...]
    final_stage: str
    iterations: int
    check_updates: int
    reason: str
    workflow: ReleaseWorkflowResult | None = None


@dataclass(frozen=True)
class _TemporalLayout:
    graph: Graph
    gari: GariGraph
    prior: tuple[float, ...]
    logicals: np.ndarray
    temporal_logicals: tuple[tuple[int, ...], ...]
    automorphisms: AutomorphismSelection
    measurement_offset: int
    readout_offset: int


@dataclass(frozen=True)
class _ComponentFault:
    """One independent CSS component at a named circuit fault location."""

    round_index: int
    section: str
    operation: str
    layer: int
    role: str
    index: int
    probability_class: str


@dataclass(frozen=True)
class _ComponentLayout:
    """Exact scheduled single-component graph used by the FPGA workflow."""

    graph: Graph
    gari: GariGraph
    prior: tuple[float, ...]
    logical_signatures: tuple[tuple[int, ...], ...]
    fault_data_masks: tuple[int, ...]
    detector_masks: tuple[int, ...]
    automorphisms: AutomorphismSelection
    fault_count: int
    discarded_invisible_components: int


def _rec(absolute_measurement: int, current_measurements: int) -> Any:
    import stim

    return stim.target_rec(absolute_measurement - current_measurements)


def _pauli_rows(matrix: np.ndarray, axis: str) -> list[Any]:
    import stim

    rows: list[Any] = []
    for row in np.asarray(matrix, dtype=np.uint8):
        value = stim.PauliString(DATA_QUBITS)
        for qubit in np.flatnonzero(row):
            value[int(qubit)] = axis
        rows.append(value)
    return rows


def ideal_encoded_preparation(code: Gross144Code, basis: str) -> Any:
    """Prepare a deterministic logical basis state outside the noise model."""

    import stim

    if basis not in {"X", "Z"}:
        raise ValueError("basis must be X or Z")
    stabilizers = _pauli_rows(code.hx, "X") + _pauli_rows(code.hz, "Z")
    stabilizers += _pauli_rows(code.lx if basis == "X" else code.lz, basis)
    try:
        # Gross144 has six dependent rows in each stabilizer matrix.  They
        # specify the same code space, so let Stim retain the independent
        # subset while accepting the complete public matrix description.
        return stim.Tableau.from_stabilizers(stabilizers, allow_redundant=True).to_circuit("mpp_state")
    except ValueError as exc:  # pragma: no cover - construction invariant
        raise ValueError("Gross144 logical preparation is not deterministic") from exc


def _perfect_matching(matrix: np.ndarray) -> np.ndarray:
    """Deterministic row-to-column perfect matching for a square binary block."""

    size = int(matrix.shape[0])
    neighbors = [np.flatnonzero(matrix[row]).astype(int).tolist() for row in range(size)]
    matched_row = [-1] * size

    def augment(row: int, seen: set[int]) -> bool:
        for column in neighbors[row]:
            if column in seen:
                continue
            seen.add(column)
            old_row = matched_row[column]
            if old_row < 0 or augment(old_row, seen):
                matched_row[column] = row
                return True
        return False

    for row in range(size):
        if not augment(row, set()):
            raise ValueError("Gross144 coefficient block has no perfect matching")
    result = np.empty(size, dtype=np.int16)
    for column, row in enumerate(matched_row):
        result[row] = column
    return result


def _factor_three_regular(block: np.ndarray) -> tuple[np.ndarray, ...]:
    """Factor one 72x72 degree-three group block into exact CNOT layers."""

    remaining = np.asarray(block, dtype=np.uint8).copy()
    if remaining.shape != (CHECKS_PER_TYPE, CHECKS_PER_TYPE) or not np.all(
            remaining.sum(axis=0) == 3) or not np.all(remaining.sum(axis=1) == 3):
        raise ValueError("Gross144 syndrome schedule requires degree-three coefficient blocks")
    layers: list[np.ndarray] = []
    for _ in range(3):
        matching = _perfect_matching(remaining)
        remaining[np.arange(CHECKS_PER_TYPE), matching] = 0
        layers.append(matching)
    if np.any(remaining):
        raise ValueError("Gross144 coefficient factorization left unmatched edges")
    return tuple(layers)


def syndrome_layers(check_matrix: np.ndarray) -> tuple[np.ndarray, ...]:
    """Return the explicit six group-element CNOT layers for one CSS half.

    A generic regular-graph factorization is a valid Tanner-edge schedule but
    is *not* the Gross construction's layer schedule: it can arbitrarily
    break the ``Z_12 x Z_6`` translations used by S1A.  The FPGA path needs
    the named group elements themselves, in the polynomial order used to
    construct ``A`` and ``B``.  Keeping this table explicit also makes the
    circuit schedule a stable benchmark artifact instead of an incidental
    property of a matching implementation.
    """

    matrix = np.asarray(check_matrix, dtype=np.uint8)
    if matrix.shape != (CHECKS_PER_TYPE, DATA_QUBITS):
        raise ValueError("Gross144 check matrix shape mismatch")

    ell, m = GROSS144_PARAMETERS["ell"], GROSS144_PARAMETERS["m"]

    def group_element(delta_x: int, delta_y: int) -> np.ndarray:
        return np.asarray([
            (((index // m + delta_x) % ell) * m + ((index % m + delta_y) % m))
            for index in range(CHECKS_PER_TYPE)
        ], dtype=np.int16)

    # Hx = [A, B], Hz = [B^T, A^T], where
    # A=x^3+y+y^2 and B=y^3+x+x^2.  A transpose inverts the group element.
    candidates = (
        (( (3, 0), (0, 1), (0, 2), (0, 3), (1, 0), (2, 0) ), False),
        (( (0, -3), (-1, 0), (-2, 0), (-3, 0), (0, -1), (0, -2) ), False),
    )
    for terms, _unused in candidates:
        layers = tuple(
            group_element(delta_x, delta_y) + (CHECKS_PER_TYPE if index >= 3 else 0)
            for index, (delta_x, delta_y) in enumerate(terms)
        )
        reconstructed = np.zeros_like(matrix)
        for layer in layers:
            reconstructed[np.arange(CHECKS_PER_TYPE), layer] ^= 1
        if np.array_equal(reconstructed, matrix):
            return layers
    raise ValueError("check matrix is not the declared Gross144 group-element schedule")


def _append_reset(circuit: Any, gate: str, qubits: Sequence[int], p: float) -> None:
    circuit.append(gate, list(qubits))
    circuit.append("DEPOLARIZE1", list(qubits), p)


def _append_measure(circuit: Any, gate: str, qubits: Sequence[int], p: float) -> None:
    circuit.append("DEPOLARIZE1", list(qubits), p)
    circuit.append(gate, list(qubits))


def _append_half(circuit: Any, *, layers: Sequence[np.ndarray], ancillas: Sequence[int],
                 x_checks: bool, p: float) -> None:
    for layer in layers:
        pairs: list[int] = []
        for check, data in enumerate(layer):
            pairs.extend((ancillas[check], int(data)) if x_checks else (int(data), ancillas[check]))
        circuit.append("CX", pairs)
        circuit.append("DEPOLARIZE2", pairs, p)
        circuit.append("TICK")


def build_gross144_memory_circuit(*, basis: str, p: float = PHYSICAL_ERROR_RATE,
                                  rounds: int = MEMORY_ROUNDS,
                                  code: Gross144Code | None = None) -> Any:
    """Build the frozen 12-round no-idle circuit-level Gross144 memory experiment."""

    import stim

    if basis not in {"X", "Z"} or rounds != MEMORY_ROUNDS or not 0.0 < p < 1.0:
        raise ValueError("invalid frozen Gross144 memory circuit parameters")
    code = code or build_gross144()
    circuit = stim.Circuit()
    circuit += ideal_encoded_preparation(code, basis)
    circuit.append("TICK")
    x_ancillas = tuple(range(X_ANCILLA_OFFSET, Z_ANCILLA_OFFSET))
    z_ancillas = tuple(range(Z_ANCILLA_OFFSET, Z_ANCILLA_OFFSET + CHECKS_PER_TYPE))
    x_layers, z_layers = syndrome_layers(code.hx), syndrome_layers(code.hz)
    measurements = circuit.num_measurements
    prior_x: list[int] | None = None
    prior_z: list[int] | None = None
    for _round in range(rounds):
        _append_reset(circuit, "RX", x_ancillas, p)
        circuit.append("TICK")
        _append_half(circuit, layers=x_layers, ancillas=x_ancillas, x_checks=True, p=p)
        _append_measure(circuit, "MX", x_ancillas, p)
        x_records = list(range(measurements, measurements + CHECKS_PER_TYPE))
        measurements += CHECKS_PER_TYPE
        for current, previous in zip(x_records, prior_x or [None] * CHECKS_PER_TYPE):
            targets = [_rec(current, measurements)]
            if previous is not None:
                targets.append(_rec(previous, measurements))
            circuit.append("DETECTOR", targets)
        prior_x = x_records
        circuit.append("TICK")

        _append_reset(circuit, "R", z_ancillas, p)
        circuit.append("TICK")
        _append_half(circuit, layers=z_layers, ancillas=z_ancillas, x_checks=False, p=p)
        _append_measure(circuit, "M", z_ancillas, p)
        z_records = list(range(measurements, measurements + CHECKS_PER_TYPE))
        measurements += CHECKS_PER_TYPE
        for current, previous in zip(z_records, prior_z or [None] * CHECKS_PER_TYPE):
            targets = [_rec(current, measurements)]
            if previous is not None:
                targets.append(_rec(previous, measurements))
            circuit.append("DETECTOR", targets)
        prior_z = z_records
        circuit.append("TICK")

    _append_measure(circuit, "MX" if basis == "X" else "M", range(DATA_QUBITS), p)
    data_records = list(range(measurements, measurements + DATA_QUBITS))
    measurements += DATA_QUBITS
    boundary_matrix = code.hx if basis == "X" else code.hz
    boundary_records = prior_x if basis == "X" else prior_z
    if boundary_records is None:  # pragma: no cover - rounds is fixed positive
        raise ValueError("missing final syndrome boundary")
    for check, row in enumerate(boundary_matrix):
        targets = [_rec(boundary_records[check], measurements)]
        targets.extend(_rec(data_records[int(qubit)], measurements)
                       for qubit in np.flatnonzero(row))
        circuit.append("DETECTOR", targets)
    for logical, row in enumerate(code.lx if basis == "X" else code.lz):
        circuit.append("OBSERVABLE_INCLUDE", [_rec(data_records[int(qubit)], measurements)
                                               for qubit in np.flatnonzero(row)], logical)
    return circuit


class Gross144TemporalFpgaAdapter:
    """Detector-only temporal lift of the fixed-point FPGA release workflow."""

    def __init__(self, code: Gross144Code | None = None, *,
                 config: Gross144MemoryConfig | None = None):
        self.code = code or build_gross144()
        self.code.validate()
        self.config = config or Gross144MemoryConfig()
        self.config.validate()
        self.detector_count = self.config.rounds * 2 * CHECKS_PER_TYPE + CHECKS_PER_TYPE
        self._layouts = {basis: self._build_layout(basis) for basis in ("X", "Z")}
        self._caches = {basis: Stage2Cache() for basis in ("X", "Z")}

    @property
    def decoder_configuration_hash(self) -> str:
        return self.config.digest()

    def _build_layout(self, basis: str) -> _TemporalLayout:
        check = self.code.hx if basis == "X" else self.code.hz
        logicals = self.code.lx if basis == "X" else self.code.lz
        base_graph, _ = self.code.graph("Z" if basis == "X" else "X")
        if not np.array_equal(np.asarray(base_graph.to_matrix(), dtype=np.uint8), check):
            raise ValueError("Gross144 temporal channel does not match its CSS check matrix")
        rows = [np.flatnonzero(row).astype(int) for row in check]
        rounds = self.config.rounds
        measurement_offset = rounds * DATA_QUBITS
        readout_offset = measurement_offset + rounds * CHECKS_PER_TYPE
        variables = readout_offset + DATA_QUBITS
        neighbors: list[tuple[int, ...]] = []
        for check_id, row in enumerate(rows):
            neighbors.append(tuple(row.tolist()) + (measurement_offset + check_id,))
        for round_index in range(1, rounds):
            data_base = round_index * DATA_QUBITS
            measurement_base = measurement_offset + round_index * CHECKS_PER_TYPE
            previous_base = measurement_base - CHECKS_PER_TYPE
            for check_id, row in enumerate(rows):
                neighbors.append(tuple((data_base + row).tolist()) +
                                 (measurement_base + check_id, previous_base + check_id))
        for check_id, row in enumerate(rows):
            neighbors.append(tuple((readout_offset + row).tolist()) +
                             (measurement_offset + (rounds - 1) * CHECKS_PER_TYPE + check_id,))
        temporal_logicals: list[tuple[int, ...]] = []
        for logical in logicals:
            signature = np.zeros(variables, dtype=np.uint8)
            support = np.flatnonzero(logical)
            for round_index in range(rounds):
                signature[round_index * DATA_QUBITS + support] = 1
            signature[readout_offset + support] = 1
            temporal_logicals.append(tuple(int(bit) for bit in signature))
        variable_actions: list[tuple[int, ...]] = []
        check_actions: list[tuple[int, ...]] = []
        for data_action, check_action in zip(base_graph.group.variable_permutations,
                                             base_graph.group.check_permutations):
            variable_map = list(range(variables))
            for round_index in range(rounds):
                data_base = round_index * DATA_QUBITS
                measure_base = measurement_offset + round_index * CHECKS_PER_TYPE
                for old, new in enumerate(data_action):
                    variable_map[data_base + old] = data_base + new
                for old, new in enumerate(check_action):
                    variable_map[measure_base + old] = measure_base + new
            for old, new in enumerate(data_action):
                variable_map[readout_offset + old] = readout_offset + new
            check_map = list(range((rounds + 1) * CHECKS_PER_TYPE))
            for time_index in range(rounds + 1):
                base = time_index * CHECKS_PER_TYPE
                for old, new in enumerate(check_action):
                    check_map[base + old] = base + new
            variable_actions.append(tuple(variable_map))
            check_actions.append(tuple(check_map))
        graph = Graph.from_neighbors(variables, neighbors)
        graph = replace(graph, group=GroupActionMetadata(tuple(variable_actions), tuple(check_actions)))
        if not all(graph.preserves_graph(action, checks)
                   for action, checks in zip(graph.group.variable_permutations,
                                             graph.group.check_permutations)):
            raise ValueError("Gross144 temporal automorphism lift is invalid")
        p = self.config.physical_error_rate
        x_counts = self.code.hx.sum(axis=0).astype(float)
        z_counts = self.code.hz.sum(axis=0).astype(float)
        if basis == "X":
            interval_counts = [x_counts] + [x_counts + z_counts] * (rounds - 1)
            tail_counts = z_counts
        else:
            interval_counts = [x_counts + z_counts] * rounds
            tail_counts = np.zeros(DATA_QUBITS, dtype=float)
        data_probabilities = np.concatenate([
            1.0 - np.power(1.0 - 8.0 * p / 15.0, counts)
            for counts in interval_counts
        ])
        measurement_probability = 1.0 - ((1.0 - 2.0 * p / 3.0) ** 2 *
                                          (1.0 - 8.0 * p / 15.0) ** 6)
        readout_probability = 2.0 * p / 3.0
        tail_probability = 1.0 - (1.0 - (1.0 - np.power(1.0 - 8.0 * p / 15.0,
                                                          tail_counts))) * (1.0 - readout_probability)
        probabilities = np.concatenate((data_probabilities,
                                        np.full(rounds * CHECKS_PER_TYPE, measurement_probability),
                                        tail_probability))
        prior = tuple(math.log((1.0 - float(value)) / float(value)) for value in probabilities)
        gari = GariGraph.from_decoder_graph(
            graph, check_types=("D_X",) * len(graph.checks), logical_signatures=temporal_logicals,
        )
        automorphisms = select_decorrelated_group_trials(
            graph, max_iterations=self.config.automorphism_minsum_iterations,
        )
        return _TemporalLayout(graph, gari, prior, np.asarray(logicals, dtype=np.uint8),
                               tuple(temporal_logicals), automorphisms,
                               measurement_offset, readout_offset)

    def _adapt(self, detectors: Sequence[int], basis: str) -> tuple[int, ...]:
        values = np.asarray(detectors, dtype=np.uint8)
        if values.ndim != 1 or len(values) != self.detector_count or np.any(values > 1):
            raise ValueError("Gross144 circuit detector width/value mismatch")
        events = values[:self.config.rounds * 2 * CHECKS_PER_TYPE].reshape(
            self.config.rounds, 2, CHECKS_PER_TYPE
        )
        channel = 0 if basis == "X" else 1
        return tuple(int(bit) for bit in np.concatenate((events[:, channel, :].reshape(-1),
                                                          values[-CHECKS_PER_TYPE:])))

    def _workflow_config(self, layout: _TemporalLayout, syndrome: Sequence[int]) -> ReleaseWorkflowConfig:
        perturbations = _gross144_relay_perturbations(
            layout.graph.num_variables, syndrome=syndrome,
            amplitude=self.config.relay_dither_amplitude,
            lfsr_seed=self.config.relay_dither_lfsr_seed,
            lfsr_polynomial=self.config.relay_dither_lfsr_polynomial,
            salt=self.config.relay_dither_salt,
        )
        relay = RelayConfig(tuple(
            RelayLegConfig(leg_id, perturbation=perturbations[leg_id],
                           max_iterations=self.config.relay_iterations)
            for leg_id in range(self.config.relay_legs)
        ), quorum=1)
        # The release defaults are sized for the 390-check static graph.  This
        # memory lift has 936 detector checks: with their original limits S1A
        # is forcibly timed out after 10,000 work units, before mandatory GARI
        # and Relay can run.  These limits are exact maxima implied by the
        # frozen stage counts, not a decoder-parameter retune.
        checks = len(layout.graph.checks)
        stage_budgets = (
            StageBudget(Stage.S1, (self.config.warmup_iterations +
                                   self.config.compressed_minsum_iterations) * checks),
            StageBudget(Stage.S1A, 4 * (self.config.warmup_iterations +
                                        self.config.automorphism_minsum_iterations) * checks),
            # GARI charges its cold resident-image load (64) plus one
            # unpaged detector page, in addition to one update per check.
            StageBudget(Stage.S2, self.config.gari_iterations * checks + 65),
            StageBudget(Stage.S2R, self.config.relay_legs * self.config.relay_iterations * checks),
            StageBudget(Stage.HOST, 0),
        )
        return ReleaseWorkflowConfig(
            graph_type=GraphType.CIRCUIT,
            warmup=WarmupConfig(
                warmup_iterations=self.config.warmup_iterations,
                minsum_iterations=self.config.compressed_minsum_iterations,
                llr_scale=self.config.llr_scale,
            ),
            automorphism_iterations=self.config.automorphism_minsum_iterations,
            stage2=Stage2IntegrationConfig(
                max_iterations=self.config.gari_iterations,
                logical_signatures=layout.temporal_logicals,
            ),
            relay=relay,
            logical_signatures=layout.temporal_logicals,
            stage_budgets=stage_budgets,
            total_work_budget=sum(budget.max_work for budget in stage_budgets),
        )

    def decode(self, detectors: Sequence[int], *, basis: str) -> Gross144MemoryDecodeResult:
        if basis not in self._layouts:
            raise ValueError("basis must be X or Z")
        target = self._adapt(detectors, basis)
        layout = self._layouts[basis]
        if not any(target):
            return Gross144MemoryDecodeResult(True, (0,) * DATA_QUBITS, (0,) * LOGICALS,
                                               target, "S0", 0, 0, "zero detector syndrome")
        graph = replace(layout.graph, syndrome=target)
        workflow = run_release_workflow(
            graph, layout.prior, syndrome=target,
            config=self._workflow_config(layout, target),
            gari=layout.gari.with_syndrome(target, original_graph=graph),
            stage2_cache=self._caches[basis], automorphism_selection=layout.automorphisms,
        )
        candidate = workflow.controller.selected_candidate
        if candidate is None:
            return Gross144MemoryDecodeResult(
                False, (0,) * DATA_QUBITS, (0,) * LOGICALS, target, "defer",
                _workflow_iterations(workflow), workflow.controller.total_work,
                workflow.controller.reason or "all FPGA stages deferred", workflow,
            )
        correction = np.asarray(candidate.correction, dtype=np.uint8)
        data_history = correction[:layout.measurement_offset].reshape(self.config.rounds, DATA_QUBITS)
        net = np.bitwise_xor.reduce(data_history, axis=0) ^ correction[
            layout.readout_offset:layout.readout_offset + DATA_QUBITS
        ]
        predicted = tuple(int(value) for value in ((layout.logicals @ net) & 1))
        return Gross144MemoryDecodeResult(
            True, tuple(int(value) for value in net), predicted, target, candidate.source_stage,
            _workflow_iterations(workflow), workflow.controller.total_work, "", workflow,
        )


def circuit_schedule_sha256(code: Gross144Code | None = None) -> str:
    """Hash the explicit six-layer X/Z group-match schedule."""

    code = code or build_gross144()
    payload = {
        "X": [layer.astype(int).tolist() for layer in syndrome_layers(code.hx)],
        "Z": [layer.astype(int).tolist() for layer in syndrome_layers(code.hz)],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Gross144CircuitFpgaAdapter:
    """Circuit-scheduled input image for the existing FPGA decoder pipeline.

    The static FPGA decoder sees one binary CSS component at a time.  This
    adapter keeps that representation, but replaces the inaccurate
    one-data-error-per-round phenomenological lift with a variable for every
    *named relevant component location* in the frozen CNOT schedule.  CNOT
    components are placed immediately after their CNOTs and are propagated
    through the remaining layers exactly.  It is therefore a decoder-input
    adaptation, not a replacement decoder: all nonzero syndromes still pass
    through fixed-point S1 -> S1A -> GARI -> four-leg Relay.

    ``DEPOLARIZE2`` correlations between the two CSS components are outside
    this binary FPGA path.  Their first-order component marginals are exact
    (``8p/15`` after a CNOT); the full physical circuit remains the sole shot
    generator and scoring authority.
    """

    def __init__(self, code: Gross144Code | None = None, *,
                 config: Gross144MemoryConfig | None = None):
        self.code = code or build_gross144()
        self.code.validate()
        self.config = config or Gross144MemoryConfig(
            name="fixed_point_fpga_component_schedule_s1_s1a_gari_relay_gross144_v3",
            # The static 6-edge image uses scale four.  Circuit-component
            # checks have up to 41 edges; scale four drives the 5-bit message
            # path into saturation and prevents parity decisions.  Scale two
            # preserves the same fixed-point primitives without saturation.
            llr_scale=2,
        )
        self.config.validate()
        self.detector_count = self.config.rounds * 2 * CHECKS_PER_TYPE + CHECKS_PER_TYPE
        self._layouts = {basis: self._build_layout(basis) for basis in ("X", "Z")}
        self._caches = {basis: Stage2Cache() for basis in ("X", "Z")}

    @property
    def decoder_configuration_hash(self) -> str:
        return self.config.digest()

    @staticmethod
    def _fault_key(fault: _ComponentFault) -> tuple[int, str, str, int, str, int, str]:
        return (fault.round_index, fault.section, fault.operation, fault.layer,
                fault.role, fault.index, fault.probability_class)

    def _response_records(
        self, basis: str,
    ) -> tuple[list[tuple[_ComponentFault, int]], int]:
        """Propagate every named binary component through the scheduled circuit.

        The reverse Clifford pass stores, for each qubit at each insertion
        boundary, the detector/logical/data response to a component inserted
        there.  This avoids sampling or looking at the true error pattern
        while retaining the exact placement of every relevant single-qubit
        component in the circuit-level schedule.
        """

        check = self.code.hx if basis == "X" else self.code.hz
        logicals = self.code.lx if basis == "X" else self.code.lz
        x_layers = syndrome_layers(self.code.hx)
        z_layers = syndrome_layers(self.code.hz)
        x_ancillas = tuple(range(X_ANCILLA_OFFSET, Z_ANCILLA_OFFSET))
        z_ancillas = tuple(range(Z_ANCILLA_OFFSET, Z_ANCILLA_OFFSET + CHECKS_PER_TYPE))
        operations: list[tuple[str, int, str, int, tuple[int, ...]]] = []
        for round_index in range(self.config.rounds):
            operations.append(("reset", round_index, "X", -1, x_ancillas))
            for layer_index, layer in enumerate(x_layers):
                pairs = tuple(value for check_id, data in enumerate(layer)
                              for value in (x_ancillas[check_id], int(data)))
                operations.append(("cnot", round_index, "X", layer_index, pairs))
            operations.append(("measure", round_index, "X", -1, x_ancillas))
            operations.append(("reset", round_index, "Z", -1, z_ancillas))
            for layer_index, layer in enumerate(z_layers):
                pairs = tuple(value for check_id, data in enumerate(layer)
                              for value in (int(data), z_ancillas[check_id]))
                operations.append(("cnot", round_index, "Z", layer_index, pairs))
            operations.append(("measure", round_index, "Z", -1, z_ancillas))
        operations.append(("final_measure", self.config.rounds, "D", -1,
                           tuple(range(DATA_QUBITS))))

        detector_bits = (self.config.rounds + 1) * CHECKS_PER_TYPE
        logical_offset = detector_bits
        data_offset = logical_offset + LOGICALS
        responses = [0] * (Z_ANCILLA_OFFSET + CHECKS_PER_TYPE)
        records: list[tuple[_ComponentFault, int]] = []

        def ancilla_record_response(round_index: int, check_id: int) -> int:
            value = 1 << (round_index * CHECKS_PER_TYPE + check_id)
            next_index = round_index + 1
            value |= 1 << (next_index * CHECKS_PER_TYPE + check_id)
            return value

        def final_data_response(qubit: int) -> int:
            value = 1 << (data_offset + qubit)
            for check_id in np.flatnonzero(check[:, qubit]):
                value |= 1 << (self.config.rounds * CHECKS_PER_TYPE + int(check_id))
            for logical_id in np.flatnonzero(logicals[:, qubit]):
                value |= 1 << (logical_offset + int(logical_id))
            return value

        def role_and_index(qubit: int) -> tuple[str, int]:
            if qubit < DATA_QUBITS:
                return "data", qubit
            if qubit < Z_ANCILLA_OFFSET:
                return "x_ancilla", qubit - X_ANCILLA_OFFSET
            return "z_ancilla", qubit - Z_ANCILLA_OFFSET

        # The backward update is the binary symplectic propagation for the
        # one relevant component: Z for X-memory, X for Z-memory.
        for operation, round_index, section, layer, targets in reversed(operations):
            if operation == "final_measure":
                for qubit in targets:
                    responses[qubit] = final_data_response(qubit)
                for qubit in targets:
                    records.append((_ComponentFault(
                        round_index, section, "measure", layer, "data", qubit,
                        "reset_measure"), responses[qubit]))
                continue
            if operation == "measure":
                for qubit in targets:
                    check_id = qubit - (X_ANCILLA_OFFSET if section == "X" else Z_ANCILLA_OFFSET)
                    responses[qubit] = (
                        ancilla_record_response(round_index, check_id)
                        if section == basis else 0
                    )
                for qubit in targets:
                    role, index = role_and_index(qubit)
                    records.append((_ComponentFault(
                        round_index, section, "measure", layer, role, index,
                        "reset_measure"), responses[qubit]))
                continue
            if operation == "reset":
                for qubit in targets:
                    role, index = role_and_index(qubit)
                    records.append((_ComponentFault(
                        round_index, section, "reset", layer, role, index,
                        "reset_measure"), responses[qubit]))
                for qubit in targets:
                    responses[qubit] = 0
                continue
            if operation != "cnot":  # pragma: no cover - timeline invariant
                raise ValueError(f"unknown Gross144 component operation {operation}")
            for qubit in targets:
                role, index = role_and_index(qubit)
                records.append((_ComponentFault(
                    round_index, section, "cnot", layer, role, index, "cnot"),
                    responses[qubit]))
            for control, target in zip(targets[::2], targets[1::2]):
                if basis == "X":
                    # CNOT maps Z(target) to Z(control) Z(target).
                    responses[target] ^= responses[control]
                else:
                    # CNOT maps X(control) to X(control) X(target).
                    responses[control] ^= responses[target]

        if not records:  # pragma: no cover - frozen non-empty circuit
            raise ValueError("Gross144 component timeline has no fault locations")
        return records, detector_bits

    @staticmethod
    def _map_detector_mask(mask: int, check_permutation: Sequence[int]) -> int:
        """Apply one spatial check permutation at every detector time slice."""

        mapped = 0
        while mask:
            low_bit = mask & -mask
            detector_id = low_bit.bit_length() - 1
            time_index, check_id = divmod(detector_id, CHECKS_PER_TYPE)
            mapped |= 1 << (time_index * CHECKS_PER_TYPE + int(check_permutation[check_id]))
            mask ^= low_bit
        return mapped

    def _build_component_automorphisms(
        self,
        *,
        basis: str,
        graph: Graph,
        detector_masks: Sequence[int],
        probabilities: Sequence[float],
    ) -> tuple[AutomorphismSelection, GroupActionMetadata]:
        """Lift the frozen static S1A table through group-element circuit layers."""

        error_type = "Z" if basis == "X" else "X"
        base_graph, _ = self.code.graph(error_type)
        base_selection = select_decorrelated_group_trials(
            base_graph, max_iterations=self.config.automorphism_minsum_iterations,
        )
        # Equivalent independent components with identical detector *and
        # logical* response were folded into their exact parity variable below.
        # Their detector masks are therefore unique, which gives a direct
        # group lift without guessing a physical fault from a sampled shot.
        fault_ids = {mask: index for index, mask in enumerate(detector_masks)}

        component_trials: list[AutomorphismTrial] = []
        graph_variable_actions: list[tuple[int, ...]] = []
        graph_check_actions: list[tuple[int, ...]] = []
        for trial in base_selection.trials:
            variable_map: list[int] = []
            for fault_id, detector_mask in enumerate(detector_masks):
                expected = self._map_detector_mask(detector_mask, trial.check_permutation)
                try:
                    mapped_id = fault_ids[expected]
                except KeyError as exc:  # pragma: no cover - group-layer invariant
                    raise ValueError("Gross144 group layer maps a component outside the image") from exc
                if not math.isclose(probabilities[fault_id], probabilities[mapped_id],
                                    rel_tol=0.0, abs_tol=1e-15):
                    raise ValueError(
                        "Gross144 group-element schedule changes a component prior"
                    )
                variable_map.append(mapped_id)
            check_map = tuple(
                time_index * CHECKS_PER_TYPE + int(trial.check_permutation[check_id])
                for time_index in range(self.config.rounds + 1)
                for check_id in range(CHECKS_PER_TYPE)
            )
            action = tuple(variable_map)
            if not graph.preserves_graph(action, check_map):
                raise ValueError("Gross144 component group table failed graph validation")
            component_trials.append(AutomorphismTrial(
                trial.trial_id, action, check_map,
                max_iterations=self.config.automorphism_minsum_iterations,
                schedule_id=trial.schedule_id,
            ))
            graph_variable_actions.append(action)
            graph_check_actions.append(check_map)
        # S1A consumes the frozen four static representatives.  The support
        # statistics describe their source selection and are intentionally
        # retained as metadata rather than recomputed from this much larger
        # circuit graph on every image build.
        selection = AutomorphismSelection(
            trials=tuple(component_trials),
            candidate_count=base_selection.candidate_count,
            coset_count=base_selection.coset_count,
            short_cycle_supports=(),
            trapping_set_supports=(),
            minimum_pairwise_decorrelation=base_selection.minimum_pairwise_decorrelation,
            pairwise_decorrelation=base_selection.pairwise_decorrelation,
        )
        return selection, GroupActionMetadata(tuple(graph_variable_actions), tuple(graph_check_actions))

    def _build_layout(self, basis: str) -> _ComponentLayout:
        records, detector_bits = self._response_records(basis)
        if detector_bits != (self.config.rounds + 1) * CHECKS_PER_TYPE:
            raise ValueError("Gross144 component detector layout mismatch")
        detector_mask_limit = (1 << detector_bits) - 1
        logical_mask_limit = (1 << LOGICALS) - 1
        visible: list[tuple[_ComponentFault, int, int, int]] = []
        discarded = 0
        for fault, response in records:
            detector_mask = response & detector_mask_limit
            logical_mask = (response >> detector_bits) & logical_mask_limit
            data_mask = response >> (detector_bits + LOGICALS)
            if not detector_mask:
                if logical_mask:
                    raise ValueError(
                        "Gross144 component model found a logical-only single fault; "
                        "the frozen binary decoder cannot score it honestly"
                    )
                discarded += 1
                continue
            visible.append((fault, detector_mask, logical_mask, data_mask))
        # Coalesce only response-identical independent components.  For a
        # bucket with probabilities q_i, its parity has q=(1-prod(1-2q_i))/2.
        # That preserves every detector and logical bit exactly while removing
        # redundant variables that the FPGA has no reason to distinguish.
        # We deliberately do not merge merely detector-identical columns with
        # different logical actions.
        grouped: dict[tuple[int, int], list[tuple[_ComponentFault, int]]] = {}
        for fault, detector_mask, logical_mask, data_mask in visible:
            grouped.setdefault((detector_mask, logical_mask), []).append((fault, data_mask))
        probability_by_class = {
            "reset_measure": 2.0 * self.config.physical_error_rate / 3.0,
            "cnot": 8.0 * self.config.physical_error_rate / 15.0,
        }
        folded: list[tuple[_ComponentFault, int, int, int, float]] = []
        for (detector_mask, logical_mask), components in sorted(grouped.items()):
            bias = 1.0
            for fault, _data_mask in components:
                bias *= 1.0 - 2.0 * probability_by_class[fault.probability_class]
            representative, data_mask = components[0]
            folded.append((representative, detector_mask, logical_mask, data_mask,
                           (1.0 - bias) / 2.0))
        faults = tuple(item[0] for item in folded)
        detector_masks = tuple(item[1] for item in folded)
        probabilities = tuple(item[4] for item in folded)
        check_neighbors: list[list[int]] = [[] for _ in range(detector_bits)]
        for fault_id, detector_mask in enumerate(detector_masks):
            remaining = detector_mask
            while remaining:
                low_bit = remaining & -remaining
                check_neighbors[low_bit.bit_length() - 1].append(fault_id)
                remaining ^= low_bit
        graph = Graph.from_neighbors(len(faults), check_neighbors)
        automorphisms, group = self._build_component_automorphisms(
            basis=basis, graph=graph, detector_masks=detector_masks, probabilities=probabilities,
        )
        graph = replace(graph, group=group)
        prior = tuple(math.log((1.0 - probability) / probability)
                      for probability in probabilities)
        logical_signatures = tuple(
            tuple((item[2] >> logical_id) & 1 for item in folded)
            for logical_id in range(LOGICALS)
        )
        gari = GariGraph.from_decoder_graph(
            graph, check_types=("D_X",) * len(graph.checks),
            logical_signatures=logical_signatures,
        )
        return _ComponentLayout(
            graph=graph, gari=gari, prior=prior, logical_signatures=logical_signatures,
            fault_data_masks=tuple(item[3] for item in folded), detector_masks=detector_masks,
            automorphisms=automorphisms,
            fault_count=len(faults), discarded_invisible_components=discarded,
        )

    def _adapt(self, detectors: Sequence[int], basis: str) -> tuple[int, ...]:
        values = np.asarray(detectors, dtype=np.uint8)
        if values.ndim != 1 or len(values) != self.detector_count or np.any(values > 1):
            raise ValueError("Gross144 circuit detector width/value mismatch")
        events = values[:self.config.rounds * 2 * CHECKS_PER_TYPE].reshape(
            self.config.rounds, 2, CHECKS_PER_TYPE
        )
        channel = 0 if basis == "X" else 1
        return tuple(int(bit) for bit in np.concatenate((events[:, channel, :].reshape(-1),
                                                          values[-CHECKS_PER_TYPE:])))

    def _workflow_config(self, layout: _ComponentLayout,
                         syndrome: Sequence[int]) -> ReleaseWorkflowConfig:
        perturbations = _gross144_relay_perturbations(
            layout.graph.num_variables, syndrome=syndrome,
            amplitude=self.config.relay_dither_amplitude,
            lfsr_seed=self.config.relay_dither_lfsr_seed,
            lfsr_polynomial=self.config.relay_dither_lfsr_polynomial,
            salt=self.config.relay_dither_salt,
        )
        relay = RelayConfig(tuple(
            RelayLegConfig(leg_id, perturbation=perturbations[leg_id],
                           max_iterations=self.config.relay_iterations)
            for leg_id in range(self.config.relay_legs)
        ), quorum=1)
        checks = len(layout.graph.checks)
        stage_budgets = (
            StageBudget(Stage.S1, (self.config.warmup_iterations +
                                   self.config.compressed_minsum_iterations) * checks),
            StageBudget(Stage.S1A, 4 * (self.config.warmup_iterations +
                                        self.config.automorphism_minsum_iterations) * checks),
            # GARI charges its cold resident-image load (64) plus one
            # unpaged detector page, in addition to one update per check.
            StageBudget(Stage.S2, self.config.gari_iterations * checks + 65),
            StageBudget(Stage.S2R, self.config.relay_legs * self.config.relay_iterations * checks),
            StageBudget(Stage.HOST, 0),
        )
        return ReleaseWorkflowConfig(
            graph_type=GraphType.CIRCUIT,
            warmup=WarmupConfig(
                warmup_iterations=self.config.warmup_iterations,
                minsum_iterations=self.config.compressed_minsum_iterations,
                llr_scale=self.config.llr_scale,
            ),
            automorphism_iterations=self.config.automorphism_minsum_iterations,
            stage2=Stage2IntegrationConfig(
                max_iterations=self.config.gari_iterations,
                logical_signatures=layout.logical_signatures,
            ),
            relay=relay,
            logical_signatures=layout.logical_signatures,
            stage_budgets=stage_budgets,
            total_work_budget=sum(budget.max_work for budget in stage_budgets),
        )

    def decode(self, detectors: Sequence[int], *, basis: str) -> Gross144MemoryDecodeResult:
        if basis not in self._layouts:
            raise ValueError("basis must be X or Z")
        target = self._adapt(detectors, basis)
        layout = self._layouts[basis]
        if not any(target):
            return Gross144MemoryDecodeResult(True, (0,) * DATA_QUBITS, (0,) * LOGICALS,
                                               target, "S0", 0, 0, "zero detector syndrome")
        graph = replace(layout.graph, syndrome=target)
        workflow = run_release_workflow(
            graph, layout.prior, syndrome=target,
            config=self._workflow_config(layout, target),
            gari=layout.gari.with_syndrome(target, original_graph=graph),
            stage2_cache=self._caches[basis], automorphism_selection=layout.automorphisms,
        )
        candidate = workflow.controller.selected_candidate
        if candidate is None:
            return Gross144MemoryDecodeResult(
                False, (0,) * DATA_QUBITS, (0,) * LOGICALS, target, "defer",
                _workflow_iterations(workflow), workflow.controller.total_work,
                workflow.controller.reason or "all FPGA stages deferred", workflow,
            )
        correction = np.asarray(candidate.correction, dtype=np.uint8)
        net_mask = 0
        for fault_id in np.flatnonzero(correction):
            net_mask ^= layout.fault_data_masks[int(fault_id)]
        net = tuple((net_mask >> qubit) & 1 for qubit in range(DATA_QUBITS))
        predicted = tuple(
            int(sum(int(bit) * logical[fault_id] for fault_id, bit in enumerate(correction)) & 1)
            for logical in layout.logical_signatures
        )
        return Gross144MemoryDecodeResult(
            True, net, predicted, target, candidate.source_stage,
            _workflow_iterations(workflow), workflow.controller.total_work, "", workflow,
        )
