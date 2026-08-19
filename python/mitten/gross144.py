"""Frozen Gross ``[[144,12,12]]`` code and FPGA-path decoder adapter.

The public bivariate-bicycle construction is ``ell=12``, ``m=6``,
``A=x^3+y+y^2`` and ``B=y^3+x+x^2``.  This module implements that
construction directly, derives a deterministic paired CSS logical basis over
GF(2), and drives the existing fixed-point S1 -> S1A -> GARI -> Relay
workflow.  It intentionally contains no OSD, matching, or floating-point
decoder fallback.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Literal, Sequence

import numpy as np

from .gari import GariGraph
from .graph_model import Graph, GroupActionMetadata
from .hybrid_warmup import DEFAULT_LLR_SCALE, WarmupConfig
from .automorphism import AutomorphismSelection, select_decorrelated_group_trials
from .relay_reference import RelayConfig, RelayLegConfig
from .release_workflow import ReleaseWorkflowConfig, ReleaseWorkflowResult, run_release_workflow
from .stage2_integration import Stage2Cache, Stage2IntegrationConfig
from .unified_stage_controller import GraphType


GROSS144_NAME = "GROSS_144_12_12_V1"
GROSS144_PARAMETERS = {
    "ell": 12,
    "m": 6,
    "A": "x^3 + y + y^2",
    "B": "y^3 + x + x^2",
}
ErrorType = Literal["X", "Z"]


@dataclass(frozen=True)
class Gross144Code:
    """Binary CSS matrices with a paired 12-logical basis."""

    hx: np.ndarray
    hz: np.ndarray
    lx: np.ndarray
    lz: np.ndarray

    @property
    def n(self) -> int:
        return int(self.hx.shape[1])

    @property
    def k(self) -> int:
        return int(self.lx.shape[0])

    def validate(self) -> None:
        if self.hx.shape != (72, 144) or self.hz.shape != (72, 144):
            raise ValueError("Gross144 check matrices must be 72 by 144")
        if self.lx.shape != (12, 144) or self.lz.shape != (12, 144):
            raise ValueError("Gross144 logical matrices must be 12 by 144")
        matrices = (self.hx, self.hz, self.lx, self.lz)
        if any(matrix.dtype != np.uint8 or np.any(matrix > 1) for matrix in matrices):
            raise ValueError("Gross144 matrices must be binary uint8")
        if np.any((self.hx @ self.hz.T) & 1):
            raise ValueError("Gross144 CSS checks do not commute")
        if _gf2_rank(self.hx) != 66 or _gf2_rank(self.hz) != 66:
            raise ValueError("Gross144 check rank must be 66 in each sector")
        if np.any((self.lx @ self.hz.T) & 1) or np.any((self.lz @ self.hx.T) & 1):
            raise ValueError("Gross144 logical representatives do not commute with checks")
        if not np.array_equal((self.lx @ self.lz.T) & 1, np.eye(12, dtype=np.uint8)):
            raise ValueError("Gross144 logical bases are not paired")
        if not all(int(weight) == 6 for weight in self.hx.sum(axis=1)) or \
                not all(int(weight) == 6 for weight in self.hz.sum(axis=1)):
            raise ValueError("Gross144 checks must all have degree six")

    def hashes(self) -> dict[str, str]:
        return {
            name: hashlib.sha256(np.ascontiguousarray(matrix).tobytes()).hexdigest()
            for name, matrix in (("Hx", self.hx), ("Hz", self.hz),
                                 ("Lx", self.lx), ("Lz", self.lz))
        }

    def graph(self, error_type: ErrorType) -> tuple[Graph, tuple[tuple[int, ...], ...]]:
        """Return the fixed FPGA Tanner graph for one Pauli-error sector.

        ``X`` errors are detected by ``Hz`` and their logical class is read
        with ``Lz``; ``Z`` errors use the dual ``Hx``/``Lx`` pair.
        """

        if error_type == "X":
            check, signatures = self.hz, self.lz
        elif error_type == "Z":
            check, signatures = self.hx, self.lx
        else:
            raise ValueError("error_type must be X or Z")
        graph = Graph.from_matrix(check.tolist())
        group = _translation_group(graph, ell=GROSS144_PARAMETERS["ell"],
                                   m=GROSS144_PARAMETERS["m"])
        return dataclass_replace(graph, group=group), tuple(
            tuple(int(bit) for bit in row) for row in signatures
        )


def build_gross144() -> Gross144Code:
    """Construct and validate the public ``[[144,12,12]]`` Gross code."""

    ell, m = GROSS144_PARAMETERS["ell"], GROSS144_PARAMETERS["m"]
    identity_ell = np.eye(ell, dtype=np.uint8)
    identity_m = np.eye(m, dtype=np.uint8)
    x = tuple(np.kron(np.roll(identity_ell, shift, axis=1), identity_m) for shift in range(ell))
    y = tuple(np.kron(identity_ell, np.roll(identity_m, shift, axis=1)) for shift in range(m))
    a = (x[3] ^ y[1] ^ y[2]).astype(np.uint8, copy=False)
    b = (y[3] ^ x[1] ^ x[2]).astype(np.uint8, copy=False)
    hx = np.concatenate((a, b), axis=1)
    hz = np.concatenate((b.T, a.T), axis=1)
    lz = _logical_quotient_basis(hx, hz)
    lx_unpaired = _logical_quotient_basis(hz, hx)
    pairing = (lx_unpaired @ lz.T) & 1
    lx = (_gf2_inverse(pairing) @ lx_unpaired) & 1
    code = Gross144Code(
        hx.astype(np.uint8, copy=False), hz.astype(np.uint8, copy=False),
        lx.astype(np.uint8, copy=False), lz.astype(np.uint8, copy=False),
    )
    code.validate()
    return code


def write_gross144_artifacts(directory: str | Path) -> dict[str, str]:
    """Materialize deterministic ``.npy`` artifacts and return file hashes."""

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    code = build_gross144()
    for name, matrix in (("Hx", code.hx), ("Hz", code.hz), ("Lx", code.lx), ("Lz", code.lz)):
        np.save(target / f"{name}.npy", matrix, allow_pickle=False)
    return {
        name: _sha256_file(target / f"{name}.npy")
        for name in ("Hx", "Hz", "Lx", "Lz")
    }


@dataclass(frozen=True)
class Gross144DecoderConfig:
    """Initial frozen FPGA-equivalent configuration for this code image."""

    name: str = "fixed_point_fpga_s1_s1a_gari_relay_gross144_v5"
    physical_error_rate: float = 0.004
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
        if self.warmup_iterations != 2:
            raise ValueError("FPGA warm-up must be exactly two iterations")
        if self.compressed_minsum_iterations < 0 or self.automorphism_minsum_iterations < 0 or \
                self.gari_iterations < 0 or self.relay_iterations < 0:
            raise ValueError("iteration counts must be non-negative")
        if self.relay_legs != 4:
            raise ValueError("FPGA release requires exactly four Relay legs")
        if self.relay_dither_amplitude < 1 or self.relay_dither_amplitude > 15:
            raise ValueError("Relay dither amplitude must fit the fixed message range")
        if any(value < 0 or value >= (1 << 32) for value in (
            self.relay_dither_lfsr_seed, self.relay_dither_lfsr_polynomial,
            self.relay_dither_salt,
        )):
            raise ValueError("Relay dither LFSR values must be unsigned 32-bit")
        if self.relay_dither_lfsr_polynomial == 0:
            raise ValueError("Relay dither LFSR polynomial must be non-zero")
        if self.llr_scale < 1:
            raise ValueError("LLR scale must be positive")

    def digest(self) -> str:
        material = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class Gross144DecodeResult:
    accepted: bool
    correction: tuple[int, ...]
    predicted_logicals: tuple[int, ...]
    input_syndrome: tuple[int, ...]
    final_stage: str
    iterations: int
    check_updates: int
    reason: str
    workflow: ReleaseWorkflowResult | None = None


class Gross144FpgaAdapter:
    """One syndrome-only adapter for the fixed-point FPGA decoder workflow."""

    def __init__(self, code: Gross144Code | None = None, *,
                 config: Gross144DecoderConfig | None = None):
        self.code = code or build_gross144()
        self.code.validate()
        self.config = config or Gross144DecoderConfig()
        self.config.validate()
        self._graphs: dict[ErrorType, Graph] = {}
        self._logical_signatures: dict[ErrorType, tuple[tuple[int, ...], ...]] = {}
        self._gari_images: dict[ErrorType, GariGraph] = {}
        self._caches: dict[ErrorType, Stage2Cache] = {}
        self._automorphism_selections: dict[ErrorType, AutomorphismSelection] = {}
        for error_type in ("X", "Z"):
            graph, signatures = self.code.graph(error_type)
            self._graphs[error_type] = graph
            self._logical_signatures[error_type] = signatures
            self._gari_images[error_type] = GariGraph.from_decoder_graph(
                graph, check_types=("D_X",) * len(graph.checks),
                original_graph=None, logical_signatures=signatures,
            )
            self._caches[error_type] = Stage2Cache()
            # This selection depends only on the frozen Tanner graph and its
            # declared translations.  Compile it once, just as the FPGA image
            # contains a concrete four-action table, rather than spending
            # per-shot decoder time rediscovering the same selection.
            self._automorphism_selections[error_type] = select_decorrelated_group_trials(
                graph, max_iterations=self.config.automorphism_minsum_iterations,
            )
        llr = math.log((1.0 - self.config.physical_error_rate) /
                       self.config.physical_error_rate)
        self._prior = tuple(llr for _ in range(self.code.n))

    @property
    def decoder_configuration_hash(self) -> str:
        return self.config.digest()

    def decode(self, syndrome: Sequence[int], *, error_type: ErrorType) -> Gross144DecodeResult:
        """Decode detector-visible syndrome data; no truth/error vector enters here."""

        if error_type not in self._graphs:
            raise ValueError("error_type must be X or Z")
        target = tuple(int(bit) for bit in syndrome)
        base = self._graphs[error_type]
        if len(target) != len(base.checks) or any(bit not in (0, 1) for bit in target):
            raise ValueError("Gross144 syndrome width/value mismatch")
        signatures = self._logical_signatures[error_type]
        if not any(target):
            return Gross144DecodeResult(
                True, (0,) * self.code.n, (0,) * self.code.k, target,
                "S0", 0, 0, "zero syndrome", None,
            )
        active_graph = dataclass_replace(base, syndrome=target)
        gari = self._gari_images[error_type].with_syndrome(
            target, original_graph=active_graph,
        )
        workflow = run_release_workflow(
            active_graph, self._prior, syndrome=target,
            config=self._workflow_config(signatures, target),
            gari=gari, stage2_cache=self._caches[error_type],
            automorphism_selection=self._automorphism_selections[error_type],
        )
        candidate = workflow.controller.selected_candidate
        if candidate is None:
            return Gross144DecodeResult(
                False, (0,) * self.code.n, (0,) * self.code.k, target, "defer",
                _workflow_iterations(workflow), workflow.controller.total_work,
                workflow.controller.reason or "all FPGA stages deferred", workflow,
            )
        correction = tuple(int(bit) for bit in candidate.correction)
        predicted = _logical_bits(signatures, correction)
        return Gross144DecodeResult(
            True, correction, predicted, target, candidate.source_stage,
            _workflow_iterations(workflow), workflow.controller.total_work, "", workflow,
        )

    def _workflow_config(
        self,
        signatures: tuple[tuple[int, ...], ...],
        syndrome: Sequence[int],
    ) -> ReleaseWorkflowConfig:
        relay_perturbations = _gross144_relay_perturbations(
            self.code.n, syndrome=syndrome, amplitude=self.config.relay_dither_amplitude,
            lfsr_seed=self.config.relay_dither_lfsr_seed,
            lfsr_polynomial=self.config.relay_dither_lfsr_polynomial,
            salt=self.config.relay_dither_salt,
        )
        relay = RelayConfig(tuple(
            RelayLegConfig(leg_id, perturbation=relay_perturbations[leg_id],
                           max_iterations=self.config.relay_iterations)
            for leg_id in range(self.config.relay_legs)
        ), quorum=1)
        stage2 = Stage2IntegrationConfig(
            max_iterations=self.config.gari_iterations,
            logical_signatures=signatures,
        )
        return ReleaseWorkflowConfig(
            graph_type=GraphType.STATIC_RECOVERY,
            warmup=WarmupConfig(
                warmup_iterations=self.config.warmup_iterations,
                minsum_iterations=self.config.compressed_minsum_iterations,
                llr_scale=self.config.llr_scale,
            ),
            automorphism_iterations=self.config.automorphism_minsum_iterations,
            stage2=stage2,
            relay=relay,
            logical_signatures=signatures,
            total_work_budget=100_000,
        )


def _translation_group(graph: Graph, *, ell: int, m: int) -> GroupActionMetadata:
    """Declare the analytic ``Z_ell x Z_m`` translations; never search them."""

    block = ell * m
    if graph.num_variables != 2 * block or len(graph.checks) != block:
        raise ValueError("Gross144 translation action received the wrong graph shape")

    def coordinate_action(delta_x: int, delta_y: int, count: int) -> tuple[int, ...]:
        return tuple(
            (((index // m + delta_x) % ell) * m + ((index % m + delta_y) % m))
            for index in range(count)
        )

    variable_actions: list[tuple[int, ...]] = []
    check_actions: list[tuple[int, ...]] = []
    for delta_x in range(ell):
        for delta_y in range(m):
            coord = coordinate_action(delta_x, delta_y, block)
            variable_actions.append(tuple(coord[index % block] + (index // block) * block
                                          for index in range(2 * block)))
            check_actions.append(coord)
    group = GroupActionMetadata(tuple(variable_actions), tuple(check_actions))
    for variable, checks in zip(group.variable_permutations, group.check_permutations):
        if not graph.preserves_graph(variable, checks):
            raise ValueError("Gross144 declared translation is not a graph automorphism")
    return group


def _logical_quotient_basis(check: np.ndarray, stabilizer: np.ndarray) -> np.ndarray:
    """Pick a deterministic basis of ``ker(check) / rowspace(stabilizer)``."""

    candidates = _gf2_nullspace(check)
    selected = _independent_extension(stabilizer, candidates)
    if len(selected) != 12:
        raise ValueError(f"expected 12 Gross144 logicals, found {len(selected)}")
    return np.asarray(selected, dtype=np.uint8)


def _independent_extension(base: np.ndarray, candidates: np.ndarray) -> list[np.ndarray]:
    accumulated = np.asarray(base, dtype=np.uint8).copy() & 1
    rank = _gf2_rank(accumulated)
    selected: list[np.ndarray] = []
    for candidate in candidates:
        expanded = np.vstack((accumulated, candidate))
        next_rank = _gf2_rank(expanded)
        if next_rank > rank:
            selected.append(np.asarray(candidate, dtype=np.uint8).copy())
            accumulated, rank = expanded, next_rank
    return selected


def _gf2_nullspace(matrix: np.ndarray) -> np.ndarray:
    reduced, pivots = _gf2_rref(matrix)
    width = reduced.shape[1]
    free = [column for column in range(width) if column not in set(pivots)]
    vectors: list[np.ndarray] = []
    for column in free:
        vector = np.zeros(width, dtype=np.uint8)
        vector[column] = 1
        for row, pivot in enumerate(pivots):
            vector[pivot] = reduced[row, column]
        vectors.append(vector)
    return np.asarray(vectors, dtype=np.uint8)


def _gf2_inverse(matrix: np.ndarray) -> np.ndarray:
    source = np.asarray(matrix, dtype=np.uint8) & 1
    if source.ndim != 2 or source.shape[0] != source.shape[1]:
        raise ValueError("GF(2) inverse requires a square matrix")
    size = source.shape[0]
    augmented = np.concatenate((source.copy(), np.eye(size, dtype=np.uint8)), axis=1)
    row = 0
    for column in range(size):
        pivots = np.flatnonzero(augmented[row:, column])
        if not len(pivots):
            raise ValueError("GF(2) matrix is singular")
        pivot = row + int(pivots[0])
        if pivot != row:
            augmented[[row, pivot]] = augmented[[pivot, row]]
        for other in range(size):
            if other != row and augmented[other, column]:
                augmented[other] ^= augmented[row]
        row += 1
    return augmented[:, size:]


def _gf2_rank(matrix: np.ndarray) -> int:
    return len(_gf2_rref(matrix)[1])


def _gf2_rref(matrix: np.ndarray) -> tuple[np.ndarray, tuple[int, ...]]:
    work = np.asarray(matrix, dtype=np.uint8).copy() & 1
    if work.ndim != 2:
        raise ValueError("GF(2) matrix must be two dimensional")
    row = 0
    pivots: list[int] = []
    for column in range(work.shape[1]):
        pivot_rows = np.flatnonzero(work[row:, column])
        if not len(pivot_rows):
            continue
        pivot = row + int(pivot_rows[0])
        if pivot != row:
            work[[row, pivot]] = work[[pivot, row]]
        for other in range(work.shape[0]):
            if other != row and work[other, column]:
                work[other] ^= work[row]
        pivots.append(column)
        row += 1
        if row == work.shape[0]:
            break
    return work, tuple(pivots)


def _logical_bits(signatures: Sequence[Sequence[int]], correction: Sequence[int]) -> tuple[int, ...]:
    return tuple(sum(int(bit) * int(value) for bit, value in zip(signature, correction)) & 1
                 for signature in signatures)


def _gross144_relay_perturbations(
    num_variables: int,
    *,
    syndrome: Sequence[int],
    amplitude: int,
    lfsr_seed: int,
    lfsr_polynomial: int,
    salt: int,
) -> tuple[tuple[int, ...], ...]:
    """Syndrome-seeded, reproducible Relay masks for the four FPGA legs.

    Leg zero is the unmodified checkpoint.  The other three apply fixed
    SplitMix64 sign masks generated from a 32-bit LFSR fold of the received
    syndrome.  Both the LFSR and the three legs are fixed in the decoder
    configuration.  The seed depends only on detector-visible syndrome bits,
    never physical faults or logical truth, and can be generated in hardware
    with one XOR/shift step per syndrome bit.
    """

    if amplitude < 1 or amplitude > 15 or any(int(bit) not in (0, 1) for bit in syndrome):
        raise ValueError("invalid Gross144 Relay dither configuration")

    def splitmix64(value: int) -> int:
        value = (value + 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & ((1 << 64) - 1)
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & ((1 << 64) - 1)
        return value ^ (value >> 31)

    state = int(lfsr_seed) & 0xFFFFFFFF
    polynomial = int(lfsr_polynomial) & 0xFFFFFFFF
    for bit in syndrome:
        feedback = (state ^ int(bit)) & 1
        state >>= 1
        if feedback:
            state ^= polynomial
    root = state ^ (int(salt) & 0xFFFFFFFF)
    masks = tuple(
        tuple(amplitude if splitmix64(root + leg_id + variable) & 1 else -amplitude
              for variable in range(num_variables))
        for leg_id in range(1, 4)
    )
    return ((0,) * num_variables, *masks)


def _workflow_iterations(workflow: ReleaseWorkflowResult) -> int:
    """Return diagnostic sweeps performed before the final stage decision."""

    iterations = 0
    if workflow.hybrid is not None:
        iterations += len(workflow.hybrid.warmup.trace) + workflow.hybrid.minsum.iterations
    if workflow.automorphism is not None:
        iterations += sum(trial.iterations for trial in workflow.automorphism.trials)
    if workflow.stage2 is not None and workflow.stage2.gari_result is not None:
        iterations += workflow.stage2.gari_result.iterations
    if workflow.relay is not None and workflow.relay.legs:
        check_count = len(workflow.hybrid.warmup.edge_messages) \
            if workflow.hybrid is not None else 1
        if check_count:
            iterations += max(leg.work for leg in workflow.relay.legs) // check_count
    return iterations


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
