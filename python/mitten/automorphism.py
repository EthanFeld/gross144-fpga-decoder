"""M01 sequential automorphism/list-decoding reference."""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass, replace
from itertools import combinations, permutations
from typing import Callable, Sequence

from .candidate_score import Candidate, make_candidate, select_best
from .graph_model import Graph, GroupActionMetadata
from .minsum_reference import DecodeResult, layered_min_sum


Decoder = Callable[..., DecodeResult]


@dataclass(frozen=True)
class AutomorphismTrial:
    trial_id: int
    variable_permutation: tuple[int, ...]
    check_permutation: tuple[int, ...]
    prior_perturbation: tuple[float, ...] = ()
    max_iterations: int = 10
    enabled: bool = True
    schedule_id: int = 0


@dataclass(frozen=True)
class AutomorphismConfig:
    trials: int = 4
    early_stop_score: float | None = None
    source_stage: str = "S1A"
    qualification_subset: bool = False


@dataclass(frozen=True)
class AutomorphismTrialResult:
    trial_id: int
    variable_permutation: tuple[int, ...]
    candidate: Candidate | None
    success: bool
    iterations: int
    work: int
    trace_digest: str
    reason: str = ""


@dataclass(frozen=True)
class AutomorphismResult:
    status: str
    selected_candidate: Candidate | None
    trials: tuple[AutomorphismTrialResult, ...]
    trial_order: tuple[int, ...]
    state_context_count: int
    restore_count: int
    total_work: int
    distinct_trace_count: int


@dataclass(frozen=True)
class AutomorphismSelection:
    """Frozen S1A selection derived from declared group actions only."""

    trials: tuple[AutomorphismTrial, ...]
    candidate_count: int
    coset_count: int
    short_cycle_supports: tuple[tuple[int, ...], ...]
    trapping_set_supports: tuple[tuple[int, ...], ...]
    minimum_pairwise_decorrelation: float
    pairwise_decorrelation: tuple[tuple[float, ...], ...]


def _validate_permutation(values: Sequence[int], size: int, label: str) -> tuple[int, ...]:
    result = tuple(int(value) for value in values)
    if len(result) != size or set(result) != set(range(size)):
        raise ValueError(f"{label} permutation must contain each ID 0..{size - 1} once")
    return result


def transform_graph(
    graph: Graph,
    variable_permutation: Sequence[int],
    check_permutation: Sequence[int],
) -> Graph:
    """Return the graph under old-ID -> new-ID variable/check permutations."""

    variables = _validate_permutation(variable_permutation, graph.num_variables, "variable")
    checks = _validate_permutation(check_permutation, len(graph.checks), "check")
    neighbors: list[tuple[int, ...] | None] = [None] * len(graph.checks)
    syndrome: list[int] = [0] * len(graph.checks)
    check_types: list[str] = ["static"] * len(graph.checks)
    for old_check in graph.checks:
        new_id = checks[old_check.id]
        neighbors[new_id] = tuple(variables[var] for var in old_check.neighbors)
        syndrome[new_id] = graph.syndrome[old_check.id]
        check_types[new_id] = old_check.check_type
    if any(row is None for row in neighbors):
        raise ValueError("check permutation did not cover every check")
    return Graph.from_neighbors(
        graph.num_variables,
        tuple(row for row in neighbors if row is not None),
        prior_classes=tuple(variable.prior_class for variable in graph.variables),
        syndrome=syndrome,
        check_types=check_types,
    )


def _transform_prior(
    prior_llr: Sequence[float],
    variable_permutation: Sequence[int],
    perturbation: Sequence[float],
) -> tuple[float, ...]:
    transformed = [0.0] * len(prior_llr)
    for old_var, new_var in enumerate(variable_permutation):
        transformed[new_var] = float(prior_llr[old_var])
    if perturbation:
        if len(perturbation) != len(prior_llr):
            raise ValueError("prior perturbation width mismatch")
        transformed = [value + float(delta)
                       for value, delta in zip(transformed, perturbation)]
    return tuple(transformed)


