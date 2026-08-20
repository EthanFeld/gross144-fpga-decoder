"""N01 typed GARI Stage-2 software oracle."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .circuit_graph import CircuitDetectorGraph
from .fixed_point import fixed_check_update, saturating_add, saturating_sub
from .graph_model import Graph
from .minsum_reference import compute_syndrome


SCHEMA = "GROSS144-GARI"
VERSION = 1
CHECK_TYPES = ("D_X", "D_Z", "U", "V")


@dataclass(frozen=True)
class GariCheck:
    id: int
    check_type: str
    neighbors: tuple[int, ...]
    syndrome: int
    partition: int = 0


@dataclass(frozen=True)
class GariExchange:
    id: int
    source_check: int
    source_edge: int
    destination_check: int
    destination_edge: int


@dataclass(frozen=True)
class GariRouteTag:
    id: int
    check_id: int
    edge_index: int
    variable_id: int
    partition: int


@dataclass(frozen=True)
class GariGraph:
    num_variables: int
    checks: tuple[GariCheck, ...]
    inverse_variable_map: tuple[int, ...]
    source_model: Mapping[str, Any]
    exchanges: tuple[GariExchange, ...] = ()
    original_graph: Graph | None = None
    logical_signatures: tuple[tuple[int, ...], ...] = ()
    routing_tags: tuple[GariRouteTag, ...] = ()

    @classmethod
    def from_dict(cls, model: Mapping[str, Any], *, original_graph: Graph | None = None) -> "GariGraph":
        source = copy.deepcopy(dict(model))
        if source.get("schema") != SCHEMA or source.get("version") != VERSION:
            raise ValueError("unsupported GARI schema/version")
        num_variables = int(source.get("num_variables", 0))
        if num_variables < 1:
            raise ValueError("GARI variable count must be positive")
        inverse = tuple(int(value) for value in source.get("inverse_variable_map", []))
        if len(inverse) != num_variables or set(inverse) != set(range(num_variables)):
            raise ValueError("inverse variable map must be a permutation")
        rows = source.get("checks")
        if not isinstance(rows, list) or not rows:
            raise ValueError("GARI checks must be a non-empty array")
        checks = tuple(GariCheck(
            id=int(row.get("id", -1)),
            check_type=str(row.get("type", "")),
            neighbors=tuple(int(value) for value in row.get("neighbors", [])),
            syndrome=int(row.get("syndrome", 0)),
            partition=int(row.get("partition", 0)),
        ) for row in rows)
        if tuple(check.id for check in checks) != tuple(range(len(checks))):
            raise ValueError("GARI check IDs must be ordered")
        for check in checks:
            if check.check_type not in CHECK_TYPES:
                raise ValueError(f"unsupported GARI check type {check.check_type}")
            if not check.neighbors or any(
                value < 0 or value >= num_variables for value in check.neighbors
            ):
                raise ValueError(f"invalid GARI neighbors for check {check.id}")
            if len(set(check.neighbors)) != len(check.neighbors):
                raise ValueError(f"duplicate GARI neighbor in check {check.id}")
            if check.syndrome not in (0, 1) or check.partition < 0:
                raise ValueError(f"invalid GARI metadata for check {check.id}")
        exchange_rows = source.get("exchange_order", [])
        if not isinstance(exchange_rows, list):
            raise ValueError("GARI exchange_order must be an array")
        exchanges = tuple(GariExchange(
            id=int(row.get("id", -1)),
            source_check=int(row.get("source_check", -1)),
            source_edge=int(row.get("source_edge", -1)),
            destination_check=int(row.get("destination_check", -1)),
            destination_edge=int(row.get("destination_edge", -1)),
        ) for row in exchange_rows)
        if tuple(exchange.id for exchange in exchanges) != tuple(range(len(exchanges))):
            raise ValueError("GARI exchange IDs must be ordered")
        for exchange in exchanges:
            if not (0 <= exchange.source_check < len(checks) and
                    0 <= exchange.destination_check < len(checks)):
                raise ValueError("GARI exchange check ID out of range")
            if exchange.source_edge >= len(checks[exchange.source_check].neighbors) or \
                    exchange.destination_edge >= len(checks[exchange.destination_check].neighbors) or \
                    exchange.source_edge < 0 or exchange.destination_edge < 0:
                raise ValueError("GARI exchange edge out of range")
        logical_rows = source.get("logical_signatures", [])
        if not isinstance(logical_rows, list):
            raise ValueError("GARI logical_signatures must be an array")
        logical_signatures = tuple(tuple(int(bit) for bit in row) for row in logical_rows)
        if any(len(row) != num_variables or any(bit not in (0, 1) for bit in row)
               for row in logical_signatures):
            raise ValueError("GARI logical signature width/value mismatch")
        route_rows = source.get("routing_tags", [])
        if not isinstance(route_rows, list):
            raise ValueError("GARI routing_tags must be an array")
        routing_tags = tuple(GariRouteTag(
            id=int(row.get("id", -1)), check_id=int(row.get("check_id", -1)),
            edge_index=int(row.get("edge_index", -1)),
            variable_id=int(row.get("variable_id", -1)),
            partition=int(row.get("partition", -1)),
        ) for row in route_rows)
        if tuple(tag.id for tag in routing_tags) != tuple(range(len(routing_tags))):
            raise ValueError("GARI routing tag IDs must be ordered")
        for tag in routing_tags:
            if not (0 <= tag.check_id < len(checks) and
                    0 <= tag.edge_index < len(checks[tag.check_id].neighbors) and
                    tag.variable_id == checks[tag.check_id].neighbors[tag.edge_index] and
                    tag.partition >= 0):
                raise ValueError("invalid GARI routing tag")
        if original_graph is not None and original_graph.num_variables != num_variables:
            raise ValueError("original graph variable width mismatch")
        return cls(num_variables, checks, inverse, source, exchanges, original_graph,
                   logical_signatures, routing_tags)

    @classmethod
    def from_decoder_graph(
        cls,
        graph: Graph,
        *,
        check_types: Sequence[str] | None = None,
        inverse_variable_map: Sequence[int] | None = None,
        exchanges: Sequence[GariExchange] = (),
        original_graph: Graph | None = None,
        logical_signatures: Sequence[Sequence[int]] = (),
        routing_tags: Sequence[GariRouteTag] = (),
    ) -> "GariGraph":
        types = tuple(check_types or ("D_X",) * len(graph.checks))
        if len(types) != len(graph.checks):
            raise ValueError("GARI check type width mismatch")
        inverse = tuple(range(graph.num_variables)) if inverse_variable_map is None \
            else tuple(int(value) for value in inverse_variable_map)
        model = {
            "schema": SCHEMA, "version": VERSION,
            "num_variables": graph.num_variables,
            "inverse_variable_map": list(inverse),
            "checks": [
                {"id": check.id, "type": types[check.id],
                 "neighbors": list(check.neighbors),
                 "syndrome": graph.syndrome[check.id],
                 "partition": 0}
                for check in graph.checks
            ],
            "exchange_order": [exchange.__dict__ for exchange in exchanges],
            "logical_signatures": [list(row) for row in logical_signatures],
            "routing_tags": [tag.__dict__ for tag in routing_tags],
        }
        return cls.from_dict(model, original_graph=original_graph)

    @classmethod
    def from_circuit(
        cls,
        circuit: CircuitDetectorGraph,
        detector_syndrome: Sequence[int],
    ) -> "GariGraph":
        return compile_gari_subgraph(circuit, detector_syndrome)

    def to_graph(self) -> Graph:
        return Graph.from_neighbors(
            self.num_variables,
            [check.neighbors for check in self.checks],
            syndrome=tuple(check.syndrome for check in self.checks),
            check_types=[check.check_type for check in self.checks],
        )

    def main_order(self) -> tuple[int, ...]:
        return tuple(check.id for check in self.checks if check.check_type in ("D_X", "D_Z"))

    def auxiliary_batches(self, parallelism: int) -> tuple[tuple[int, ...], ...]:
        if parallelism < 1:
            raise ValueError("auxiliary parallelism must be positive")
        auxiliary = [check for check in self.checks if check.check_type in ("U", "V")]
        batches: list[tuple[int, ...]] = []
        current: list[int] = []
        used_variables: set[int] = set()
        current_partition: int | None = None
        for check in auxiliary:
            independent = not used_variables.intersection(check.neighbors)
            same_partition = current_partition is None or current_partition == check.partition
            if current and (len(current) >= parallelism or not independent or not same_partition):
                batches.append(tuple(current))
                current, used_variables, current_partition = [], set(), None
            current.append(check.id)
            used_variables.update(check.neighbors)
            current_partition = check.partition
        if current:
            batches.append(tuple(current))
        return tuple(batches)

    def inverse_correction(self, correction: Sequence[int]) -> tuple[int, ...]:
        if len(correction) != self.num_variables:
            raise ValueError("GARI correction width mismatch")
        result = [0] * self.num_variables
        for gari_var, original_var in enumerate(self.inverse_variable_map):
            result[original_var] = int(correction[gari_var])
        return tuple(result)

    def with_syndrome(
        self,
        syndrome: Sequence[int],
        *,
        original_graph: Graph | None = None,
    ) -> "GariGraph":
        """Bind one received syndrome without changing the resident graph image.

        The FPGA keeps the check topology resident between decode windows.  A
        received syndrome is therefore runtime state, not part of the image
        identity used by :class:`Stage2Cache`.
        """

        target = tuple(int(bit) for bit in syndrome)
        if len(target) != len(self.checks) or any(bit not in (0, 1) for bit in target):
            raise ValueError("GARI syndrome width/value mismatch")
        if original_graph is not None:
            if original_graph.num_variables != self.num_variables or \
                    len(original_graph.checks) != len(self.checks):
                raise ValueError("GARI original graph shape mismatch")
            if tuple(original_graph.syndrome) != target:
                raise ValueError("GARI original graph syndrome mismatch")
        checks = tuple(
            GariCheck(check.id, check.check_type, check.neighbors, target[check.id],
                      check.partition)
            for check in self.checks
        )
        return GariGraph(
            self.num_variables, checks, self.inverse_variable_map, self.source_model,
            self.exchanges, original_graph, self.logical_signatures, self.routing_tags,
        )


def compile_gari_subgraph(
    circuit: CircuitDetectorGraph,
    detector_syndrome: Sequence[int],
    *,
    inverse_variable_map: Sequence[int] | None = None,
    check_type_overrides: Mapping[int, str] | None = None,
) -> GariGraph:
    """Compile J01/J02 circuit graph into typed GARI sections and route maps."""
    source_graph = circuit.to_decoder_graph()
    if len(detector_syndrome) != len(source_graph.checks):
        raise ValueError("detector syndrome width mismatch")
    num_variables = len(circuit.faults)
    original = Graph.from_neighbors(
        num_variables, [check.neighbors for check in source_graph.checks],
        syndrome=detector_syndrome,
        check_types=[check.check_type for check in source_graph.checks],
    )
    inverse = tuple(range(num_variables)) if inverse_variable_map is None \
        else tuple(int(value) for value in inverse_variable_map)
    if len(inverse) != num_variables or set(inverse) != set(range(num_variables)):
        raise ValueError("GARI inverse variable map must be a permutation")
    forward = [0] * num_variables
    for gari_id, original_id in enumerate(inverse):
        forward[original_id] = gari_id
    overrides = dict(check_type_overrides or {})
    if any(check_id < 0 or check_id >= len(original.checks) for check_id in overrides):
        raise ValueError("GARI check-type override ID out of range")

    neighbors: list[tuple[int, ...]] = []
    check_types: list[str] = []
    partitions: list[int] = []
    for check in original.checks:
        source_faults = check.neighbors
        check_type = overrides.get(check.id) or _classify_gari_check(circuit, source_faults)
        if check_type not in CHECK_TYPES:
            raise ValueError(f"unsupported GARI check type {check_type}")
        partition = _check_partition(circuit, source_faults) if check_type in ("U", "V") else 0
        neighbors.append(tuple(forward[fault_id] for fault_id in source_faults))
        check_types.append(check_type)
        partitions.append(partition)
    transformed = Graph.from_neighbors(
        num_variables, neighbors, syndrome=detector_syndrome,
        check_types=check_types,
    )

    logical_signatures: list[tuple[int, ...]] = []
    for logical_id in range(len(circuit.logical_actions)):
        original_signature = tuple(
            int(logical_id in fault.logical_actions) for fault in circuit.faults
        )
        logical_signatures.append(tuple(original_signature[original_id] for original_id in inverse))

    route_tags: list[GariRouteTag] = []
    for check_id, (check, check_type) in enumerate(zip(transformed.checks, check_types)):
        if check_type not in ("U", "V"):
            continue
        for edge_index, variable_id in enumerate(check.neighbors):
            route_tags.append(GariRouteTag(
                len(route_tags), check_id, edge_index, variable_id, partitions[check_id],
            ))

    exchanges: list[GariExchange] = []
    main_edges: dict[int, list[tuple[int, int]]] = {}
    for check_id, (check, check_type) in enumerate(zip(transformed.checks, check_types)):
        if check_type in ("D_X", "D_Z"):
            for edge_index, variable_id in enumerate(check.neighbors):
                main_edges.setdefault(variable_id, []).append((check_id, edge_index))
    for tag in route_tags:
        for destination_check, destination_edge in main_edges.get(tag.variable_id, ()):
            exchanges.append(GariExchange(
                len(exchanges), tag.check_id, tag.edge_index,
                destination_check, destination_edge,
            ))
    return GariGraph.from_decoder_graph(
        transformed, check_types=check_types, inverse_variable_map=inverse,
        exchanges=tuple(exchanges), original_graph=original,
        logical_signatures=tuple(logical_signatures), routing_tags=tuple(route_tags),
    )


def gari_compile_report(circuit: CircuitDetectorGraph, gari: GariGraph) -> dict[str, object]:
    """Return deterministic C09 node/edge/type/routing metrics."""
    original_edges = sum(len(fault.detectors) for fault in circuit.faults)
    gari_edges = sum(len(check.neighbors) for check in gari.checks)
    type_counts = {check_type: sum(check.check_type == check_type for check in gari.checks)
                   for check_type in CHECK_TYPES}
    return {
        "original_nodes": {
            "detectors": len(circuit.detectors), "faults": len(circuit.faults),
            "edges": original_edges,
        },
        "gari_nodes": {
            "checks": len(gari.checks), "variables": gari.num_variables,
            "edges": gari_edges,
        },
        "deltas": {
            "checks_minus_detectors": len(gari.checks) - len(circuit.detectors),
            "variables_minus_faults": gari.num_variables - len(circuit.faults),
            "edges_minus_original": gari_edges - original_edges,
        },
        "check_type_counts": type_counts,
        "main_order": list(gari.main_order()),
        "auxiliary_batches": [list(batch) for batch in gari.auxiliary_batches(2)],
        "routing_tags": [tag.__dict__ for tag in gari.routing_tags],
        "exchange_count": len(gari.exchanges),
        "logical_signature_count": len(gari.logical_signatures),
        "inverse_variable_map": list(gari.inverse_variable_map),
    }


def _classify_gari_check(
    circuit: CircuitDetectorGraph, fault_ids: Sequence[int],
) -> str:
    explicit = {
        str(circuit.faults[fault_id].stage2_metadata.get("gari_type",
                                                         circuit.faults[fault_id].stage2_metadata.get("check_type")))
        for fault_id in fault_ids
        if circuit.faults[fault_id].stage2_metadata.get("gari_type",
                                                        circuit.faults[fault_id].stage2_metadata.get("check_type"))
        is not None
    }
    if len(explicit) > 1:
        raise ValueError("GARI check has conflicting explicit types")
    if explicit:
        value = next(iter(explicit))
        if value not in CHECK_TYPES:
            raise ValueError(f"unsupported GARI check type {value}")
        return value
    bases = {str(circuit.faults[fault_id].stage2_metadata.get("basis", "X")).upper()
             for fault_id in fault_ids}
    return "D_Z" if bases == {"Z"} else "D_X"


def _check_partition(circuit: CircuitDetectorGraph, fault_ids: Sequence[int]) -> int:
    partitions = {
        int(circuit.faults[fault_id].stage2_metadata.get(
            "partition", circuit.faults[fault_id].stage2_metadata.get("auxiliary_partition", 0)))
        for fault_id in fault_ids
    }
    if len(partitions) > 1:
        raise ValueError("GARI auxiliary check has conflicting partitions")
    value = next(iter(partitions), 0)
    if value < 0:
        raise ValueError("GARI partition must be non-negative")
    return value


@dataclass(frozen=True)
class GariConfig:
    main_schedule: str = "serial"
    max_iterations: int = 2
    auxiliary_parallelism: int = 2
    page_size: int = 0


@dataclass(frozen=True)
class GariTraceEvent:
    iteration: int
    section: str
    checks: tuple[int, ...]
    exchanges: tuple[int, ...]


@dataclass(frozen=True)
class GariResult:
    correction: tuple[int, ...]
    original_correction: tuple[int, ...]
    syndrome: tuple[int, ...]
    original_syndrome: tuple[int, ...] | None
    success: bool
    iterations: int
    trace: tuple[GariTraceEvent, ...]
    typed_updates: tuple[tuple[int, tuple[int, ...]], ...]
    generic_updates: tuple[tuple[int, tuple[int, ...]], ...]
    exchange_events: tuple[int, ...]
    checks_processed: int
    page_fetches: int
    cycle_count: int


def run_gari(
    gari: GariGraph,
    prior_llr: Sequence[int],
    *,
    syndrome: Sequence[int] | None = None,
    config: GariConfig | None = None,
) -> GariResult:
    config = config or GariConfig()
    if config.main_schedule not in ("serial", "layered"):
        raise ValueError("main_schedule must be serial or layered")
    if config.max_iterations < 0 or config.auxiliary_parallelism < 1:
        raise ValueError("invalid GARI configuration")
    if config.page_size < 0:
        raise ValueError("page_size must be non-negative")
    if len(prior_llr) != gari.num_variables:
        raise ValueError("GARI prior width mismatch")
    target = tuple(check.syndrome for check in gari.checks) if syndrome is None \
        else tuple(int(bit) for bit in syndrome)
    if len(target) != len(gari.checks) or any(bit not in (0, 1) for bit in target):
        raise ValueError("GARI syndrome width/value mismatch")
    graph = gari.to_graph()
    posterior = [max(-1024, min(1023, int(value))) for value in prior_llr]
    edge_messages = [[0 for _ in check.neighbors] for check in gari.checks]
    correction = tuple(int(value < 0) for value in posterior)
    actual = compute_syndrome(graph, correction)
    if actual == target:
        original = gari.inverse_correction(correction)
        original_syndrome = compute_syndrome(gari.original_graph, original) \
            if gari.original_graph is not None else None
        return GariResult(
            correction, original, actual, original_syndrome,
            original_syndrome is None or original_syndrome == gari.original_graph.syndrome,
            0, (), (), (), (), 0, _page_fetches(len(gari.checks), config.page_size),
            _page_fetches(len(gari.checks), config.page_size),
        )

    main_order = gari.main_order()
    aux_batches = gari.auxiliary_batches(config.auxiliary_parallelism)
    trace: list[GariTraceEvent] = []
    typed_updates: list[tuple[int, tuple[int, ...]]] = []
    generic_updates: list[tuple[int, tuple[int, ...]]] = []
    exchange_events: list[int] = []
    checks_processed = 0
    page_fetches = _page_fetches(len(gari.checks), config.page_size)

    def update_check(check_id: int) -> None:
        nonlocal checks_processed
        check = gari.checks[check_id]
        old = tuple(edge_messages[check_id])
        extrinsic = tuple(
            saturating_sub(posterior[var], old_message)
            for var, old_message in zip(check.neighbors, old)
        )
        # Typed and generic CHECK_VAR paths intentionally share the exact
        # fixed-point primitive; retaining both traces makes equality explicit.
        typed = fixed_check_update(extrinsic, syndrome_bit=target[check_id])
        generic = fixed_check_update(extrinsic, syndrome_bit=target[check_id])
        if typed != generic:
            raise AssertionError("typed/generic GARI update mismatch")
        for edge, (var, old_message, new_message) in enumerate(
            zip(check.neighbors, old, typed)
        ):
            posterior[var] = saturating_add(
                saturating_sub(posterior[var], old_message), new_message
            )
            edge_messages[check_id][edge] = new_message
        typed_updates.append((check_id, tuple(typed)))
        generic_updates.append((check_id, tuple(generic)))
        checks_processed += 1

    for iteration in range(1, config.max_iterations + 1):
        if config.main_schedule == "serial":
            main_batches = tuple((check_id,) for check_id in main_order)
        else:
            main_batches = (main_order,)
        for batch in main_batches:
            for check_id in batch:
                update_check(check_id)
            trace.append(GariTraceEvent(iteration, "main", tuple(batch), ()))
        for batch in aux_batches:
            for check_id in batch:
                update_check(check_id)
            batch_exchanges = tuple(exchange.id for exchange in gari.exchanges
                                    if exchange.source_check in batch or
                                    exchange.destination_check in batch)
            exchange_events.extend(batch_exchanges)
            trace.append(GariTraceEvent(iteration, "aux", tuple(batch), batch_exchanges))
        correction = tuple(int(value < 0) for value in posterior)
        actual = compute_syndrome(graph, correction)
        if actual == target:
            original = gari.inverse_correction(correction)
            original_syndrome = compute_syndrome(gari.original_graph, original) \
                if gari.original_graph is not None else None
            original_ok = original_syndrome is None or original_syndrome == gari.original_graph.syndrome
            return GariResult(
                correction, original, actual, original_syndrome, original_ok,
                iteration, tuple(trace), tuple(typed_updates), tuple(generic_updates),
                tuple(exchange_events), checks_processed, page_fetches,
                checks_processed + page_fetches,
            )
    original = gari.inverse_correction(correction)
    original_syndrome = compute_syndrome(gari.original_graph, original) \
        if gari.original_graph is not None else None
    original_ok = original_syndrome is None or original_syndrome == gari.original_graph.syndrome
    return GariResult(
        correction, original, actual, original_syndrome, False and original_ok,
        config.max_iterations, tuple(trace), tuple(typed_updates), tuple(generic_updates),
        tuple(exchange_events), checks_processed, page_fetches,
        checks_processed + page_fetches,
    )


def _page_fetches(check_count: int, page_size: int) -> int:
    if check_count == 0:
        return 0
    return 1 if page_size == 0 else (check_count + page_size - 1) // page_size
