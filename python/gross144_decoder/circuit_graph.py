"""J01 circuit detector-error graph schema and strict importer."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .graph_model import Graph


SCHEMA = "GROSS144-CIRCUIT-GRAPH"
VERSION = 1


@dataclass(frozen=True)
class DetectorNode:
    id: int
    round: int
    window: int


@dataclass(frozen=True)
class LogicalAction:
    id: int
    name: str
    support: tuple[int, ...]


@dataclass(frozen=True)
class FaultVariable:
    id: int
    detectors: tuple[int, ...]
    prior: float
    logical_actions: tuple[int, ...]
    window_start: int
    window_end: int
    boundary_ownership: str
    erasure: bool
    stage2_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class CircuitDetectorGraph:
    detectors: tuple[DetectorNode, ...]
    faults: tuple[FaultVariable, ...]
    logical_actions: tuple[LogicalAction, ...]
    source_model: Mapping[str, Any]

    @classmethod
    def from_dict(cls, model: Mapping[str, Any]) -> "CircuitDetectorGraph":
        source = copy.deepcopy(dict(model))
        if source.get("schema") != SCHEMA or source.get("version") != VERSION:
            raise ValueError("unsupported circuit graph schema/version")
        detector_rows = source.get("detectors")
        fault_rows = source.get("faults")
        logical_rows = source.get("logical_actions", [])
        if not isinstance(detector_rows, list) or not isinstance(fault_rows, list):
            raise ValueError("detectors and faults must be arrays")
        if not isinstance(logical_rows, list):
            raise ValueError("logical_actions must be an array")

        detectors = tuple(DetectorNode(
            id=_int_field(row, "id"),
            round=_nonnegative(row, "round"),
            window=_nonnegative(row, "window"),
        ) for row in detector_rows)
        _validate_ordered_ids((node.id for node in detectors), "detector")

        logical_actions = tuple(LogicalAction(
            id=_int_field(row, "id"),
            name=str(row.get("name", "")),
            support=_int_tuple(row.get("support", []), "logical support"),
        ) for row in logical_rows)
        _validate_ordered_ids((action.id for action in logical_actions), "logical action")

        faults = tuple(FaultVariable(
            id=_int_field(row, "id"),
            detectors=_int_tuple(row.get("detectors"), "fault detectors"),
            prior=_probability(row.get("prior")),
            logical_actions=_int_tuple(row.get("logical_actions", []), "fault logical actions"),
            window_start=_nonnegative(row, "window_start"),
            window_end=_nonnegative(row, "window_end"),
            boundary_ownership=str(row.get("boundary_ownership", "interior")),
            erasure=bool(row.get("erasure", False)),
            stage2_metadata=copy.deepcopy(row.get("stage2_metadata", {})),
        ) for row in fault_rows)
        _validate_ordered_ids((fault.id for fault in faults), "fault")

        detector_by_id = {node.id: node for node in detectors}
        logical_by_id = {action.id: action for action in logical_actions}
        for action in logical_actions:
            if any(detector_id not in detector_by_id for detector_id in action.support):
                raise ValueError(
                    f"logical action {action.id} support references an unknown detector"
                )
        for fault in faults:
            if fault.window_end < fault.window_start:
                raise ValueError(f"fault {fault.id} has inverted temporal window")
            if not fault.detectors:
                raise ValueError(f"fault {fault.id} has no detector references")
            if any(detector_id not in detector_by_id for detector_id in fault.detectors):
                raise ValueError(f"fault {fault.id} references an unknown detector")
            if any(action_id not in logical_by_id for action_id in fault.logical_actions):
                raise ValueError(f"fault {fault.id} references an unknown logical action")
            for detector_id in fault.detectors:
                detector = detector_by_id[detector_id]
                if not fault.window_start <= detector.round <= fault.window_end:
                    raise ValueError(
                        f"fault {fault.id} detector {detector_id} is outside its temporal window"
                    )

        # Source representation is retained verbatim (including ordering and
        # optional fields) for exact compiler-side validation.
        return cls(detectors, faults, logical_actions, source)

    def to_dict(self) -> dict[str, Any]:
        model = {
            "schema": SCHEMA,
            "version": VERSION,
            "detectors": [
                {"id": node.id, "round": node.round, "window": node.window}
                for node in self.detectors
            ],
            "faults": [
                {
                    "id": fault.id,
                    "detectors": list(fault.detectors),
                    "prior": fault.prior,
                    "logical_actions": list(fault.logical_actions),
                    "window_start": fault.window_start,
                    "window_end": fault.window_end,
                    "boundary_ownership": fault.boundary_ownership,
                    "erasure": fault.erasure,
                    "stage2_metadata": copy.deepcopy(dict(fault.stage2_metadata)),
                }
                for fault in self.faults
            ],
            "logical_actions": [
                {"id": action.id, "name": action.name, "support": list(action.support)}
                for action in self.logical_actions
            ],
        }
        # Compilers may attach immutable source hashes/configuration.  Preserve
        # this verbatim so a dumped J01 graph remains a self-describing frozen
        # benchmark input rather than merely a derived adjacency list.
        if "source_metadata" in self.source_model:
            model["source_metadata"] = copy.deepcopy(self.source_model["source_metadata"])
        return model

    def source_digest(self) -> str:
        raw = json.dumps(self.source_model, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def detector_matrix(self) -> tuple[tuple[int, ...], ...]:
        columns = [set(fault.detectors) for fault in self.faults]
        return tuple(tuple(int(detector_id in column) for column in columns)
                     for detector_id in range(len(self.detectors)))

    def logical_metadata(self) -> tuple[tuple[int, str, tuple[int, ...]], ...]:
        return tuple((action.id, action.name, action.support)
                     for action in self.logical_actions)

    def to_graph(self) -> Graph:
        """Return the legacy fault-column graph view."""
        return Graph.from_neighbors(
            len(self.detectors),
            [fault.detectors for fault in self.faults],
            check_types=["detector"] * len(self.faults),
        )

    def to_decoder_graph(self) -> Graph:
        """Transpose detector columns into checks over fault variables."""

        # A raw circuit DEM can contain millions of fault columns.  Scanning
        # every column once per detector is quadratic in the compiled graph
        # size and prevents an otherwise valid lossless graph from loading.
        # The source fault order is already stable, so this one-pass transpose
        # preserves deterministic neighbor ordering without column merging.
        neighbors: list[list[int]] = [[] for _ in self.detectors]
        for fault in self.faults:
            for detector_id in fault.detectors:
                neighbors[detector_id].append(fault.id)
        return Graph.from_neighbors(
            len(self.faults), neighbors, check_types=["detector"] * len(neighbors)
        )


def load_circuit_graph(raw: bytes | str | Mapping[str, Any]) -> CircuitDetectorGraph:
    if isinstance(raw, Mapping):
        model = raw
    else:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        model = json.loads(text)
    if not isinstance(model, Mapping):
        raise ValueError("circuit graph root must be an object")
    return CircuitDetectorGraph.from_dict(model)


def dump_circuit_graph(graph: CircuitDetectorGraph) -> bytes:
    return (json.dumps(graph.to_dict(), indent=2, sort_keys=False) + "\n").encode("utf-8")


def synthetic_circuit_graph() -> CircuitDetectorGraph:
    return load_circuit_graph({
        "schema": SCHEMA,
        "version": VERSION,
        "detectors": [
            {"id": 0, "round": 0, "window": 0},
            {"id": 1, "round": 1, "window": 0},
            {"id": 2, "round": 2, "window": 1},
        ],
        "faults": [
            {"id": 0, "detectors": [0], "prior": 0.01,
             "logical_actions": [], "window_start": 0, "window_end": 0,
             "boundary_ownership": "initial", "erasure": False,
             "stage2_metadata": {}},
            {"id": 1, "detectors": [0, 1], "prior": 0.02,
             "logical_actions": [0], "window_start": 0, "window_end": 1,
             "boundary_ownership": "interior", "erasure": False,
             "stage2_metadata": {"basis": "X"}},
            {"id": 2, "detectors": [1, 2], "prior": 0.03,
             "logical_actions": [1], "window_start": 1, "window_end": 2,
             "boundary_ownership": "interior", "erasure": True,
             "stage2_metadata": {"basis": "Z"}},
            {"id": 3, "detectors": [2], "prior": 0.04,
             "logical_actions": [], "window_start": 2, "window_end": 2,
             "boundary_ownership": "final", "erasure": False,
             "stage2_metadata": {}},
        ],
        "logical_actions": [
            {"id": 0, "name": "logical_X", "support": [0, 1]},
            {"id": 1, "name": "logical_Z", "support": [1, 2]},
        ],
    })


def synthetic_degree9_stage1_graph() -> CircuitDetectorGraph:
    """Small initialization-basis graph accepted by the fixed-degree RTL.

    Each detector owns nine independent fault variables.  It is deliberately
    small enough for the resident BRAM path while exercising the exact
    degree-nine C01/C03 representation used by the hardware decoder.
    """

    detectors = [
        {"id": detector_id, "round": detector_id // 2, "window": detector_id // 2}
        for detector_id in range(8)
    ]
    faults = []
    for detector_id, detector in enumerate(detectors):
        for local_id in range(9):
            faults.append({
                "id": len(faults),
                "detectors": [detector_id],
                "prior": 0.25,
                "logical_actions": [],
                "window_start": detector["round"],
                "window_end": detector["round"],
                "boundary_ownership": (
                    "initial" if detector_id == 0 else
                    "final" if detector_id == len(detectors) - 1 else "interior"
                ),
                "erasure": False,
                "stage2_metadata": {"basis": "initialization", "local_id": local_id},
            })
    return load_circuit_graph({
        "schema": SCHEMA,
        "version": VERSION,
        "detectors": detectors,
        "faults": faults,
        "logical_actions": [],
    })


def _int_field(row: Any, key: str) -> int:
    if not isinstance(row, Mapping) or key not in row or isinstance(row[key], bool):
        raise ValueError(f"missing or invalid {key}")
    try:
        value = int(row[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {key}") from exc
    return value


def _nonnegative(row: Any, key: str) -> int:
    value = _int_field(row, key)
    if value < 0:
        raise ValueError(f"{key} must be non-negative")
    return value


def _int_tuple(values: Any, label: str) -> tuple[int, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{label} must be an array")
    result = tuple(int(value) for value in values)
    if any(value < 0 for value in result):
        raise ValueError(f"{label} contains a negative ID")
    if len(set(result)) != len(result):
        raise ValueError(f"{label} contains a duplicate ID")
    return result


def _probability(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("fault prior must be numeric") from exc
    if not 0.0 <= result <= 1.0:
        raise ValueError("fault prior must be in [0, 1]")
    return result


def _validate_ordered_ids(ids: Sequence[int], label: str) -> None:
    values = tuple(ids)
    if values != tuple(range(len(values))):
        raise ValueError(f"{label} IDs must preserve zero-based source ordering")