def _inverse_correction(
    transformed_correction: Sequence[int], variable_permutation: Sequence[int]
) -> tuple[int, ...]:
    original = [0] * len(transformed_correction)
    for old_var, new_var in enumerate(variable_permutation):
        original[old_var] = int(transformed_correction[new_var])
    return tuple(original)


def _trace_digest(result: DecodeResult) -> str:
    material = repr((result.correction, result.posterior, result.syndrome,
                     result.success, result.iterations))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _validate_trial(graph: Graph, trial: AutomorphismTrial) -> None:
    if trial.trial_id < 0:
        raise ValueError("trial IDs must be non-negative")
    _validate_permutation(trial.variable_permutation, graph.num_variables, "variable")
    _validate_permutation(trial.check_permutation, len(graph.checks), "check")
    if trial.prior_perturbation and len(trial.prior_perturbation) != graph.num_variables:
        raise ValueError("prior perturbation width mismatch")
    if trial.max_iterations < 0:
        raise ValueError("max_iterations must be non-negative")
    if not graph.preserves_graph(trial.variable_permutation, trial.check_permutation):
        raise ValueError(f"trial {trial.trial_id} is not a graph automorphism")


def run_automorphism_ensemble(
    graph: Graph,
    prior_llr: Sequence[float],
    *,
    syndrome: Sequence[int] | None = None,
    trials: Sequence[AutomorphismTrial],
    config: AutomorphismConfig | None = None,
    logical_signatures: Sequence[Sequence[int]] | None = None,
    decoder: Decoder = layered_min_sum,
) -> AutomorphismResult:
    config = config or AutomorphismConfig()
    if config.trials not in (1, 2, 4):
        raise ValueError("automorphism qualification supports one, two, or four trials")
    if config.trials != 4 and not config.qualification_subset:
        raise ValueError("release automorphism ensemble requires exactly four trials")
    if len(trials) != config.trials:
        raise ValueError("exactly four trial specifications are required")
    if len(prior_llr) != graph.num_variables:
        raise ValueError("prior width mismatch")
    target = tuple(graph.syndrome if syndrome is None else syndrome)
    if len(target) != len(graph.checks) or any(int(bit) not in (0, 1) for bit in target):
        raise ValueError("syndrome must contain one binary bit per check")
    seen_ids: set[int] = set()
    for trial in trials:
        _validate_trial(graph, trial)
        if trial.trial_id in seen_ids:
            raise ValueError("trial IDs must be unique")
        seen_ids.add(trial.trial_id)

    results: list[AutomorphismTrialResult] = []
    valid_candidates: list[Candidate] = []
    total_work = 0
    for trial in trials:
        if not trial.enabled:
            results.append(AutomorphismTrialResult(
                trial.trial_id, tuple(trial.variable_permutation), None,
                False, 0, 0, "", "disabled"
            ))
            continue
        transformed = transform_graph(
            graph, trial.variable_permutation, trial.check_permutation
        )
        transformed_prior = _transform_prior(
            prior_llr, trial.variable_permutation, trial.prior_perturbation
        )
        # transform_graph stores the syndrome at new check IDs; derive it from
        # the immutable original target using the same old->new permutation.
        transformed_syndrome_list = [0] * len(graph.checks)
        for old_id, new_id in enumerate(trial.check_permutation):
            transformed_syndrome_list[new_id] = target[old_id]
        transformed_syndrome = tuple(transformed_syndrome_list)
        decoded = decoder(
            transformed, transformed_prior, syndrome=transformed_syndrome,
            max_iterations=trial.max_iterations,
        )
        total_work += decoded.iterations * len(graph.checks)
        original_correction = _inverse_correction(
            decoded.correction, trial.variable_permutation
        )
        candidate = make_candidate(
            graph, original_correction, source_stage=config.source_stage,
            trial_id=trial.trial_id, syndrome=target, prior_llr=prior_llr,
            logical_signatures=logical_signatures,
        )
        accepted = bool(decoded.success and candidate.valid and candidate.syndrome_satisfied)
        if accepted:
            valid_candidates.append(candidate)
        results.append(AutomorphismTrialResult(
            trial.trial_id, tuple(trial.variable_permutation), candidate,
            accepted, decoded.iterations, decoded.iterations * len(graph.checks),
            _trace_digest(decoded), "" if accepted else "invalid or syndrome failure"
        ))
        if accepted and config.early_stop_score is not None and \
                candidate.negative_log_likelihood_or_weight <= config.early_stop_score:
            break

    selected = select_best(valid_candidates)
    status = "SUCCESS" if selected is not None else "NO_VALID_CANDIDATE"
    digests = {result.trace_digest for result in results if result.trace_digest}
    return AutomorphismResult(
        status, selected, tuple(results), tuple(result.trial_id for result in results),
        1, len(results), total_work, len(digests)
    )


