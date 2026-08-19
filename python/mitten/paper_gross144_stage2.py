"""Exact, quotient-compressed streamed S2 for the public Gross144 DEM.

The paper's second-stage input is the full 1,728-detector correlated DEM,
not the 936-detector S1 projection.  Its 72 spatial translations are exact.
This module stores 24 detector-time templates and streams the decoder in time
order.  Faults live for at most four detector-time slices, so posterior state
is a bounded ring rather than a 68k-entry RAM.

The runtime is deliberately detector-only.  It executes from the quotient
image, not from the 68k-variable host graph: only the four-slice posterior
ring, a one-bit candidate bitmap, and the 1,728-bit syndrome input are live.
Logical observables are accumulated through the compact dictionary only after
a fault retires.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from .fixed_point import saturating_add
from .graph_model import Graph
from .gross144 import build_gross144
from .hybrid_warmup import quantize_llr
from .paper_gross144 import (
    LOGICALS,
    PAPER_FIXTURE_DIR,
    _FIXTURE_SHA256,
    _fixture_path,
    _p_token,
    _sha256_bytes,
)
from .paper_gross144_component_templates import _add_coordinates
from .wide_minsum import WideMinSumConfig, _wide_check_update


GROUP_ORDER = 72
CHECKS_PER_SLICE = 72
SLICES = 24
POSTERIOR_BITS = 11
_BRIDGED_COMPLETION_REASON = "logical-neutral bridged degree-1/2 completion"


@dataclass(frozen=True)
class PaperGross144Stage2Layout:
    """Frozen full correlated DEM plus temporal-fault lifetime metadata."""

    basis: str
    physical_error_rate: float
    circuit: object
    graph: Graph
    prior_llr: tuple[float, ...]
    logical_signatures: tuple[tuple[int, ...], ...]
    fault_start_slice: tuple[int, ...]
    fault_end_slice: tuple[int, ...]
    fixture_path: Path
    fixture_sha256: str
    circuit_sha256: str
    detector_error_model_sha256: str

    @property
    def max_check_degree(self) -> int:
        return max(len(check.neighbors) for check in self.graph.checks)

    @property
    def max_fault_span(self) -> int:
        return max(end - start for start, end in zip(
            self.fault_start_slice, self.fault_end_slice,
        ))


@dataclass(frozen=True)
class StreamedS2TemplateImage:
    """Topology and residency contract for one exact full-DEM S2 image."""

    basis: str
    physical_error_rate: float
    variables: int
    checks: int
    edges: int
    group_order: int
    time_slices: int
    variable_orbits: int
    detector_time_templates: tuple[tuple[tuple[int, int], ...], ...]
    orbit_bank_colors: tuple[int, ...]
    banked_detector_time_templates: tuple[
        tuple[tuple[tuple[int, int, int, int], ...], ...], ...
    ]
    orbit_prior_llr: tuple[int, ...]
    orbit_start_slice: tuple[int, ...]
    orbit_end_slice: tuple[int, ...]
    logical_mask_dictionary: tuple[int, ...]
    logical_label_templates: tuple[tuple[int, ...], ...]
    orbit_logical_label_template_ids: tuple[int, ...]
    neutral_completion_parents: tuple[int, ...]
    neutral_completion_faults: tuple[int, ...]
    neutral_completion_order: tuple[int, ...]
    neutral_component_by_check: tuple[int, ...]
    neutral_bridge_faults: tuple[int, ...]
    neutral_bridge_vectors: tuple[int, ...]
    neutral_bridge_combinations: tuple[int, ...]
    max_template_degree: int
    max_banked_cycles: int
    max_live_variables: int
    serialized_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "PAPER-GROSS144-S2-STREAMED-TEMPLATE",
            "version": 4,
            "basis": self.basis,
            "p": self.physical_error_rate,
            "variables": self.variables,
            "checks": self.checks,
            "edges": self.edges,
            "group_order": self.group_order,
            "time_slices": self.time_slices,
            "variable_orbits": self.variable_orbits,
            "variable_indexing": "orbit_major_z12x6",
            # Immutable orbit attributes are required by both the causal
            # S2 stream and the SDRAM-backed Relay initializer. Earlier JSON
            # artifacts carried these values only inside the digest, which
            # made the serialized image impossible to execute alone.
            "orbit_prior_llr": list(self.orbit_prior_llr),
            "orbit_start_slice": list(self.orbit_start_slice),
            "orbit_end_slice": list(self.orbit_end_slice),
            "detector_time_templates": [list(map(list, row))
                                        for row in self.detector_time_templates],
            "posterior_banking": {
                "bank_count": 4,
                "orbit_bank_colors": list(self.orbit_bank_colors),
                "bank_rule": "(orbit_color + variable_x_coordinate) mod 4",
                "max_cycles_per_check": self.max_banked_cycles,
                "detector_time_templates": [
                    [list(map(list, cycle)) for cycle in row]
                    for row in self.banked_detector_time_templates
                ],
            },
            "streaming": {
                "schedule": "causal detector-time sweep",
                "max_fault_span_slices": 3,
                "max_live_variables": self.max_live_variables,
                "edge_message_ram": "none; each check is visited once per pass",
                "logical_projection": {
                    "format": "dictionary-coded 12-bit observable mask",
                    "dictionary": list(self.logical_mask_dictionary),
                    "label_bits": max(1, (len(self.logical_mask_dictionary) - 1).bit_length()),
                    "label_templates": [list(template) for template in self.logical_label_templates],
                    "orbit_label_template_ids": list(self.orbit_logical_label_template_ids),
                    "read_schedule": "one orbit-template id then one label/fault at retirement",
                },
                "neutral_completion": {
                    "format": "logical-neutral bridged degree-1/2 spanning forest",
                    "boundary_node": self.checks,
                    "parents": list(self.neutral_completion_parents),
                    "faults": list(self.neutral_completion_faults),
                    "traversal": list(self.neutral_completion_order),
                    "component_by_check": list(self.neutral_component_by_check),
                    "bridge_faults": list(self.neutral_bridge_faults),
                    "bridge_vectors": list(self.neutral_bridge_vectors),
                    "bridge_combinations": list(self.neutral_bridge_combinations),
                },
            },
            "serialized_sha256": self.serialized_sha256,
        }


@dataclass(frozen=True)
class StreamedS2Config:
    """One-pass S2 arithmetic.  One pass keeps the hard 1k/s speed gate."""

    llr_scale: int = 4
    message_magnitude_bits: int = 5
    correction_shift: int = 3

    def minsum_config(self) -> WideMinSumConfig:
        config = WideMinSumConfig(
            max_iterations=1,
            message_magnitude_bits=self.message_magnitude_bits,
            correction_shift=self.correction_shift,
        )
        config.validate()
        if self.llr_scale < 1:
            raise ValueError("S2 LLR scale must be positive")
        return config


@dataclass(frozen=True)
class StreamedS2Result:
    accepted: bool
    correction: tuple[int, ...] | None
    correction_weight: int
    predicted_logicals: tuple[int, ...]
    final_syndrome: tuple[int, ...]
    checks_processed: int
    max_live_variables: int
    reason: str


def _relay_prior_cost(priors: np.ndarray, correction: np.ndarray) -> int:
    """Return the fixed-point constant-free error cost of a hard candidate.

    All portfolio images decode the same detector word.  Their integer prior
    LLRs are therefore a bounded, truth-free likelihood proxy for selecting
    between their syndrome-exact corrections.  Widen before the dot product:
    the full S2R image is large enough to overflow a 32-bit accumulator.
    """

    return int(np.dot(priors.astype(np.int64), correction.astype(np.int64)))


def _relay_lite_gamma_q(*, sets: int, variables: int) -> np.ndarray:
    """Native Relay explicit gamma stream, quantized with Rust/C semantics."""

    variable = np.arange(variables, dtype=np.uint64)[None, :]
    relay_set = np.arange(sets, dtype=np.uint64)[:, None] + np.uint64(1)
    values = (
        np.uint64(43_091) +
        relay_set * np.uint64(0x9E3779B97F4A7C15) +
        variable * np.uint64(0xD1B54A32D192ED03)
    )
    values ^= values >> np.uint64(30)
    values *= np.uint64(0xBF58476D1CE4E5B9)
    values ^= values >> np.uint64(27)
    values *= np.uint64(0x94D049BB133111EB)
    values ^= values >> np.uint64(31)
    unit = (values >> np.uint64(11)).astype(np.float64) * (1.0 / (1 << 53))
    gamma = -0.24 + (0.66 + 0.24) * unit
    return np.trunc(gamma * 128.0).astype(np.int32)


@dataclass(frozen=True)
class StreamedS2RelayConfig:
    """Fixed-point flooding Relay tail kept behind the fast causal S2 pass.

    The tail has one signed check-message image and two full posterior images,
    which belong in the GW2AR embedded SDRAM.  It intentionally does not alter
    the 18-BSRAM causal S2C overlay or its 1k/s service contract.
    """

    max_iterations: int = 20
    memory_weight_shift: int = 4  # retain 15/16 prior plus 1/16 posterior memory
    message_magnitude_bits: int = 6
    # Zero preserves terminal-only completion.  A positive value screens the
    # exact logical-neutral forest at and after that Relay iteration.
    neutral_completion_start_iteration: int = 1
    neutral_completion_interval: int = 1
    # A deterministic, zero-mean fixed-prior dither is a cheap portfolio
    # axis for rare wrong-coset tails. It is derived from the quotient-expanded
    # fault index, so hardware needs only a counter/xorshift bit; it stores no
    # per-fault side table and never sees circuit logical observables.
    prior_dither_amplitude: int = 0
    prior_dither_seed: int = 0
    # Optional Relay-style disordered memory strengths.  Values are fixed
    # point Q7 gamma numerators (gamma * 128), matching the native i32
    # backend's data scale.  None keeps the original scalar binary memory.
    memory_strength_low_q: int | None = None
    memory_strength_high_q: int | None = None
    memory_strength_seed: int = 0

    def validate(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("S2R iteration budget must be positive")
        if self.memory_weight_shift < 1:
            raise ValueError("S2R memory weight must be a proper binary fraction")
        if not 1 <= self.message_magnitude_bits <= 10:
            raise ValueError("S2R message magnitude width must be in [1, 10]")
        if self.neutral_completion_start_iteration < 0:
            raise ValueError("S2R neutral-completion start must be non-negative")
        if self.neutral_completion_interval < 1:
            raise ValueError("S2R neutral-completion interval must be positive")
        if not 0 <= self.prior_dither_amplitude <= 7:
            raise ValueError("S2R prior dither amplitude must be in [0, 7]")
        if not 0 <= self.prior_dither_seed < (1 << 32):
            raise ValueError("S2R prior dither seed must be a 32-bit unsigned value")
        if (self.memory_strength_low_q is None) != (self.memory_strength_high_q is None):
            raise ValueError("S2R memory-strength bounds must be both set or both None")
        if self.memory_strength_low_q is not None:
            if not -128 <= self.memory_strength_low_q <= 256 or \
                    not -128 <= self.memory_strength_high_q <= 256 or \
                    self.memory_strength_low_q >= self.memory_strength_high_q:
                raise ValueError("S2R memory-strength Q7 bounds are invalid")
        if not 0 <= self.memory_strength_seed < (1 << 32):
            raise ValueError("S2R memory-strength seed must be a 32-bit unsigned value")


@dataclass(frozen=True)
class StreamedS2RelayResult:
    """Detector-only result for the SDRAM-backed S2R candidate."""

    accepted: bool
    correction: tuple[int, ...] | None
    correction_weight: int
    prior_cost: int
    predicted_logicals: tuple[int, ...]
    final_syndrome: tuple[int, ...]
    iterations: int
    check_updates: int
    message_magnitude_peak: int
    neutral_completion_flips: int
    neutral_completion_attempts: int
    reason: str


def _raw_fixture_circuit(relay_root: Path | str, *, p: float, basis: str):
    import stim

    root = Path(relay_root)
    path = _fixture_path(root, p=p, basis=basis)
    raw = path.read_bytes()
    expected = _FIXTURE_SHA256[(p, basis)]
    digest = _sha256_bytes(raw)
    if digest != expected:
        raise ValueError("Relay fixture digest mismatch while compiling S2")
    return path, digest, stim.Circuit(raw.decode("utf-8"))


def load_paper_gross144_stage2_layout(
    relay_root: Path | str, *, p: float, basis: str,
) -> PaperGross144Stage2Layout:
    """Compile exact full correlated DEM. No errors are merged or dropped."""

    if basis not in ("X", "Z"):
        raise ValueError("basis must be X or Z")
    _p_token(p)
    path, fixture_sha, circuit = _raw_fixture_circuit(relay_root, p=p, basis=basis)
    dem = circuit.detector_error_model()
    if dem.num_detectors != SLICES * CHECKS_PER_SLICE or \
            dem.num_observables != LOGICALS:
        raise ValueError("paper full DEM detector/observable dimensions drifted")

    check_neighbors: list[list[int]] = [[] for _ in range(dem.num_detectors)]
    priors: list[float] = []
    starts: list[int] = []
    ends: list[int] = []
    logical_columns: list[list[int]] = [[] for _ in range(LOGICALS)]
    for instruction in dem.flattened():
        if instruction.type != "error":
            continue
        arguments = instruction.args_copy()
        if len(arguments) != 1 or not 0.0 < arguments[0] < 0.5:
            raise ValueError("paper full DEM contains unsupported error prior")
        variable = len(priors)
        detectors: list[int] = []
        for target in instruction.targets_copy():
            if target.is_relative_detector_id():
                detector = int(target.val)
                check_neighbors[detector].append(variable)
                detectors.append(detector)
            elif target.is_logical_observable_id():
                logical_columns[int(target.val)].append(variable)
            elif target.is_separator():
                raise ValueError("paper full DEM unexpectedly contains decomposed hyperedges")
            else:
                raise ValueError("paper full DEM contains unsupported target")
        if not detectors:
            raise ValueError("paper full DEM contains logical-only fault")
        priors.append(math.log((1.0 - arguments[0]) / arguments[0]))
        slice_indices = [detector // CHECKS_PER_SLICE for detector in detectors]
        starts.append(min(slice_indices))
        ends.append(max(slice_indices))

    graph = Graph.from_neighbors(len(priors), check_neighbors)
    expected_variables = 67_824 if basis == "X" else 67_752
    expected_edges = 391_464 if basis == "X" else 391_320
    if graph.num_variables != expected_variables or graph.edge_count() != expected_edges or \
            max(len(check.neighbors) for check in graph.checks) != 242:
        raise ValueError("paper full DEM topology drifted from frozen fixture")
    if max(end - start for start, end in zip(starts, ends)) != 3:
        raise ValueError("paper full DEM temporal span no longer fits four-slice stream")

    # Full DEM logical columns contain thousands of faults.  Convert once so
    # this remains linear in the 67k-variable image rather than quadratic.
    logical_sets = tuple(set(columns) for columns in logical_columns)
    signatures = tuple(
        tuple(int(variable in columns) for variable in range(graph.num_variables))
        for columns in logical_sets
    )
    return PaperGross144Stage2Layout(
        basis=basis,
        physical_error_rate=p,
        circuit=circuit,
        graph=graph,
        prior_llr=tuple(priors),
        logical_signatures=signatures,
        fault_start_slice=tuple(starts),
        fault_end_slice=tuple(ends),
        fixture_path=path,
        fixture_sha256=fixture_sha,
        circuit_sha256=_sha256_bytes(str(circuit).encode("utf-8")),
        detector_error_model_sha256=_sha256_bytes(str(dem).encode("utf-8")),
    )


def _translation_permutations(
    layout: PaperGross144Stage2Layout,
) -> dict[int, tuple[int, ...]]:
    """Map spatial destination coordinate to exact detector permutation."""

    error_type = "Z" if layout.basis == "X" else "X"
    base_graph, _ = build_gross144().graph(error_type)
    actions: dict[int, tuple[int, ...]] = {}
    for base_checks in base_graph.group.check_permutations:
        destination = int(base_checks[0])
        mapped = tuple(
            time * CHECKS_PER_SLICE + int(base_checks[check])
            for time in range(SLICES)
            for check in range(CHECKS_PER_SLICE)
        )
        if destination in actions:
            raise ValueError("duplicate Gross translation coordinate")
        actions[destination] = mapped
    if set(actions) != set(range(GROUP_ORDER)):
        raise ValueError("Gross translation group no longer covers spatial coordinates")
    return actions


def _fault_response_keys(
    layout: PaperGross144Stage2Layout,
) -> tuple[dict[tuple[tuple[int, ...], float], int], tuple[tuple[int, ...], ...]]:
    masks: list[list[int]] = [[] for _ in range(layout.graph.num_variables)]
    for check in layout.graph.checks:
        for variable in check.neighbors:
            masks[variable].append(check.id)
    keys: dict[tuple[tuple[int, ...], float], int] = {}
    frozen_masks: list[tuple[int, ...]] = []
    for variable, mask in enumerate(masks):
        key = (tuple(mask), round(layout.prior_llr[variable], 12))
        if key in keys:
            raise ValueError("paper full DEM has ambiguous translation response")
        keys[key] = variable
        frozen_masks.append(tuple(mask))
    return keys, tuple(frozen_masks)


def _translated_variable(
    variable: int,
    *, detector_permutation: Sequence[int],
    masks: Sequence[Sequence[int]],
    priors: Sequence[float],
    by_response: dict[tuple[tuple[int, ...], float], int],
) -> int:
    translated = tuple(sorted(detector_permutation[detector] for detector in masks[variable]))
    try:
        return by_response[(translated, round(priors[variable], 12))]
    except KeyError as exc:
        raise ValueError("paper full DEM translation left frozen S2 image") from exc


def _four_bank_schedule(
    templates: Sequence[Sequence[tuple[int, int]]], *, orbit_count: int, basis: str,
) -> tuple[tuple[int, ...], tuple[tuple[tuple[tuple[int, int, int, int], ...], ...], ...]]:
    """Deterministic four-bank packing for high-degree S2 check rows.

    Unlike S1, the degree-242 rows do not require a mathematically-minimal
    colouring claim.  Deterministic coordinate descent emits a conflict-free
    schedule; cycle counts below use emitted beats, never a degree lower-bound.
    """

    bank_count = 4
    # A colour affects only the handful of template edges owned by its orbit.
    # Coordinate descent therefore finds a much flatter high-degree S2 bank
    # schedule than orbit-id modulo four, without changing a graph edge.
    contributions: list[list[tuple[int, int]]] = [[] for _ in range(orbit_count)]
    for time, row in enumerate(templates):
        for orbit, anchor in row:
            contributions[orbit].append((time, anchor // 6))
    colors = [orbit % bank_count for orbit in range(orbit_count)]
    occupancy = [[0] * bank_count for _ in templates]
    for orbit, entries in enumerate(contributions):
        for time, anchor_x in entries:
            occupancy[time][(colors[orbit] + anchor_x) % bank_count] += 1

    def objective() -> tuple[int, int]:
        return (
            max(max(row) for row in occupancy),
            sum(value * value for row in occupancy for value in row),
        )

    rng = random.Random(0x533258 if basis == "X" else 0x53325A)
    current = objective()
    for _sweep in range(96):
        changed = False
        order = list(range(orbit_count))
        rng.shuffle(order)
        for orbit in order:
            old = colors[orbit]
            for time, anchor_x in contributions[orbit]:
                occupancy[time][(old + anchor_x) % bank_count] -= 1
            best_color = old
            best_objective: tuple[int, int] | None = None
            for candidate in range(bank_count):
                for time, anchor_x in contributions[orbit]:
                    occupancy[time][(candidate + anchor_x) % bank_count] += 1
                candidate_objective = objective()
                for time, anchor_x in contributions[orbit]:
                    occupancy[time][(candidate + anchor_x) % bank_count] -= 1
                if best_objective is None or candidate_objective < best_objective:
                    best_color, best_objective = candidate, candidate_objective
            colors[orbit] = best_color
            for time, anchor_x in contributions[orbit]:
                occupancy[time][(best_color + anchor_x) % bank_count] += 1
            changed |= best_color != old
        updated = objective()
        if updated > current:  # pragma: no cover - local step is monotonic
            raise AssertionError("S2 bank optimizer regressed schedule")
        if not changed:
            break
        current = updated
    colors = tuple(colors)
    packed: list[tuple[tuple[tuple[int, int, int, int], ...], ...]] = []
    for row in templates:
        by_bank: list[list[tuple[int, int, int]]] = [[] for _ in range(bank_count)]
        for edge, (orbit, anchor) in enumerate(row):
            bank = (colors[orbit] + anchor // 6) % bank_count
            by_bank[bank].append((edge, orbit, anchor))
        beats: list[tuple[tuple[int, int, int, int], ...]] = []
        for beat in range(max(map(len, by_bank), default=0)):
            beats.append(tuple(
                (bank, by_bank[bank][beat][0], by_bank[bank][beat][1], by_bank[bank][beat][2])
                for bank in range(bank_count) if beat < len(by_bank[bank])
            ))
        if sorted((orbit, anchor) for beat in beats for _bank, _edge, orbit, anchor in beat) != \
                sorted(row):
            raise ValueError("S2 bank schedule lost a template edge")
        packed.append(tuple(beats))
    return colors, tuple(packed)


def _build_neutral_completion_tree(
    templates: Sequence[Sequence[tuple[int, int]]],
    *, orbit_count: int, dictionary: Sequence[int], labels: Sequence[int],
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """Build deterministic logical-neutral degree-1/2 forest in quotient IDs."""

    checks = len(templates) * GROUP_ORDER
    variables = orbit_count * GROUP_ORDER
    boundary = checks
    response: list[list[int]] = [[] for _ in range(variables)]
    for time, row in enumerate(templates):
        for coordinate in range(GROUP_ORDER):
            check = time * GROUP_ORDER + coordinate
            for orbit, anchor in row:
                response[orbit * GROUP_ORDER + _add_coordinates(anchor, coordinate)].append(check)
    if sum(map(len, response)) != sum(map(len, templates)) * GROUP_ORDER:
        raise AssertionError("S2 neutral completion response expansion drifted")

    parent_dsu = list(range(checks + 1))
    size_dsu = [1] * (checks + 1)

    def find(node: int) -> int:
        while parent_dsu[node] != node:
            parent_dsu[node] = parent_dsu[parent_dsu[node]]
            node = parent_dsu[node]
        return node

    adjacency: list[list[tuple[int, int]]] = [[] for _ in range(checks + 1)]
    for variable, nodes in enumerate(response):
        if len(nodes) not in (1, 2) or dictionary[labels[variable]] != 0:
            continue
        left, right = nodes[0], (boundary if len(nodes) == 1 else nodes[1])
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            continue
        if size_dsu[left_root] < size_dsu[right_root]:
            left_root, right_root = right_root, left_root
        parent_dsu[right_root] = left_root
        size_dsu[left_root] += size_dsu[right_root]
        adjacency[left].append((right, variable))
        adjacency[right].append((left, variable))

    parents = [-2] * (checks + 1)
    faults = [-1] * (checks + 1)
    traversal: list[int] = []
    for root in (boundary, *range(checks)):
        if parents[root] != -2:
            continue
        parents[root] = -1
        stack = [root]
        while stack:
            node = stack.pop()
            traversal.append(node)
            for neighbor, variable in adjacency[node]:
                if parents[neighbor] != -2:
                    continue
                parents[neighbor] = node
                faults[neighbor] = variable
                stack.append(neighbor)
    if any(parent == -2 for parent in parents) or len(traversal) != checks + 1:
        raise AssertionError("S2 neutral completion forest did not cover every check")
    return tuple(parents), tuple(faults), tuple(traversal)


def _build_neutral_completion_bridge(
    templates: Sequence[Sequence[tuple[int, int]]], *, orbit_count: int,
    dictionary: Sequence[int], labels: Sequence[int], parents: Sequence[int],
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """Compile logical-neutral links between detached completion-tree components.

    The degree-1/2 forest is deliberately acyclic, so it can leave disconnected
    components.  A residual with odd parity in one of those components cannot
    reach the boundary through the tree alone.  This compact GF(2) basis uses
    only zero-logical faults to bridge every reachable component-parity vector
    before the normal tree sweep.  It never changes the candidate logical word.
    """

    checks = len(templates) * GROUP_ORDER
    variables = orbit_count * GROUP_ORDER
    boundary = checks

    def root(node: int) -> int:
        while parents[node] >= 0:
            node = parents[node]
        return node

    boundary_root = root(boundary)
    detached_roots = sorted({root(check) for check in range(checks)
                             if root(check) != boundary_root})
    component_id = {component_root: index
                    for index, component_root in enumerate(detached_roots)}
    component_by_check = tuple(component_id.get(root(check), -1)
                               for check in range(checks))
    if not detached_roots:
        return component_by_check, (), (), ()

    response: list[list[int]] = [[] for _ in range(variables)]
    for time, row in enumerate(templates):
        for coordinate in range(GROUP_ORDER):
            check = time * GROUP_ORDER + coordinate
            for orbit, anchor in row:
                response[orbit * GROUP_ORDER + _add_coordinates(anchor, coordinate)].append(check)

    # A row-echelon basis is enough: every retained column is itself a
    # logical-neutral fault, while its combination word records how that
    # retained column reduces against earlier pivots.
    pivot_vectors: dict[int, tuple[int, int]] = {}
    bridge_faults: list[int] = []
    for variable, checks_for_fault in sorted(
            enumerate(response), key=lambda row: (len(row[1]), row[0]),
    ):
        if dictionary[labels[variable]] != 0:
            continue
        signature = 0
        for check in checks_for_fault:
            component = component_by_check[check]
            if component >= 0:
                signature ^= 1 << component
        if not signature:
            continue
        combination = 1 << len(bridge_faults)
        reduced = signature
        while reduced:
            pivot = reduced.bit_length() - 1
            prior = pivot_vectors.get(pivot)
            if prior is None:
                bridge_faults.append(variable)
                pivot_vectors[pivot] = (reduced, combination)
                break
            reduced ^= prior[0]
            combination ^= prior[1]

    # Solve from the most-significant pivot down, matching the echelon form.
    ordered = tuple(pivot_vectors[pivot]
                    for pivot in sorted(pivot_vectors, reverse=True))
    return (component_by_check, tuple(bridge_faults),
            tuple(vector for vector, _combination in ordered),
            tuple(combination for _vector, combination in ordered))


def compile_paper_gross144_stage2_templates(
    relay_root: Path | str, *, p: float, basis: str,
) -> StreamedS2TemplateImage:
    """Build exact spatial quotient and bounded-live-state S2 contract."""

    layout = load_paper_gross144_stage2_layout(relay_root, p=p, basis=basis)
    by_coordinate = _translation_permutations(layout)
    by_response, masks = _fault_response_keys(layout)

    unassigned = set(range(layout.graph.num_variables))
    orbit_by_variable: dict[int, int] = {}
    coordinate_by_variable: dict[int, int] = {}
    representatives: list[int] = []
    for orbit_id in range(layout.graph.num_variables):
        if not unassigned:
            break
        representative = min(unassigned)
        orbit: set[int] = set()
        for coordinate, permutation in by_coordinate.items():
            mapped = _translated_variable(
                representative, detector_permutation=permutation, masks=masks,
                priors=layout.prior_llr, by_response=by_response,
            )
            orbit.add(mapped)
            orbit_by_variable[mapped] = orbit_id
            coordinate_by_variable[mapped] = coordinate
        if len(orbit) != GROUP_ORDER:
            raise ValueError("paper full DEM S2 variable orbit is not free")
        unassigned.difference_update(orbit)
        representatives.append(representative)
    orbit_count = len(representatives)
    if orbit_count * GROUP_ORDER != layout.graph.num_variables:
        raise ValueError("paper full DEM S2 orbit count is inconsistent")

    reindexed = {
        variable: orbit_by_variable[variable] * GROUP_ORDER + coordinate_by_variable[variable]
        for variable in range(layout.graph.num_variables)
    }
    templates: list[tuple[tuple[int, int], ...]] = []
    for time in range(SLICES):
        row = layout.graph.checks[time * CHECKS_PER_SLICE]
        template = tuple(
            (orbit_by_variable[variable], coordinate_by_variable[variable])
            for variable in row.neighbors
        )
        templates.append(template)
        for coordinate in range(GROUP_ORDER):
            generated = {
                orbit * GROUP_ORDER + _add_coordinates(anchor, coordinate)
                for orbit, anchor in template
            }
            expected = {
                reindexed[variable]
                for variable in layout.graph.checks[time * CHECKS_PER_SLICE + coordinate].neighbors
            }
            if generated != expected:
                raise ValueError("S2 template does not reproduce exact full DEM check row")

    starts = tuple(layout.fault_start_slice[representative] for representative in representatives)
    ends = tuple(layout.fault_end_slice[representative] for representative in representatives)
    priors = tuple(quantize_llr(layout.prior_llr[representative], scale=4)
                   for representative in representatives)
    for variable, orbit in orbit_by_variable.items():
        representative = representatives[orbit]
        if layout.fault_start_slice[variable] != layout.fault_start_slice[representative] or \
                layout.fault_end_slice[variable] != layout.fault_end_slice[representative] or \
                round(layout.prior_llr[variable], 12) != round(layout.prior_llr[representative], 12):
            raise ValueError("S2 translation changed temporal lifetime or prior")

    # The final observable projection is not translation invariant (the
    # circuit frame changes at temporal boundaries).  It nevertheless has
    # only 82/71 distinct 12-bit masks in this fixture.  The label sequence
    # itself repeats by fault orbit (28 X patterns, 12 Z patterns), so store
    # one 72-label template per pattern plus one small template id/orbit.
    raw_masks = tuple(
        sum(signature[variable] << logical for logical, signature in enumerate(
            layout.logical_signatures
        ))
        for variable in range(layout.graph.num_variables)
    )
    dictionary = tuple(sorted(set(raw_masks)))
    label_by_mask = {mask: label for label, mask in enumerate(dictionary)}
    labels = [0] * layout.graph.num_variables
    for variable, address in reindexed.items():
        labels[address] = label_by_mask[raw_masks[variable]]
    if any(dictionary[labels[address]] != raw_masks[variable]
           for variable, address in reindexed.items()):
        raise ValueError("S2 compact logical-mask labels changed an observable")
    label_template_ids: list[int] = []
    label_templates: list[tuple[int, ...]] = []
    template_id_by_labels: dict[tuple[int, ...], int] = {}
    for orbit in range(orbit_count):
        template = tuple(labels[orbit * GROUP_ORDER:(orbit + 1) * GROUP_ORDER])
        template_id = template_id_by_labels.get(template)
        if template_id is None:
            template_id = len(label_templates)
            template_id_by_labels[template] = template_id
            label_templates.append(template)
        label_template_ids.append(template_id)
    neutral_parents, neutral_faults, neutral_order = _build_neutral_completion_tree(
        templates, orbit_count=orbit_count, dictionary=dictionary, labels=labels,
    )
    (neutral_component_by_check, neutral_bridge_faults, neutral_bridge_vectors,
     neutral_bridge_combinations) = _build_neutral_completion_bridge(
         templates, orbit_count=orbit_count, dictionary=dictionary, labels=labels,
         parents=neutral_parents,
     )

    max_live = max(
        GROUP_ORDER * sum(start <= time <= end for start, end in zip(starts, ends))
        for time in range(SLICES)
    )
    colors, banked = _four_bank_schedule(templates, orbit_count=orbit_count, basis=basis)
    payload = {
        "basis": basis,
        "p": p,
        "variables": layout.graph.num_variables,
        "checks": len(layout.graph.checks),
        "edges": layout.graph.edge_count(),
        "orbits": orbit_count,
        "templates": templates,
        "colors": colors,
        "starts": starts,
        "ends": ends,
        "priors": priors,
        "logical_mask_dictionary": dictionary,
        "logical_label_templates": label_templates,
        "orbit_logical_label_template_ids": label_template_ids,
        "neutral_completion_parents": neutral_parents,
        "neutral_completion_faults": neutral_faults,
        "neutral_completion_order": neutral_order,
        "neutral_component_by_check": neutral_component_by_check,
        "neutral_bridge_faults": neutral_bridge_faults,
        "neutral_bridge_vectors": neutral_bridge_vectors,
        "neutral_bridge_combinations": neutral_bridge_combinations,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    return StreamedS2TemplateImage(
        basis=basis,
        physical_error_rate=p,
        variables=layout.graph.num_variables,
        checks=len(layout.graph.checks),
        edges=layout.graph.edge_count(),
        group_order=GROUP_ORDER,
        time_slices=SLICES,
        variable_orbits=orbit_count,
        detector_time_templates=tuple(templates),
        orbit_bank_colors=colors,
        banked_detector_time_templates=banked,
        orbit_prior_llr=priors,
        orbit_start_slice=starts,
        orbit_end_slice=ends,
        logical_mask_dictionary=dictionary,
        logical_label_templates=tuple(label_templates),
        orbit_logical_label_template_ids=tuple(label_template_ids),
        neutral_completion_parents=neutral_parents,
        neutral_completion_faults=neutral_faults,
        neutral_completion_order=neutral_order,
        neutral_component_by_check=neutral_component_by_check,
        neutral_bridge_faults=neutral_bridge_faults,
        neutral_bridge_vectors=neutral_bridge_vectors,
        neutral_bridge_combinations=neutral_bridge_combinations,
        max_template_degree=max(map(len, templates)),
        max_banked_cycles=max(map(len, banked)),
        max_live_variables=max_live,
        serialized_sha256=digest,
    )


def streamed_s2_storage_plan(
    image: StreamedS2TemplateImage, *, bram_block_bits: int = 18_432,
) -> dict[str, int | float]:
    """On-chip working-set storage; immutable logical projection is streamed."""

    if bram_block_bits < 1:
        raise ValueError("BRAM block width must be positive")
    orbit_bits = max(1, (image.variable_orbits - 1).bit_length())
    template_edges = sum(len(row) for row in image.detector_time_templates)
    template_bits = template_edges * (orbit_bits + 7) + image.variable_orbits * (2 + 11 + 10) + \
        image.time_slices * 8
    explicit_topology_bits = image.edges * max(1, (image.variables - 1).bit_length())
    live_posterior_bits = image.max_live_variables * POSTERIOR_BITS
    # A retired hard decision is one bit.  Keeping this compact bitmap makes
    # the final residual replay exact without retaining a 68k posterior or an
    # edge-message image.
    correction_bitmap_bits = image.variables
    syndrome_bits = image.checks
    logical_label_bits = max(1, (len(image.logical_mask_dictionary) - 1).bit_length())
    logical_template_id_bits = max(1, (len(image.logical_label_templates) - 1).bit_length())
    logical_projection_bits = (
        len(image.logical_label_templates) * image.group_order * logical_label_bits +
        image.variable_orbits * logical_template_id_bits +
        len(image.logical_mask_dictionary) * LOGICALS
    )
    onchip_bits = template_bits + live_posterior_bits + syndrome_bits + correction_bitmap_bits
    return {
        "address_bits": max(1, (image.variables - 1).bit_length()),
        "explicit_topology_bits": explicit_topology_bits,
        "template_topology_bits": template_bits,
        "topology_compression_ratio": explicit_topology_bits / template_bits,
        "live_posterior_bits": live_posterior_bits,
        "correction_bitmap_bits": correction_bitmap_bits,
        "syndrome_bits": syndrome_bits,
        "edge_message_bits": 0,
        "onchip_working_bits": onchip_bits,
        "onchip_bram_blocks": math.ceil(onchip_bits / bram_block_bits),
        "immutable_logical_projection_bits": logical_projection_bits,
        "immutable_logical_projection_label_bits": logical_label_bits,
        "immutable_logical_projection_template_id_bits": logical_template_id_bits,
        "immutable_logical_projection_templates": len(image.logical_label_templates),
        "immutable_logical_projection_bram_blocks_if_resident": math.ceil(
            logical_projection_bits / bram_block_bits
        ),
    }


def streamed_s2_cycle_plan(
    image: StreamedS2TemplateImage, *, clock_hz: float = 45_000_000.0,
) -> dict[str, int | float]:
    """Conservative exact-residual four-bank cycle model; no overlap credited."""

    if clock_hz <= 0.0:
        raise ValueError("clock must be positive")
    check_cycles = sum(
        2 * len(row) + 2 for row in image.banked_detector_time_templates
    ) * GROUP_ORDER
    # At every fault lifetime boundary, one 7-bit projection label is read
    # and XORed into the 12-bit logical accumulator.  Prior rows fill four
    # posterior banks in parallel.  Both costs are explicit; no tail latency
    # is hidden behind the streaming claim.
    prior_fill_cycles = math.ceil(image.variables / 4)
    retirement_cycles = image.variables
    # Replay the emitted one-bit candidate bitmap through the same exact
    # quotient rows.  This gives a strict residual certificate before S2
    # accepts; it costs one four-bank read beat per template beat.
    syndrome_validation_cycles = sum(
        len(row) for row in image.banked_detector_time_templates
    ) * GROUP_ORDER
    cycles = check_cycles + prior_fill_cycles + retirement_cycles + syndrome_validation_cycles
    seconds = cycles / clock_hz
    return {
        "clock_hz": clock_hz,
        "passes": 1,
        "check_stream_cycles": check_cycles,
        "prior_fill_cycles": prior_fill_cycles,
        "logical_retirement_cycles": retirement_cycles,
        "syndrome_validation_cycles": syndrome_validation_cycles,
        "cycles_per_window": cycles,
        "microseconds_per_syndrome_round": seconds * 1e6 / 12,
        "syndrome_rounds_per_second": 12 / seconds,
    }


def streamed_s2_relay_storage_plan(
    image: StreamedS2TemplateImage,
    *, config: StreamedS2RelayConfig | None = None,
) -> dict[str, int]:
    """Dynamic S2R state for GW2AR embedded SDRAM, not BSRAM.

    The same quotient topology, correction bitmap, and logical-label ROM as
    S2C remain resident.  S2R adds a signed check-message image plus ping-pong
    posteriors; no expanded edge/topology image is stored.
    """

    config = config or StreamedS2RelayConfig()
    config.validate()
    message_bits = config.message_magnitude_bits + 1
    message_image_bits = image.edges * message_bits
    posterior_image_bits = image.variables * POSTERIOR_BITS
    packed_dynamic_bits = message_image_bits + 2 * posterior_image_bits
    # The cycle lower bound uses one SDRAM halfword per message/posterior
    # record.  Keep this implementation-ready layout separate from the
    # bit-packed state lower bound above.
    halfword_layout_bits = 16 * image.edges + 2 * 16 * image.variables
    # Two signed seven-bit Relay messages fit in one halfword.  The packed
    # layout is a concrete next transport contract; it is reported separately
    # until a sequential pair cache is placed in the S2R SDRAM controller.
    packed_message_halfwords = math.ceil(image.edges / 2)
    packed_message_halfword_layout_bits = 16 * packed_message_halfwords + \
        2 * 16 * image.variables
    # Physical S2R state is a simple contiguous 16-bit-record image.  These
    # offsets are shared with streamed_s2_relay_sdram_layout.sv: an S2R
    # controller may expand quotient topology on the fly, but it must never
    # need an expanded topology image in SDRAM.
    message_halfword_base = 0
    posterior_a_halfword_base = message_halfword_base + image.edges
    posterior_b_halfword_base = posterior_a_halfword_base + image.variables
    total_sdram_halfwords = posterior_b_halfword_base + image.variables
    completion_parent_bits = max(1, image.checks.bit_length())
    completion_fault_bits = max(1, (image.variables - 1).bit_length())
    completion_tree_bits = len(image.neutral_completion_order) * (
        completion_parent_bits + completion_fault_bits + completion_parent_bits
    )
    bridge_components = max(image.neutral_component_by_check, default=-1) + 1
    bridge_component_bits = max(1, bridge_components.bit_length())
    bridge_rank = len(image.neutral_bridge_faults)
    completion_bridge_bits = (
        image.checks * bridge_component_bits +
        bridge_rank * (completion_fault_bits + bridge_components + bridge_rank)
    )
    return {
        "message_bits": message_bits,
        "message_image_bits": message_image_bits,
        "posterior_image_bits_each": posterior_image_bits,
        "posterior_image_count": 2,
        "packed_dynamic_sdram_bits": packed_dynamic_bits,
        "packed_dynamic_sdram_bytes": math.ceil(packed_dynamic_bits / 8),
        "dynamic_sdram_bits": packed_dynamic_bits,
        "dynamic_sdram_bytes": math.ceil(packed_dynamic_bits / 8),
        "sdram_halfword_layout_bits": halfword_layout_bits,
        "sdram_halfword_layout_bytes": math.ceil(halfword_layout_bits / 8),
        "packed_message_halfwords": packed_message_halfwords,
        "packed_message_sdram_halfword_layout_bits": packed_message_halfword_layout_bits,
        "packed_message_sdram_halfword_layout_bytes": math.ceil(
            packed_message_halfword_layout_bits / 8
        ),
        "message_halfword_base": message_halfword_base,
        "posterior_a_halfword_base": posterior_a_halfword_base,
        "posterior_b_halfword_base": posterior_b_halfword_base,
        "total_sdram_halfwords": total_sdram_halfwords,
        "total_sdram_32bit_words": math.ceil(total_sdram_halfwords / 2),
        "neutral_completion_tree_bits": completion_tree_bits,
        "neutral_completion_tree_bram_blocks_reusing_s2c_live_state": math.ceil(
            completion_tree_bits / 18_432
        ),
        "neutral_completion_bridge_components": bridge_components,
        "neutral_completion_bridge_rank": bridge_rank,
        "neutral_completion_bridge_bits": completion_bridge_bits,
        "neutral_completion_bridge_bram_blocks_reusing_s2c_live_state": math.ceil(
            completion_bridge_bits / 18_432
        ),
        "s2c_bram_overlay_blocks_reused": (
            streamed_s2_storage_plan(image)["onchip_bram_blocks"] +
            streamed_s2_storage_plan(image)["immutable_logical_projection_bram_blocks_if_resident"]
        ),
    }


def streamed_s2_relay_cycle_plan(
    image: StreamedS2TemplateImage,
    *, config: StreamedS2RelayConfig | None = None,
    sdram_clock_hz: float = 120_000_000.0,
) -> dict[str, int | float]:
    """Best-case SDRAM transfer floor for fixed-iteration S2R.

    One Relay iteration streams check-major V2C/message data then variable
    reductions.  This assumes ideal 32-bit packed SDRAM bursts, so it is a
    lower bound and is deliberately not reported as measured board speed.
    """

    config = config or StreamedS2RelayConfig()
    config.validate()
    if sdram_clock_hz <= 0.0:
        raise ValueError("S2R SDRAM clock must be positive")
    # 3 halfwords/edge in the check phase (posterior read, old-message read,
    # new-message write), then one message read/edge plus two posterior
    # halfwords/variable in the reduction phase.
    halfword_transactions_per_iteration = 4 * image.edges + 2 * image.variables
    word_transactions_per_iteration = math.ceil(halfword_transactions_per_iteration / 2)
    # A sequential pair cache issues one 16-bit message record for two edges,
    # then combines two adjacent records per SDRAM word transfer.  Check
    # posterior reads and ping-pong posterior reductions retain their existing
    # width.  This is a transport floor for the packed-layout controller, not
    # a claim about the current one-record bridge.
    message_pair_halfwords = math.ceil(image.edges / 2)
    packed_message_halfword_transactions_per_iteration = (
        image.edges + 3 * message_pair_halfwords + 2 * image.variables
    )
    packed_message_word_transactions_per_iteration = (
        math.ceil(image.edges / 2) +
        3 * math.ceil(message_pair_halfwords / 2) + image.variables
    )
    validation_cycles = sum(len(row) for row in image.banked_detector_time_templates) * GROUP_ORDER
    terminal_completion_screened = False
    if config.neutral_completion_start_iteration:
        start = config.neutral_completion_start_iteration
        scheduled_completion_attempts = (
            0 if start > config.max_iterations else
            1 + (config.max_iterations - start) // config.neutral_completion_interval
        )
        terminal_completion_screened = (
            start <= config.max_iterations and
            (config.max_iterations - start) % config.neutral_completion_interval == 0
        )
    else:
        scheduled_completion_attempts = 0
    completion_attempts = scheduled_completion_attempts + int(
        not terminal_completion_screened
    )
    completion_screening_cycles = completion_attempts * len(image.neutral_completion_order)
    cycles = (
        config.max_iterations * word_transactions_per_iteration +
        validation_cycles + completion_screening_cycles
    )
    seconds = cycles / sdram_clock_hz
    return {
        "sdram_clock_hz": sdram_clock_hz,
        "iterations": config.max_iterations,
        "halfword_transactions_per_iteration": halfword_transactions_per_iteration,
        "ideal_32bit_word_transactions_per_iteration": word_transactions_per_iteration,
        "packed_message_halfword_transactions_per_iteration": (
            packed_message_halfword_transactions_per_iteration
        ),
        "packed_message_ideal_32bit_word_transactions_per_iteration": (
            packed_message_word_transactions_per_iteration
        ),
        "syndrome_validation_cycles": validation_cycles,
        "neutral_completion_attempts": completion_attempts,
        "neutral_completion_screening_cycles": completion_screening_cycles,
        "cycles_per_window_lower_bound": cycles,
        "milliseconds_per_window_lower_bound": seconds * 1e3,
    }


def _compact_neighbors(
    image: StreamedS2TemplateImage, *, time: int, check_coordinate: int,
) -> tuple[int, ...]:
    """Instantiate one quotient check row in orbit-major Z12xZ6 space."""

    return tuple(
        orbit * GROUP_ORDER + _add_coordinates(anchor, check_coordinate)
        for orbit, anchor in image.detector_time_templates[time]
    )


def _compact_syndrome(
    image: StreamedS2TemplateImage, correction: bytearray,
) -> tuple[int, ...]:
    """Exact quotient replay used by S2's strict residual certificate."""

    return tuple(
        sum(correction[variable] for variable in _compact_neighbors(
            image, time=time, check_coordinate=coordinate,
        )) & 1
        for time in range(image.time_slices)
        for coordinate in range(GROUP_ORDER)
    )


