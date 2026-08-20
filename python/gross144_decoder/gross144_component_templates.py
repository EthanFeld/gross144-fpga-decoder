"""Translation-template compiler for the Gross144 circuit-component image.

The circuit decoder's component graph is too large for a direct full-image
implementation, but its graph is regular under the same ``Z_12 x Z_6`` action
as the Gross code. This compiler emits the compact template description needed
by a paged FPGA check engine: one check row per detector time slice, plus a
translation action, rather than 936 separately stored rows.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Sequence

from .gross144_memory import CHECKS_PER_TYPE, Gross144CircuitFpgaAdapter


@dataclass(frozen=True)
class ComponentTemplateImage:
    """Verified quotient image for one CSS circuit component."""

    basis: str
    variables: int
    checks: int
    edges: int
    group_order: int
    variable_orbits: int
    detector_time_templates: tuple[tuple[tuple[int, int], ...], ...]
    max_template_degree: int
    posterior_bank_count: int
    orbit_bank_colors: tuple[int, ...]
    banked_detector_time_templates: tuple[tuple[tuple[tuple[int, int, int, int], ...], ...], ...]
    max_banked_cycles: int
    two_lane_pair_coordinate_delta: int
    serialized_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "GROSS144-COMPONENT-TEMPLATE",
            "version": 2,
            "basis": self.basis,
            "variables": self.variables,
            "checks": self.checks,
            "edges": self.edges,
            "group_order": self.group_order,
            "variable_orbits": self.variable_orbits,
            "variable_indexing": "orbit_major_z12x6",
            "max_template_degree": self.max_template_degree,
            "detector_time_templates": [list(map(list, row))
                                        for row in self.detector_time_templates],
            "posterior_banking": {
                "bank_count": self.posterior_bank_count,
                "orbit_bank_colors": list(self.orbit_bank_colors),
                "bank_rule": "(orbit_color + variable_x_coordinate) mod bank_count",
                "translated_bank_rule": "(base_bank + check_x_coordinate) mod bank_count",
                "max_cycles_per_check": self.max_banked_cycles,
                # Each entry is [base_bank, template_edge_index, orbit,
                # anchor_coordinate].  A
                # sub-cycle has at most one entry per base bank; a translated
                # check simply rotates all four physical banks by its x value.
                "detector_time_templates": [
                    [list(map(list, cycle)) for cycle in row]
                    for row in self.banked_detector_time_templates
                ],
            },
            "two_lane_pairing": {
                "coordinate_delta": self.two_lane_pair_coordinate_delta,
                "rule": "pair (time, c) with (time, c + delta) for c in 0..35 under Z12xZ6",
                "guarantee": "paired checks have disjoint component-variable sets",
            },
            "serialized_sha256": self.serialized_sha256,
        }


def _bank_schedule(
    templates: Sequence[Sequence[tuple[int, int]]],
    *, orbit_count: int, basis: str, bank_count: int,
    require_lower_bound: bool = True,
) -> tuple[tuple[int, ...], tuple[tuple[tuple[tuple[int, int, int, int], ...], ...], ...]]:
    """Pack templates into conflict-free translation-stable bank beats.

    A component ID is ``(orbit, x, y)``.  Giving each orbit a colour and
    banking by ``colour + x (mod bank_count)`` means a Gross translation only
    rotates bank names; it cannot introduce a conflict.
    """

    if bank_count < 1:
        raise ValueError("bank count must be positive")
    rows = tuple(tuple((orbit, anchor // 6) for orbit, anchor in row)
                 for row in templates)

    def objective(colors: Sequence[int]) -> tuple[int, int]:
        maxima: list[int] = []
        for row in rows:
            occupancy = [0] * bank_count
            for orbit, anchor_x in row:
                occupancy[(colors[orbit] + anchor_x) % bank_count] += 1
            maxima.append(max(occupancy, default=0))
        return max(maxima, default=0), sum(value * value for value in maxima)

    # A fixed, basis-separated seed makes the compact image reproducible.
    # This very small local search consistently reaches the mathematical
    # lower bound, but its bounded loop also prevents an image build hanging
    # if a future code has incompatible template structure.
    seed = 0x475258 if basis == "X" else 0x47525A
    rng = random.Random(seed)
    colors = [rng.randrange(bank_count) for _ in range(orbit_count)]
    current = objective(colors)
    lower_bound = max((len(row) + bank_count - 1) // bank_count for row in rows)
    for _attempt in range(10_000):
        if current[0] == lower_bound:
            break
        orbit = rng.randrange(orbit_count)
        old = colors[orbit]
        candidate = rng.randrange(bank_count - 1)
        if candidate >= old:
            candidate += 1
        colors[orbit] = candidate
        trial = objective(colors)
        # A tiny deterministic escape probability avoids shallow local
        # minima while preserving the fixed output for the frozen templates.
        if trial <= current or rng.random() < 0.003:
            current = trial
        else:
            colors[orbit] = old
    if require_lower_bound and current[0] != lower_bound:
        raise ValueError(
            f"unable to find {bank_count}-bank lower-bound schedule: {current[0]} > {lower_bound}"
        )

    packed_rows: list[tuple[tuple[tuple[int, int, int, int], ...], ...]] = []
    for row in templates:
        by_bank: list[list[tuple[int, int, int]]] = [[] for _ in range(bank_count)]
        for edge_index, (orbit, anchor) in enumerate(row):
            base_bank = (colors[orbit] + anchor // 6) % bank_count
            by_bank[base_bank].append((edge_index, orbit, anchor))
        cycles: list[tuple[tuple[int, int, int, int], ...]] = []
        for beat in range(max(map(len, by_bank), default=0)):
            cycle = tuple((bank, by_bank[bank][beat][0], by_bank[bank][beat][1],
                           by_bank[bank][beat][2])
                          for bank in range(bank_count) if beat < len(by_bank[bank]))
            if len({entry[0] for entry in cycle}) != len(cycle):  # pragma: no cover - construction invariant
                raise ValueError("component bank schedule contains a same-cycle conflict")
            cycles.append(cycle)
        if sorted((orbit, anchor) for cycle in cycles for _bank, _edge, orbit, anchor in cycle) != sorted(row):
            raise ValueError("component bank schedule lost a template edge")
        packed_rows.append(tuple(cycles))

    return tuple(colors), tuple(packed_rows)


def _four_bank_schedule(
    templates: Sequence[Sequence[tuple[int, int]]],
    *, orbit_count: int, basis: str,
) -> tuple[tuple[int, ...], tuple[tuple[tuple[tuple[int, int, int, int], ...], ...], ...]]:
    """Compatibility wrapper for established four-bank component images."""

    return _bank_schedule(
        templates, orbit_count=orbit_count, basis=basis, bank_count=4,
        require_lower_bound=True,
    )


def _full_component_actions(
    adapter: Gross144CircuitFpgaAdapter, basis: str,
) -> tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]:
    """Lift every analytic Gross translation, not just the frozen four S1A trials."""

    layout = adapter._layouts[basis]
    error_type = "Z" if basis == "X" else "X"
    base_graph, _ = adapter.code.graph(error_type)
    mask_to_variable = {mask: index for index, mask in enumerate(layout.detector_masks)}
    actions: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    for base_variables, base_checks in zip(base_graph.group.variable_permutations,
                                           base_graph.group.check_permutations):
        variables = tuple(
            mask_to_variable[adapter._map_detector_mask(mask, base_checks)]
            for mask in layout.detector_masks
        )
        checks = tuple(
            time * CHECKS_PER_TYPE + int(base_checks[check])
            for time in range(adapter.config.rounds + 1)
            for check in range(CHECKS_PER_TYPE)
        )
        if not layout.graph.preserves_graph(variables, checks):
            raise ValueError("component template action is not a graph automorphism")
        actions.append((variables, checks))
    if len({action[0] for action in actions}) != len(actions):
        raise ValueError("Gross translation table contains duplicate component actions")
    return tuple(actions)


def compile_component_templates(
    adapter: Gross144CircuitFpgaAdapter, *, basis: str,
) -> ComponentTemplateImage:
    """Compile and validate a compact time-template image for one basis."""

    if basis not in ("X", "Z"):
        raise ValueError("basis must be X or Z")
    layout = adapter._layouts[basis]
    actions = _full_component_actions(adapter, basis)
    # Actions are analytic translations.  Index each one by the destination
    # of check zero, which is the compact spatial coordinate used by a
    # template streamer.
    by_check_coordinate = {
        checks[0]: variables for variables, checks in actions
    }
    if set(by_check_coordinate) != set(range(CHECKS_PER_TYPE)):
        raise ValueError("component translations do not transitively cover check coordinates")

    unassigned = set(range(layout.graph.num_variables))
    orbit_id_by_variable: dict[int, int] = {}
    orbit_representatives: list[int] = []
    orbit_count = 0
    while unassigned:
        representative = min(unassigned)
        orbit = {variables[representative] for variables, _checks in actions}
        if len(orbit) != len(actions):
            raise ValueError("component translation has a non-free variable orbit")
        for variable in orbit:
            orbit_id_by_variable[variable] = orbit_count
        unassigned.difference_update(orbit)
        orbit_representatives.append(representative)
        orbit_count += 1

    def add_coordinates(left: int, right: int) -> int:
        left_x, left_y = divmod(left, 6)
        right_x, right_y = divmod(right, 6)
        return ((left_x + right_x) % 12) * 6 + ((left_y + right_y) % 6)

    # Give every component variable the address (orbit, translation). This is
    # a 14-bit contiguous FPGA ID, and makes a translated template neighbor a
    # small modular addition instead of a 72-by-8,784 permutation table.
    coordinate_by_variable: dict[int, int] = {}
    for orbit_id, representative in enumerate(orbit_representatives):
        for coordinate, action in by_check_coordinate.items():
            variable = action[representative]
            if orbit_id_by_variable[variable] != orbit_id:
                raise ValueError("component translation escaped its variable orbit")
            coordinate_by_variable[variable] = coordinate
    if len(coordinate_by_variable) != layout.graph.num_variables:
        raise ValueError("component orbit-coordinate reindexing is incomplete")
    reindexed_variable = {
        variable: orbit_id_by_variable[variable] * CHECKS_PER_TYPE + coordinate_by_variable[variable]
        for variable in range(layout.graph.num_variables)
    }

    templates: list[tuple[tuple[int, int], ...]] = []
    for time in range(adapter.config.rounds + 1):
        row = layout.graph.checks[time * CHECKS_PER_TYPE]
        # Store orbit plus the Z12xZ6 anchor coordinate. At spatial coordinate
        # c the streamer adds c to this coordinate and emits an orbit-major
        # variable address; no full permutation table is needed in hardware.
        template = tuple((orbit_id_by_variable[variable], coordinate_by_variable[variable])
                         for variable in row.neighbors)
        templates.append(template)
        for coordinate, action in by_check_coordinate.items():
            generated = tuple(
                orbit * CHECKS_PER_TYPE + add_coordinates(anchor_coordinate, coordinate)
                for orbit, anchor_coordinate in template
            )
            expected = layout.graph.checks[time * CHECKS_PER_TYPE + coordinate].neighbors
            # The flat graph assigns IDs globally, so its stored edge order
            # is not translation-covariant.  A check update is edge-order
            # independent once each generated variable ID is retained; the
            # template therefore validates the exact neighbor set.
            expected_reindexed = tuple(reindexed_variable[variable] for variable in expected)
            if sorted(generated) != sorted(expected_reindexed):
                raise ValueError("component template does not reproduce an exact check row")

    orbit_bank_colors, banked_templates = _four_bank_schedule(
        templates, orbit_count=orbit_count, basis=basis,
    )
    for check_coordinate in range(CHECKS_PER_TYPE):
        check_x = check_coordinate // 6
        for template, cycles in zip(templates, banked_templates):
            expected = {
                (orbit, anchor): (orbit_bank_colors[orbit] + anchor // 6 + check_x) % 4
                for orbit, anchor in template
            }
            observed = {
                (orbit, anchor): (base_bank + check_x) % 4
                for cycle in cycles for base_bank, _edge, orbit, anchor in cycle
            }
            if expected != observed:
                raise ValueError("translated component bank schedule is not address-stable")

    # Translation by x=6 is an involution on Z12xZ6, so spatial coordinates
    # 0..35 pair exactly once with 36..71.  It is a strict no-write-conflict
    # proof for the two physical check engines, not a runtime heuristic.
    pair_delta = 36
    for time_index in range(adapter.config.rounds + 1):
        for coordinate in range(CHECKS_PER_TYPE // 2):
            left = set(layout.graph.checks[time_index * CHECKS_PER_TYPE + coordinate].neighbors)
            right = set(layout.graph.checks[time_index * CHECKS_PER_TYPE + coordinate + pair_delta].neighbors)
            if left & right:
                raise ValueError("Gross144 two-lane pair has a posterior-RAM write conflict")

    payload = {
        "basis": basis,
        "variables": layout.graph.num_variables,
        "checks": len(layout.graph.checks),
        "edges": layout.graph.edge_count(),
        "group_order": len(actions),
        "variable_orbits": orbit_count,
        "templates": templates,
        "posterior_bank_count": 4,
        "orbit_bank_colors": orbit_bank_colors,
        "banked_templates": banked_templates,
        "two_lane_pair_coordinate_delta": pair_delta,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    return ComponentTemplateImage(
        basis=basis,
        variables=layout.graph.num_variables,
        checks=len(layout.graph.checks),
        edges=layout.graph.edge_count(),
        group_order=len(actions),
        variable_orbits=orbit_count,
        detector_time_templates=tuple(templates),
        max_template_degree=max(map(len, templates)),
        posterior_bank_count=4,
        orbit_bank_colors=orbit_bank_colors,
        banked_detector_time_templates=banked_templates,
        max_banked_cycles=max(map(len, banked_templates)),
        two_lane_pair_coordinate_delta=pair_delta,
        serialized_sha256=digest,
    )