def select_decorrelated_group_trials(
    graph: Graph,
    *,
    trial_count: int = 4,
    max_iterations: int = 10,
    prior_perturbations: Sequence[Sequence[float]] | None = None,
    schedule_ids: Sequence[int] | None = None,
    require_identity: bool = True,
    maximum_cycle_length: int = 8,
    maximum_trapping_set_size: int = 4,
) -> AutomorphismSelection:
    """Select fixed group-action representatives by maximin support diversity.

    ``graph.group`` is a declarative, analytic action table.  This routine
    never discovers graph automorphisms: it validates those known actions,
    collapses actions with the same induced map on short-cycle/trapping-set
    supports, then exactly maximizes the minimum pairwise decorrelation among
    the requested representatives.  The identity is included by default so
    the first S1A trial remains the baseline-equivalent decoder path.
    """

    if trial_count < 1:
        raise ValueError("automorphism selection requires at least one trial")
    if max_iterations < 0:
        raise ValueError("max_iterations must be non-negative")
    if maximum_cycle_length < 4 or maximum_cycle_length % 2:
        raise ValueError("maximum cycle length must be even and at least four")
    if maximum_trapping_set_size < 2:
        raise ValueError("maximum trapping-set size must be at least two")

    cycles = _short_cycle_supports(graph, maximum_cycle_length)
    trapping_sets = _small_trapping_set_supports(
        graph, cycles, maximum_trapping_set_size,
    )
    supports = tuple(sorted({*cycles, *trapping_sets}, key=lambda item: (len(item), item)))
    if not supports:
        raise ValueError(
            "group selection requires at least one short-cycle or small trapping-set support"
        )

    all_actions = _declared_group_actions(graph)
    representatives = _support_coset_representatives(all_actions, supports)
    if len(representatives) < trial_count:
        raise ValueError(
            "known group action has fewer support-distinct coset representatives "
            f"than the requested {trial_count} trials"
        )
    selected_indices, minimum_decorrelation = _maximin_group_selection(
        representatives, supports, trial_count, require_identity,
    )
    selected_actions = tuple(representatives[index] for index in selected_indices)

    if prior_perturbations is None:
        perturbations = ((),) * trial_count
    else:
        if len(prior_perturbations) != trial_count:
            raise ValueError("prior perturbation count must match selected trial count")
        perturbations = tuple(tuple(float(value) for value in values)
                              for values in prior_perturbations)
        if any(values and len(values) != graph.num_variables for values in perturbations):
            raise ValueError("prior perturbation width mismatch")
    if schedule_ids is None:
        schedules = tuple(range(trial_count))
    else:
        if len(schedule_ids) != trial_count:
            raise ValueError("schedule ID count must match selected trial count")
        schedules = tuple(int(value) for value in schedule_ids)
        if any(value < 0 or value > 0xFFFF for value in schedules):
            raise ValueError("schedule ID must fit unsigned 16-bit table field")

    trials = tuple(
        AutomorphismTrial(
            trial_id=index,
            variable_permutation=action[0],
            check_permutation=action[1],
            prior_perturbation=perturbations[index],
            max_iterations=max_iterations,
            schedule_id=schedules[index],
        )
        for index, action in enumerate(selected_actions)
    )
    denominator = sum(len(support) for support in supports)
    pairwise = _pairwise_decorrelation(selected_actions, supports, denominator)
    return AutomorphismSelection(
        trials=trials,
        candidate_count=len(all_actions),
        coset_count=len(representatives),
        short_cycle_supports=cycles,
        trapping_set_supports=trapping_sets,
        minimum_pairwise_decorrelation=minimum_decorrelation / denominator,
        pairwise_decorrelation=pairwise,
    )