def _compact_logicals(
    image: StreamedS2TemplateImage, correction: Sequence[int],
) -> tuple[int, ...]:
    """Project compact orbit-major correction through the exact mask ROM."""

    cache_key = (
        image.basis, image.variables, image.checks, image.edges,
        image.serialized_sha256,
    )
    masks = _COMPACT_LOGICAL_MASK_CACHE.get(cache_key)
    if masks is None:
        masks = np.fromiter(
            (
                image.logical_mask_dictionary[
                    image.logical_label_templates[
                        image.orbit_logical_label_template_ids[orbit]
                    ][coordinate]
                ]
                for orbit in range(image.variable_orbits)
                for coordinate in range(GROUP_ORDER)
            ),
            dtype=np.uint16,
            count=image.variables,
        )
        _COMPACT_LOGICAL_MASK_CACHE[cache_key] = masks
    active = np.asarray(correction, dtype=np.uint8).astype(bool, copy=False)
    logical_word = int(np.bitwise_xor.reduce(masks[active], initial=np.uint16(0)))
    return tuple((logical_word >> logical) & 1 for logical in range(LOGICALS))


_COMPACT_EDGE_FAULT_CACHE: dict[
    tuple[str, int, int, int, str], tuple[np.ndarray, np.ndarray]
] = {}
_COMPACT_LOGICAL_MASK_CACHE: dict[tuple[str, int, int, int, str], np.ndarray] = {}