def _declared_group_actions(
    graph: Graph,
) -> tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]:
    variables = graph.group.variable_permutations
    checks = graph.group.check_permutations
    if not variables or not checks:
        raise ValueError("group action metadata is required; automorphisms are not searched")
    if len(variables) != len(checks):
        raise ValueError("group variable/check action tables must have equal length")

    identity = (tuple(range(graph.num_variables)), tuple(range(len(graph.checks))))
    actions: set[tuple[tuple[int, ...], tuple[int, ...]]] = {identity}
    for index, (variable, check) in enumerate(zip(variables, checks)):
        action = (tuple(int(value) for value in variable),
                  tuple(int(value) for value in check))
        _validate_trial(graph, AutomorphismTrial(index, action[0], action[1]))
        _logical_action(graph, action[0])
        actions.add(action)
    return tuple(sorted(actions, key=lambda item: (item != identity, item[0], item[1])))


def _support_coset_representatives(
    actions: Sequence[tuple[tuple[int, ...], tuple[int, ...]]],
    supports: Sequence[tuple[int, ...]],
) -> tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]:
    """Use one canonical representative of each support-action stabilizer coset."""

    representatives: dict[tuple[tuple[int, ...], ...], tuple[tuple[int, ...], tuple[int, ...]]] = {}
    for action in actions:
        signature = tuple(tuple(action[0][variable] for variable in support)
                          for support in supports)
        representatives.setdefault(signature, action)
    identity = actions[0]
    ordered = sorted(representatives.values(), key=lambda item: (item != identity, item[0], item[1]))
    return tuple(ordered)


def _maximin_group_selection(
    actions: Sequence[tuple[tuple[int, ...], tuple[int, ...]]],
    supports: Sequence[tuple[int, ...]],
    trial_count: int,
    require_identity: bool,
) -> tuple[tuple[int, ...], int]:
    denominator = sum(len(support) for support in supports)
    if trial_count == 1:
        return ((0,) if require_identity else (0,), denominator)
    matrix = _pairwise_decorrelation_units(actions, supports)
    thresholds = sorted({matrix[left][right]
                         for left in range(len(actions))
                         for right in range(left + 1, len(actions))})
    low, high = 0, len(thresholds) - 1
    best: tuple[int, ...] | None = None
    best_threshold = 0
    while low <= high:
        middle = (low + high) // 2
        candidate = _find_threshold_clique(
            matrix, thresholds[middle], trial_count, require_identity,
        )
        if candidate is None:
            high = middle - 1
        else:
            best = candidate
            best_threshold = thresholds[middle]
            low = middle + 1
    if best is None:
        raise ValueError("unable to form the requested decorrelated group trial set")
    return best, best_threshold


def _find_threshold_clique(
    matrix: Sequence[Sequence[int]], threshold: int, trial_count: int,
    require_identity: bool,
) -> tuple[int, ...] | None:
    if require_identity:
        pool = tuple(index for index in range(1, len(matrix))
                     if matrix[0][index] >= threshold)
        tail = _find_clique(matrix, threshold, pool, trial_count - 1)
        return None if tail is None else (0,) + tail
    return _find_clique(matrix, threshold, tuple(range(len(matrix))), trial_count)


def _find_clique(
    matrix: Sequence[Sequence[int]], threshold: int, pool: Sequence[int],
    needed: int,
) -> tuple[int, ...] | None:
    if needed == 0:
        return ()
    if len(pool) < needed:
        return None
    for offset, candidate in enumerate(pool):
        compatible = tuple(
            other for other in pool[offset + 1:]
            if matrix[candidate][other] >= threshold
        )
        tail = _find_clique(matrix, threshold, compatible, needed - 1)
        if tail is not None:
            return (candidate,) + tail
    return None


def _pairwise_decorrelation_units(
    actions: Sequence[tuple[tuple[int, ...], tuple[int, ...]]],
    supports: Sequence[tuple[int, ...]],
) -> tuple[tuple[int, ...], ...]:
    matrix: list[list[int]] = [[0] * len(actions) for _ in actions]
    denominator = sum(len(support) for support in supports)
    for left, left_action in enumerate(actions):
        matrix[left][left] = denominator
        for right in range(left + 1, len(actions)):
            right_action = actions[right]
            difference = sum(
                int(left_action[0][variable] != right_action[0][variable])
                for support in supports for variable in support
            )
            matrix[left][right] = difference
            matrix[right][left] = difference
    return tuple(tuple(row) for row in matrix)


def _pairwise_decorrelation(
    actions: Sequence[tuple[tuple[int, ...], tuple[int, ...]]],
    supports: Sequence[tuple[int, ...]], denominator: int,
) -> tuple[tuple[float, ...], ...]:
    units = _pairwise_decorrelation_units(actions, supports)
    return tuple(tuple(value / denominator if left != right else 0.0
                       for right, value in enumerate(row))
                 for left, row in enumerate(units))


def _short_cycle_supports(
    graph: Graph, maximum_cycle_length: int,
    *, maximum_supports: int = 4096, maximum_walks: int = 200_000,
) -> tuple[tuple[int, ...], ...]:
    """Return a bounded deterministic catalogue of variable supports of short cycles."""

    variables = graph.num_variables
    adjacency: list[tuple[int, ...]] = []
    adjacency.extend(tuple(variables + check.id for check in graph.checks
                           if variable in check.neighbors)
                     for variable in range(variables))
    adjacency.extend(tuple(check.neighbors) for check in graph.checks)
    supports: set[tuple[int, ...]] = set()
    walks = 0

    def walk(start: int, node: int, path: tuple[int, ...]) -> None:
        nonlocal walks
        if len(supports) >= maximum_supports or walks >= maximum_walks:
            return
        edges = len(path) - 1
        if edges >= maximum_cycle_length:
            return
        for next_node in adjacency[node]:
            walks += 1
            next_edges = edges + 1
            if next_node == start:
                if next_edges >= 4 and next_edges % 2 == 0:
                    support = tuple(sorted(vertex for vertex in path if vertex < variables))
                    if 2 <= len(support) <= maximum_cycle_length // 2:
                        supports.add(support)
                continue
            if next_node in path:
                continue
            if next_node < variables and next_node < start:
                continue
            walk(start, next_node, path + (next_node,))
            if len(supports) >= maximum_supports or walks >= maximum_walks:
                return

    for start in range(variables):
        walk(start, start, (start,))
        if len(supports) >= maximum_supports or walks >= maximum_walks:
            break
    return tuple(sorted(supports, key=lambda item: (len(item), item)))


def _small_trapping_set_supports(
    graph: Graph,
    cycle_supports: Sequence[tuple[int, ...]],
    maximum_size: int,
    *, maximum_supports: int = 4096,
) -> tuple[tuple[int, ...], ...]:
    """Build bounded (a,b) trapping-set candidates from short-cycle neighborhoods."""

    def odd_checks(support: tuple[int, ...]) -> int:
        members = set(support)
        return sum(sum(variable in members for variable in check.neighbors) & 1
                   for check in graph.checks)

    seeds: set[tuple[int, ...]] = set(cycle_supports)
    if not seeds:
        for check in graph.checks:
            seeds.update(tuple(pair) for pair in combinations(sorted(check.neighbors), 2))
    candidates: set[tuple[int, ...]] = set()
    pending = list(sorted(seed for seed in seeds if len(seed) <= maximum_size))
    seen = set(pending)
    while pending and len(candidates) < maximum_supports:
        support = pending.pop(0)
        odd = odd_checks(support)
        if 0 < odd <= len(support):
            candidates.add(support)
        if len(support) >= maximum_size:
            continue
        members = set(support)
        neighbors = {
            variable for check in graph.checks
            if members.intersection(check.neighbors)
            for variable in check.neighbors
            if variable not in members
        }
        for variable in sorted(neighbors):
            expanded = tuple(sorted((*support, variable)))
            if expanded not in seen:
                seen.add(expanded)
                pending.append(expanded)
    return tuple(sorted(candidates, key=lambda item: (len(item), item)))