def _compact_edge_faults(image: StreamedS2TemplateImage) -> tuple[np.ndarray, np.ndarray]:
    """Materialize quotient edges only for Python S2R model execution.

    FPGA S2R expands these rows from the resident templates while streaming
    SDRAM; this host array is never part of the FPGA storage plan.
    """

    # CPU telescope is resident across board shots. Re-expanding 391k
    # quotient edges per rare handoff dominated tail latency (and rebuilt the
    # same immutable topology every time). Cache by the compiled image's
    # stable content key; id(image) is unsafe because Python can reuse an id
    # after a short-lived X/Z image is collected.
    cache_key = (
        image.basis, image.variables, image.checks, image.edges,
        image.serialized_sha256,
    )
    cached = _COMPACT_EDGE_FAULT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    degrees = np.fromiter(
        (len(image.detector_time_templates[time])
         for time in range(image.time_slices) for _ in range(GROUP_ORDER)),
        dtype=np.int64, count=image.checks,
    )
    offsets = np.empty(image.checks + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(degrees, out=offsets[1:])
    edge_faults = np.fromiter(
        (variable
         for time in range(image.time_slices)
         for coordinate in range(GROUP_ORDER)
         for variable in _compact_neighbors(
             image, time=time, check_coordinate=coordinate,
         )),
        dtype=np.int32, count=image.edges,
    )
    if int(offsets[-1]) != image.edges or len(edge_faults) != image.edges:
        raise AssertionError("S2R quotient edge expansion drifted")
    _COMPACT_EDGE_FAULT_CACHE[cache_key] = (offsets, edge_faults)
    return offsets, edge_faults


def _neutral_tree_completion(
    image: StreamedS2TemplateImage, residual: Sequence[int],
) -> tuple[int, ...] | None:
    """Solve a residual through compiled logical-neutral forest, if possible."""

    if len(residual) != image.checks:
        raise ValueError("S2 neutral completion residual width mismatch")
    boundary = image.checks
    state = bytearray(image.checks + 1)
    state[:image.checks] = bytes(int(bit) for bit in residual)
    flips: list[int] = []
    for node in reversed(image.neutral_completion_order):
        parent = image.neutral_completion_parents[node]
        if parent < 0:
            if node != boundary and state[node]:
                return None
        elif state[node]:
            fault = image.neutral_completion_faults[node]
            if fault < 0:
                raise AssertionError("S2 neutral completion tree lost edge fault")
            flips.append(fault)
            state[parent] ^= 1
    return tuple(flips)


def _neutral_component_bridge(
    image: StreamedS2TemplateImage, residual: Sequence[int],
) -> tuple[int, ...] | None:
    """Return a logical-neutral component bridge for a residual, if reachable."""

    if len(residual) != image.checks:
        raise ValueError("S2 neutral bridge residual width mismatch")
    parity = 0
    for check, bit in enumerate(residual):
        component = image.neutral_component_by_check[check]
        if bit and component >= 0:
            parity ^= 1 << component
    selected = 0
    for vector, combination in zip(
            image.neutral_bridge_vectors, image.neutral_bridge_combinations,
    ):
        pivot = vector.bit_length() - 1
        if (parity >> pivot) & 1:
            parity ^= vector
            selected ^= combination
    if parity:
        return None
    return tuple(
        fault for index, fault in enumerate(image.neutral_bridge_faults)
        if (selected >> index) & 1
    )


def _neutral_bridged_completion(
    image: StreamedS2TemplateImage, residual: Sequence[int],
) -> tuple[int, ...] | None:
    """Complete a residual with logical-neutral bridge columns then the tree."""

    bridge = _neutral_component_bridge(image, residual)
    if bridge is None:
        return None
    if bridge:
        # A bridge column has the same exact quotient response as its compiled
        # full-DEM fault. Only the 1,728-bit residual is materialized here.
        offsets, edge_faults = _compact_edge_faults(image)
        bridge_correction = np.zeros(image.variables, dtype=np.uint8)
        bridge_correction[np.asarray(bridge, dtype=np.intp)] = 1
        bridged = np.bitwise_xor.reduceat(
            bridge_correction[edge_faults], offsets[:-1],
        ) ^ np.asarray(residual, dtype=np.uint8)
    else:
        bridged = residual
    tree = _neutral_tree_completion(image, bridged)
    if tree is None:
        return None
    return (*bridge, *tree)


def run_streamed_s2(
    image: StreamedS2TemplateImage,
    detectors: Sequence[int],
    *, config: StreamedS2Config | None = None,
    materialize_correction: bool = False,
) -> StreamedS2Result:
    """Execute one compressed bounded-live-state causal min-sum S2 pass.

    ``live`` contains only faults incident on current detector-time slice.
    Retired decisions go into one bit/fault bitmap; no full posterior or edge
    message array is allocated.  ``materialize_correction`` is debug-only;
    production benchmark path leaves correction expanded nowhere.
    """

    config = config or StreamedS2Config()
    minsum = config.minsum_config()
    if config.llr_scale != 4:
        raise ValueError("compiled S2 template priors are fixed at llr_scale=4")
    target = tuple(int(value) for value in detectors)
    if len(target) != image.checks or any(value not in (0, 1) for value in target):
        raise ValueError("full S2 detector width/value mismatch")
    if not any(target):
        return StreamedS2Result(
            True, ((0,) * image.variables if materialize_correction else None), 0,
            (0,) * LOGICALS, target, 0, 0, "zero detector syndrome",
        )

    if image.time_slices != SLICES or image.group_order != GROUP_ORDER:
        raise ValueError("S2 template dimensions drifted")
    start_orbits = tuple(
        tuple(orbit for orbit, start in enumerate(image.orbit_start_slice) if start == time)
        for time in range(image.time_slices)
    )
    end_orbits = tuple(
        tuple(orbit for orbit, end in enumerate(image.orbit_end_slice) if end == time)
        for time in range(image.time_slices)
    )
    live: dict[int, int] = {}
    correction = bytearray(image.variables)
    logical_word = 0
    max_live = 0
    processed = 0
    for time in range(image.time_slices):
        for orbit in start_orbits[time]:
            prior = image.orbit_prior_llr[orbit]
            base = orbit * GROUP_ORDER
            for coordinate in range(GROUP_ORDER):
                live[base + coordinate] = prior
        for coordinate in range(GROUP_ORDER):
            neighbors = _compact_neighbors(
                image, time=time, check_coordinate=coordinate,
            )
            extrinsic = tuple(live[variable] for variable in neighbors)
            outgoing = _wide_check_update(
                extrinsic, syndrome_bit=target[time * GROUP_ORDER + coordinate], config=minsum,
            )
            for variable, message in zip(neighbors, outgoing):
                live[variable] = saturating_add(live[variable], message)
            processed += 1
        max_live = max(max_live, len(live))
        for orbit in end_orbits[time]:
            base = orbit * GROUP_ORDER
            label_template = image.logical_label_templates[
                image.orbit_logical_label_template_ids[orbit]
            ]
            for coordinate in range(GROUP_ORDER):
                variable = base + coordinate
                decision = int(live.pop(variable) < 0)
                correction[variable] = decision
                if decision:
                    logical_word ^= image.logical_mask_dictionary[label_template[coordinate]]
    if live:
        raise AssertionError("S2 streaming lifetime table leaked live posterior state")
    final_syndrome = _compact_syndrome(image, correction)
    logicals = tuple((logical_word >> logical) & 1 for logical in range(LOGICALS))
    accepted = final_syndrome == target
    return StreamedS2Result(
        accepted, tuple(correction) if materialize_correction else None,
        sum(correction), logicals, final_syndrome,
        processed, max_live,
        "" if accepted else "streamed S2 causal min-sum did not satisfy syndrome",
    )


def run_streamed_s2_relay(
    image: StreamedS2TemplateImage,
    detectors: Sequence[int],
    *, config: StreamedS2RelayConfig | None = None,
    materialize_correction: bool = False,
) -> StreamedS2RelayResult:
    """Run fixed-point Relay memory tail over exact quotient-expanded rows.

    This is the software execution contract for SDRAM S2R.  The fast S2C path
    remains the production 18-BSRAM path; S2R is entered only after S2C
    defers.  Host execution expands quotient edges for NumPy, while FPGA
    implementation must generate identical rows from the resident templates.
    """

    config = config or StreamedS2RelayConfig()
    config.validate()
    target = np.asarray(detectors, dtype=np.uint8)
    if target.shape != (image.checks,) or np.any(target > 1):
        raise ValueError("full S2R detector width/value mismatch")
    if not np.any(target):
        return StreamedS2RelayResult(
            True, ((0,) * image.variables if materialize_correction else None), 0,
            0, (0,) * LOGICALS, tuple(int(bit) for bit in target), 0, 0, 0,
            0, 0, "zero detector syndrome",
        )

    offsets, edge_faults = _compact_edge_faults(image)
    degrees = np.diff(offsets)
    edge_checks = np.repeat(np.arange(image.checks, dtype=np.int32), degrees)
    base_priors = np.repeat(np.asarray(image.orbit_prior_llr, dtype=np.int32), GROUP_ORDER)
    priors = base_priors.copy()
    if config.prior_dither_amplitude:
        # xorshift-mixed quotient index. The identical affine/xorshift bit can
        # be produced serially by the SDRAM prior-fill engine; keeping it here
        # vectorized is only an execution-model optimization.
        indices = np.arange(image.variables, dtype=np.uint32)
        mixed = indices * np.uint32(0x9E3779B1) ^ np.uint32(config.prior_dither_seed)
        mixed ^= mixed << np.uint32(13)
        mixed ^= mixed >> np.uint32(17)
        mixed ^= mixed << np.uint32(5)
        dither = np.where((mixed & np.uint32(1)) != 0, 1, -1).astype(np.int32)
        priors += dither * config.prior_dither_amplitude
    posterior = priors.copy()
    if config.memory_strength_low_q is None:
        memory_q = None
    else:
        # One resident per-variable gamma image breaks trapping sets without
        # a floating-point path.  Q7 is the native Relay i32 scale; the hash
        # is the same cheap xorshift family used by prior dithering.
        indices = np.arange(image.variables, dtype=np.uint64)
        mixed = indices * np.uint64(0x9E3779B97F4A7C15) ^ np.uint64(
            config.memory_strength_seed,
        )
        mixed ^= mixed >> np.uint64(30)
        mixed *= np.uint64(0xBF58476D1CE4E5B9)
        mixed ^= mixed >> np.uint64(27)
        mixed *= np.uint64(0x94D049BB133111EB)
        mixed ^= mixed >> np.uint64(31)
        span = config.memory_strength_high_q - config.memory_strength_low_q
        memory_q = config.memory_strength_low_q + (
            ((mixed >> np.uint64(32)) * np.uint64(span)) >> np.uint64(32)
        ).astype(np.int32)
    messages = np.zeros(image.edges, dtype=np.int16)
    message_max = (1 << config.message_magnitude_bits) - 1
    divisor = 1 << config.memory_weight_shift
    peak = 0
    completion_attempts = 0

    for iteration in range(1, config.max_iterations + 1):
        extrinsic = posterior[edge_faults].astype(np.int32) - messages.astype(np.int32)
        magnitude = np.abs(extrinsic)
        signs = extrinsic < 0
        total_sign = np.bitwise_xor.reduceat(signs, offsets[:-1])
        first = np.minimum.reduceat(magnitude, offsets[:-1])
        first_matches = magnitude == first[edge_checks]
        first_count = np.add.reduceat(first_matches.astype(np.int16), offsets[:-1])
        second = np.minimum.reduceat(
            np.where(first_matches, 4096, magnitude), offsets[:-1],
        )
        second[degrees == 1] = 0
        outgoing_magnitude = np.where(
            first_matches & (first_count[edge_checks] == 1),
            second[edge_checks], first[edge_checks],
        )
        outgoing_magnitude = np.minimum(outgoing_magnitude, message_max)
        proposed = np.where(
            target[edge_checks].astype(bool) ^ total_sign[edge_checks] ^ signs,
            -outgoing_magnitude, outgoing_magnitude,
        ).astype(np.int32)
        combined = messages.astype(np.int32) + proposed
        new_messages = np.where(
            combined >= 0, (combined + 1) // 2, -((-combined + 1) // 2),
        )
        new_messages = np.clip(new_messages, -message_max, message_max).astype(np.int16)
        summed = np.bincount(
            edge_faults, weights=new_messages, minlength=image.variables,
        ).astype(np.int32)
        if memory_q is None:
            memory_prior = ((divisor - 1) * priors + posterior) // divisor
        else:
            memory_prior = (
                (128 - memory_q) * priors + memory_q * posterior
            ) // 128
        posterior = np.clip(memory_prior + summed, -1024, 1023).astype(np.int32)
        messages = new_messages
        peak = max(peak, int(np.abs(messages.astype(np.int32)).max(initial=0)))
        correction = (posterior < 0).astype(np.uint8)
        final = np.bitwise_xor.reduceat(correction[edge_faults], offsets[:-1])
        if np.array_equal(final, target):
            return StreamedS2RelayResult(
                True,
                tuple(int(bit) for bit in correction) if materialize_correction else None,
                int(correction.sum()), _relay_prior_cost(base_priors, correction),
                _compact_logicals(image, correction),
                tuple(int(bit) for bit in final), iteration,
                iteration * image.checks, peak, 0, completion_attempts, "",
            )

        screen_completion = (
            config.neutral_completion_start_iteration > 0 and
            iteration >= config.neutral_completion_start_iteration and
            (iteration - config.neutral_completion_start_iteration) %
            config.neutral_completion_interval == 0
        )
        if screen_completion:
            completion_attempts += 1
            # Mid-iteration screening deliberately stays on the existing tree:
            # a partially converged Relay word is not a sound point to choose
            # a component bridge. The bridge is a terminal-defer repair only.
            completion = _neutral_tree_completion(image, final ^ target)
            if completion is not None:
                completed = correction.copy()
                completed[np.asarray(completion, dtype=np.intp)] ^= 1
                completed_final = np.bitwise_xor.reduceat(
                    completed[edge_faults], offsets[:-1],
                )
                if np.array_equal(completed_final, target):
                    return StreamedS2RelayResult(
                        True,
                        tuple(int(bit) for bit in completed)
                        if materialize_correction else None,
                        int(completed.sum()), _relay_prior_cost(base_priors, completed),
                        _compact_logicals(image, completed),
                        tuple(int(bit) for bit in completed_final), iteration,
                        iteration * image.checks, peak, len(completion),
                        completion_attempts,
                        "early logical-neutral degree-1/2 completion",
                    )

    terminal_completion_screened = (
        config.neutral_completion_start_iteration > 0 and
        config.max_iterations >= config.neutral_completion_start_iteration and
        (config.max_iterations - config.neutral_completion_start_iteration) %
        config.neutral_completion_interval == 0
    )
    # Always screen the terminal residual with the bridge. When the ordinary
    # tree was already screened at this iteration, this reuses that terminal
    # point but supplies the one capability the tree does not have: crossing
    # detached zero-logical components.
    if not terminal_completion_screened:
        completion_attempts += 1
    completion = _neutral_bridged_completion(image, final ^ target)
    if completion is not None:
        completed = correction.copy()
        completed[np.asarray(completion, dtype=np.intp)] ^= 1
        completed_final = np.bitwise_xor.reduceat(completed[edge_faults], offsets[:-1])
        if np.array_equal(completed_final, target):
            return StreamedS2RelayResult(
                True,
                tuple(int(bit) for bit in completed) if materialize_correction else None,
                int(completed.sum()), _relay_prior_cost(base_priors, completed),
                _compact_logicals(image, completed),
                tuple(int(bit) for bit in completed_final), config.max_iterations,
                config.max_iterations * image.checks, peak, len(completion),
                completion_attempts,
                "logical-neutral bridged degree-1/2 completion",
            )

    return StreamedS2RelayResult(
        False, tuple(int(bit) for bit in correction) if materialize_correction else None,
        int(correction.sum()), _relay_prior_cost(base_priors, correction),
        _compact_logicals(image, correction),
        tuple(int(bit) for bit in final), config.max_iterations,
        config.max_iterations * image.checks, peak, 0, completion_attempts,
        "fixed-point streamed S2R Relay tail did not satisfy syndrome",
    )


def run_streamed_s2_relay_lite(
    image: StreamedS2TemplateImage,
    detectors: Sequence[int],
    *,
    materialize_correction: bool = False,
) -> StreamedS2RelayResult:
    """Bounded native-Relay rescue matching C worker's retained-posterior leg.

    Fast S2R portfolio runs first. This path runs only for terminal bridge
    candidates. It keeps native Relay's 128-scaled posterior, disordered Q7
    memory legs, retained posterior, exact hard-decision checks, and 3-hit
    early stop, but caps ensemble at 32 legs for endpoint tail control.
    """

    target = np.asarray(detectors, dtype=np.uint8)
    if target.shape != (image.checks,) or np.any(target > 1):
        raise ValueError("full S2R detector width/value mismatch")
    if not np.any(target):
        return StreamedS2RelayResult(
            True,
            tuple(0 for _ in range(image.variables)) if materialize_correction else None,
            0, 0, (0,) * LOGICALS, tuple(int(bit) for bit in target),
            0, 0, 0, 0, 0, "zero detector syndrome",
        )

    offsets, edge_faults = _compact_edge_faults(image)
    degrees = np.diff(offsets)
    edge_checks = np.repeat(np.arange(image.checks, dtype=np.int32), degrees)
    base_cost_priors = np.repeat(
        np.asarray(image.orbit_prior_llr, dtype=np.int32), GROUP_ORDER,
    )
    prior = (base_cost_priors.astype(np.int32) * 32).astype(np.int32)
    posterior = prior.copy()
    messages = np.zeros(image.edges, dtype=np.int16)
    correction = np.zeros(image.variables, dtype=np.uint8)
    final = np.zeros(image.checks, dtype=np.uint8)
    gamma_q = _relay_lite_gamma_q(sets=32, variables=image.variables)
    base_div = np.trunc(prior.astype(np.float64) / 128.0).astype(np.int64)
    best: StreamedS2RelayResult | None = None
    converged = 0
    total_iterations = 0
    peak = 0

    for set_index in range(33):
        q = np.full(
            image.variables, 12, dtype=np.int32,
        ) if set_index == 0 else gamma_q[set_index - 1]
        messages.fill(0)
        max_iterations = 80 if set_index == 0 else 60
        for local_iteration in range(1, max_iterations + 1):
            use_prior_v2c = set_index != 0 and local_iteration == 1
            if use_prior_v2c:
                extrinsic = prior[edge_faults].astype(np.int32)
            else:
                extrinsic = (
                    posterior[edge_faults].astype(np.int32) -
                    messages.astype(np.int32)
                )
            magnitude = np.abs(extrinsic)
            signs = extrinsic < 0
            total_sign = np.bitwise_xor.reduceat(signs, offsets[:-1])
            first = np.minimum.reduceat(magnitude, offsets[:-1])
            first_matches = magnitude == first[edge_checks]
            first_count = np.add.reduceat(
                first_matches.astype(np.int16), offsets[:-1],
            )
            second = np.minimum.reduceat(
                np.where(first_matches, 4096, magnitude), offsets[:-1],
            )
            second[degrees == 1] = 0
            outgoing = np.where(
                first_matches & (first_count[edge_checks] == 1),
                second[edge_checks], first[edge_checks],
            )
            proposed = np.where(
                target[edge_checks].astype(bool) ^
                total_sign[edge_checks] ^ signs,
                -outgoing, outgoing,
            ).astype(np.int16)
            messages = proposed
            peak = max(peak, int(np.abs(messages.astype(np.int32)).max(initial=0)))
            summed = np.bincount(
                edge_faults, weights=messages, minlength=image.variables,
            ).astype(np.int64)
            posterior_memory = (
                base_div * (128 - q) +
                np.trunc(posterior.astype(np.float64) / 128.0).astype(np.int64) * q
            )
            posterior = np.clip(
                posterior_memory + summed, -2047, 2047,
            ).astype(np.int32)
            correction = (posterior < 0).astype(np.uint8)
            final = np.bitwise_xor.reduceat(correction[edge_faults], offsets[:-1])
            total_iterations += 1
            if np.array_equal(final, target):
                cost = _relay_prior_cost(base_cost_priors, correction)
                current = StreamedS2RelayResult(
                    True,
                    tuple(int(bit) for bit in correction)
                    if materialize_correction else None,
                    int(correction.sum()), cost,
                    _compact_logicals(image, correction),
                    tuple(int(bit) for bit in final), total_iterations,
                    total_iterations * image.checks, peak, 0, 0,
                    "C Relay-lite exact",
                )
                converged += 1
                if best is None or (current.prior_cost, current.correction_weight) < (
                        best.prior_cost, best.correction_weight):
                    best = current
                break
        if converged >= 3:
            break

    if best is not None:
        return best
    return StreamedS2RelayResult(
        False,
        tuple(int(bit) for bit in correction) if materialize_correction else None,
        int(correction.sum()), _relay_prior_cost(base_cost_priors, correction),
        _compact_logicals(image, correction), tuple(int(bit) for bit in final),
        total_iterations, total_iterations * image.checks, peak, 0, 0,
        "C Relay-lite did not produce exact candidate",
    )


def run_streamed_s2_relay_with_bridge_retry(
    image: StreamedS2TemplateImage,
    detectors: Sequence[int],
    *,
    config: StreamedS2RelayConfig | None = None,
    bridge_retry_config: StreamedS2RelayConfig | None = None,
    materialize_correction: bool = False,
) -> tuple[StreamedS2RelayResult, StreamedS2RelayResult | None]:
    """Run primary Relay plus one fixed retry only after bridged completion.

    A bridged syndrome is exact but may retain a poor hard-decision logical
    class.  This bounded alternate fixed-point image provides one independent
    logical candidate without perturbing S2R rows that already converged or
    closed through the ordinary logical-neutral forest.  The caller must
    charge both result records to its cycle model when a retry is returned.
    """

    results = run_streamed_s2_relay_bridge_retry_portfolio(
        image, detectors, config=config,
        bridge_retry_configs=(() if bridge_retry_config is None else (bridge_retry_config,)),
        materialize_correction=materialize_correction,
    )
    return results[0], (results[1] if len(results) == 2 else None)


def run_streamed_s2_relay_bridge_retry_portfolio(
    image: StreamedS2TemplateImage,
    detectors: Sequence[int],
    *,
    config: StreamedS2RelayConfig | None = None,
    bridge_retry_configs: Sequence[StreamedS2RelayConfig] = (),
    materialize_correction: bool = False,
) -> tuple[StreamedS2RelayResult, ...]:
    """Run bounded alternate S2R images only while output remains bridged.

    Each profile is independent: no sampled truth, candidate score, or
    correction leaks from one image to another. A profile runs only when its
    predecessor needed a terminal logical-neutral bridge, where syndrome is
    exact but hard-decision logical class is least reliable. The final result
    is the last executed image; callers must charge every returned image.
    """

    result = run_streamed_s2_relay(
        image, detectors, config=config, materialize_correction=materialize_correction,
    )
    results = [result]
    for retry_config in bridge_retry_configs:
        if result.reason != _BRIDGED_COMPLETION_REASON:
            break
        result = run_streamed_s2_relay(
            image, detectors, config=retry_config,
            materialize_correction=materialize_correction,
        )
        results.append(result)
    return tuple(results)


def select_streamed_s2_relay_bridge_retry_candidate(
    results: Sequence[StreamedS2RelayResult],
) -> StreamedS2RelayResult:
    """Select strongest syndrome-exact candidate without sampled truth.

    Every image is a candidate, including the primary.  The old policy
    excluded the primary whenever any retry existed, allowing a retry with a
    worse prior cost to overwrite a better primary correction.  Compare all
    exact candidates. Ordinary/early completion keeps connected syndrome
    support; bridged completion is terminal heuristic and lower confidence.
    Prefer non-bridged exact candidates, then prior cost; deterministic.
    """

    if not results:
        raise ValueError("Relay portfolio must include a primary image")
    if len(results) == 1:
        return results[0]
    accepted = [result for result in results if result.accepted]
    if not accepted:
        return results[0]
    return min(
        accepted,
        key=lambda result: (
            result.reason == _BRIDGED_COMPLETION_REASON,
            result.prior_cost,
            result.correction_weight,
        ),
    )