def _triangle_group_metadata(graph: Graph) -> GroupActionMetadata:
    if graph.num_variables != 3 or len(graph.checks) != 3 or \
            {frozenset(check.neighbors) for check in graph.checks} != {
                frozenset((0, 1)), frozenset((0, 2)), frozenset((1, 2))}:
        raise ValueError("default trial fixture is defined for a 3-variable triangle")
    check_by_support = {frozenset(check.neighbors): check.id for check in graph.checks}
    variables = tuple(tuple(action) for action in permutations(range(3)))
    checks = tuple(
        tuple(check_by_support[frozenset(action[variable] for variable in check.neighbors)]
              for check in graph.checks)
        for action in variables
    )
    return GroupActionMetadata(variables, checks)


def identity_and_cyclic_trials(graph: Graph) -> tuple[AutomorphismTrial, ...]:
    """Legacy triangle fixture, now selected from its analytic S3 action table."""

    source = graph if graph.group.variable_permutations else replace(
        graph, group=_triangle_group_metadata(graph),
    )
    selection = select_decorrelated_group_trials(
        source,
        prior_perturbations=(
            (0.0, 0.0, 0.0),
            (0.25, -0.25, 0.0),
            (-0.25, 0.0, 0.25),
            (0.0, 0.25, -0.25),
        ),
    )
    return selection.trials


# C06 offline concrete action table. FPGA consumes permutations, never group algebra.
ACTION_MAGIC = b"MTGA"
ACTION_VERSION = 1
ACTION_HEADER = struct.Struct("<4sBBHHHHBBHII")


@dataclass(frozen=True)
class CompiledAutomorphism:
    trial_id: int
    variable_permutation: tuple[int, ...]
    variable_inverse: tuple[int, ...]
    check_permutation: tuple[int, ...]
    check_inverse: tuple[int, ...]
    syndrome_permutation: tuple[int, ...]
    logical_permutation: tuple[int, ...]
    schedule_id: int = 0


@dataclass(frozen=True)
class CompiledAutomorphismSet:
    graph: Graph
    actions: tuple[CompiledAutomorphism, ...]
    raw: bytes
    distinct_decoder_paths: int


def compile_automorphism_trials(
    graph: Graph, trials: Sequence[AutomorphismTrial],
) -> CompiledAutomorphismSet:
    """Compile, validate, inverse-map, and pack concrete automorphism actions."""
    actions: list[CompiledAutomorphism] = []
    seen_ids: set[int] = set()
    seen_transforms: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
    for trial in trials:
        _validate_trial(graph, trial)
        if trial.trial_id in seen_ids:
            raise ValueError("duplicate automorphism trial ID")
        if trial.schedule_id < 0 or trial.schedule_id > 0xFFFF:
            raise ValueError("schedule ID must fit unsigned 16-bit table field")
        key = (tuple(trial.variable_permutation), tuple(trial.check_permutation))
        if key in seen_transforms:
            raise ValueError("duplicate automorphism transform")
        seen_ids.add(trial.trial_id)
        seen_transforms.add(key)
        variable = tuple(trial.variable_permutation)
        checks = tuple(trial.check_permutation)
        actions.append(CompiledAutomorphism(
            trial.trial_id, variable, _inverse_permutation(variable),
            checks, _inverse_permutation(checks), checks,
            _logical_action(graph, variable), trial.schedule_id,
        ))
    if not actions:
        raise ValueError("automorphism compiler requires at least one trial")
    raw = pack_automorphism_table(graph, tuple(actions))
    return CompiledAutomorphismSet(
        graph, tuple(actions), raw,
        len({(item.variable_permutation, item.check_permutation, item.schedule_id)
             for item in actions}),
    )


def pack_automorphism_table(
    graph: Graph, actions: Sequence[CompiledAutomorphism],
) -> bytes:
    if len(actions) > 0xFFFF:
        raise ValueError("automorphism table has too many trials")
    logical_count = len(graph.logical.seed_logicals)
    variable_width = max(1, (graph.num_variables - 1).bit_length())
    check_width = max(1, (len(graph.checks) - 1).bit_length())
    payload = bytearray()
    for action in actions:
        if action.trial_id < 0 or action.trial_id > 0xFFFF or \
                action.schedule_id < 0 or action.schedule_id > 0xFFFF:
            raise ValueError("automorphism table ID exceeds 16-bit field")
        _validate_compiled_action(graph, action)
        if len(action.logical_permutation) != logical_count:
            raise ValueError("logical permutation width mismatch")
        payload.extend(struct.pack("<HH", action.trial_id, action.schedule_id))
        for values, width, size in (
                (action.variable_permutation, variable_width, graph.num_variables),
                (action.variable_inverse, variable_width, graph.num_variables),
                (action.check_permutation, check_width, len(graph.checks)),
                (action.check_inverse, check_width, len(graph.checks)),
                (action.logical_permutation, max(1, (logical_count - 1).bit_length()), logical_count)):
            if len(values) != size:
                raise ValueError("automorphism permutation width mismatch")
            payload.extend(_pack_table_values(values, width))
    header = ACTION_HEADER.pack(
        ACTION_MAGIC, ACTION_VERSION, 0, graph.num_variables, len(graph.checks),
        len(actions), logical_count, variable_width, check_width, 16,
        len(payload), zlib.crc32(payload) & 0xFFFFFFFF,
    )
    return header + payload


def unpack_automorphism_table(
    graph: Graph, raw: bytes,
) -> CompiledAutomorphismSet:
    if len(raw) < ACTION_HEADER.size:
        raise ValueError("truncated automorphism table header")
    (magic, version, _flags, variables, checks, trial_count, logical_count,
     variable_width, check_width, schedule_width, payload_length, expected_crc) = \
        ACTION_HEADER.unpack(raw[:ACTION_HEADER.size])
    if magic != ACTION_MAGIC or version != ACTION_VERSION:
        raise ValueError("unsupported automorphism table")
    if (variables, checks) != (graph.num_variables, len(graph.checks)):
        raise ValueError("automorphism table graph dimensions mismatch")
    if logical_count != len(graph.logical.seed_logicals) or schedule_width != 16:
        raise ValueError("automorphism table metadata mismatch")
    if len(raw) != ACTION_HEADER.size + payload_length:
        raise ValueError("automorphism table payload length mismatch")
    payload = raw[ACTION_HEADER.size:]
    if zlib.crc32(payload) & 0xFFFFFFFF != expected_crc:
        raise ValueError("automorphism table CRC mismatch")
    logical_width = max(1, (logical_count - 1).bit_length())
    record_bytes = 4 + 2 * _table_bytes(variables, variable_width) + \
        2 * _table_bytes(checks, check_width) + _table_bytes(logical_count, logical_width)
    cursor = 0
    actions: list[CompiledAutomorphism] = []
    for _ in range(trial_count):
        if cursor + record_bytes > len(payload):
            raise ValueError("truncated automorphism table record")
        trial_id, schedule_id = struct.unpack_from("<HH", payload, cursor)
        cursor += 4
        arrays: list[tuple[int, ...]] = []
        for size, width in ((variables, variable_width), (variables, variable_width),
                            (checks, check_width), (checks, check_width),
                            (logical_count, logical_width)):
            count = _table_bytes(size, width)
            arrays.append(tuple(_unpack_table_values(payload[cursor:cursor + count], size, width)))
            cursor += count
        actions.append(CompiledAutomorphism(
            trial_id, arrays[0], arrays[1], arrays[2], arrays[3], arrays[2], arrays[4], schedule_id,
        ))
    if cursor != len(payload):
        raise ValueError("automorphism table trailing bytes")
    seen: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
    for action in actions:
        _validate_compiled_action(graph, action)
        key = (action.variable_permutation, action.check_permutation)
        if key in seen:
            raise ValueError("duplicate automorphism transform in table")
        seen.add(key)
    return CompiledAutomorphismSet(
        graph, tuple(actions), raw,
        len({(item.variable_permutation, item.check_permutation, item.schedule_id)
             for item in actions}),
    )


def apply_variable_action(values: Sequence[int], permutation: Sequence[int]) -> tuple[int, ...]:
    """Apply old-variable -> new-variable permutation."""
    if len(values) != len(permutation) or set(permutation) != set(range(len(values))):
        raise ValueError("variable action width/permutation mismatch")
    result = [0] * len(values)
    for old, new in enumerate(permutation):
        result[new] = int(values[old])
    return tuple(result)


def apply_inverse_variable_action(
    values: Sequence[int], action: CompiledAutomorphism,
) -> tuple[int, ...]:
    return apply_variable_action(values, action.variable_inverse)


def apply_syndrome_action(values: Sequence[int], action: CompiledAutomorphism) -> tuple[int, ...]:
    return apply_variable_action(values, action.syndrome_permutation)


def _logical_action(graph: Graph, variable: Sequence[int]) -> tuple[int, ...]:
    supports = tuple(tuple(sorted(signature)) for signature in graph.logical.seed_logicals)
    if not supports:
        return ()
    lookup = {support: index for index, support in enumerate(supports)}
    result: list[int] = []
    for support in supports:
        transformed = tuple(sorted(variable[index] for index in support))
        if transformed not in lookup:
            raise ValueError("automorphism does not preserve logical seed basis")
        result.append(lookup[transformed])
    if set(result) != set(range(len(supports))):
        raise ValueError("logical action is not a permutation")
    return tuple(result)


def _inverse_permutation(values: Sequence[int]) -> tuple[int, ...]:
    inverse = [0] * len(values)
    for old, new in enumerate(values):
        inverse[int(new)] = old
    return tuple(inverse)


def _validate_compiled_action(graph: Graph, action: CompiledAutomorphism) -> None:
    if not graph.preserves_graph(action.variable_permutation, action.check_permutation):
        raise ValueError("compiled action does not preserve graph")
    if tuple(action.variable_inverse[action.variable_permutation[index]] for index in range(graph.num_variables)) != \
            tuple(range(graph.num_variables)):
        raise ValueError("variable inverse is not identity")
    if tuple(action.check_inverse[action.check_permutation[index]] for index in range(len(graph.checks))) != \
            tuple(range(len(graph.checks))):
        raise ValueError("check inverse is not identity")
    if action.syndrome_permutation != action.check_permutation:
        raise ValueError("syndrome permutation must match check action")
    if action.logical_permutation != _logical_action(graph, action.variable_permutation):
        raise ValueError("logical action does not match graph metadata")


def _table_bytes(count: int, width: int) -> int:
    return (count * width + 7) // 8


def _pack_table_values(values: Sequence[int], width: int) -> bytes:
    accumulator = 0
    for index, value in enumerate(values):
        if value < 0 or value >= (1 << width):
            raise ValueError("permutation value does not fit table width")
        accumulator |= int(value) << (index * width)
    return accumulator.to_bytes(_table_bytes(len(values), width), "little")


def _unpack_table_values(raw: bytes, count: int, width: int) -> list[int]:
    accumulator = int.from_bytes(raw, "little")
    mask = (1 << width) - 1
    return [(accumulator >> (index * width)) & mask for index in range(count)]
