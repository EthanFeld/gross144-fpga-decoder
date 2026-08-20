"""Exact quotient-image compiler for the paper Gross144 Stage-1 graph.

The cited S1 graph has 8,784 variables and 936 checks, but it is invariant
under the 72-element ``Z_12 x Z_6`` Gross translation group.  This module
stores one row for each of the 13 detector-time types and reconstructs the
other 71 spatial rows with a modular address calculation.  It is a graph
storage transformation only: every generated check has exactly the same
variable-neighbour set as the frozen Relay fixture.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .gross144 import build_gross144
from .gross144_component_templates import ComponentTemplateImage, _bank_schedule
from .paper_gross144 import PaperGross144Stage1Layout, load_paper_gross144_stage1_layout


CHECKS_PER_SPATIAL_SLICE = 72
GROUP_ORDER = 72
POSTERIOR_BITS = 11
WARMUP_MESSAGE_BITS = 5
CHECK_RECORD_FIXED_BITS = 14  # min1, min2, argmin


def _add_coordinates(left: int, right: int) -> int:
    """Add two ``Z_12 x Z_6`` coordinates encoded as ``x * 6 + y``."""

    left_x, left_y = divmod(left, 6)
    right_x, right_y = divmod(right, 6)
    return ((left_x + right_x) % 12) * 6 + ((left_y + right_y) % 6)


def _full_translation_actions(
    layout: PaperGross144Stage1Layout,
) -> tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]:
    """Lift all 72 declared Gross translations through frozen paper S1."""

    error_type = "Z" if layout.basis == "X" else "X"
    base_graph, _ = build_gross144().graph(error_type)
    if len(base_graph.group.variable_permutations) != GROUP_ORDER:
        raise ValueError("Gross144 base translation group drifted")
    if len(layout.graph.checks) % CHECKS_PER_SPATIAL_SLICE:
        raise ValueError("paper check count is not an integral spatial-time grid")

    masks = [0] * layout.graph.num_variables
    for check in layout.graph.checks:
        for variable in check.neighbors:
            masks[variable] |= 1 << check.id
    key_to_variable: dict[tuple[int, float], int] = {}
    for variable, mask in enumerate(masks):
        key = (mask, round(layout.prior_llr[variable], 12))
        if key in key_to_variable:
            raise ValueError("paper S1 has an ambiguous translation response")
        key_to_variable[key] = variable

    time_slices = len(layout.graph.checks) // CHECKS_PER_SPATIAL_SLICE
    actions: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    for base_variables, base_checks in zip(
        base_graph.group.variable_permutations, base_graph.group.check_permutations,
    ):
        check_action = tuple(
            time_index * CHECKS_PER_SPATIAL_SLICE + int(base_checks[check_id])
            for time_index in range(time_slices)
            for check_id in range(CHECKS_PER_SPATIAL_SLICE)
        )
        variable_action: list[int] = []
        for mask in masks:
            mapped_mask = 0
            remaining = mask
            while remaining:
                low_bit = remaining & -remaining
                detector = low_bit.bit_length() - 1
                time_index, check_id = divmod(detector, CHECKS_PER_SPATIAL_SLICE)
                mapped_mask |= 1 << (
                    time_index * CHECKS_PER_SPATIAL_SLICE + int(base_checks[check_id])
                )
                remaining ^= low_bit
            try:
                variable_action.append(
                    key_to_variable[(mapped_mask, round(layout.prior_llr[len(variable_action)], 12))]
                )
            except KeyError as exc:
                raise ValueError("paper S1 translation left frozen DEM image") from exc
        action = tuple(variable_action)
        if len(set(action)) != layout.graph.num_variables or not layout.graph.preserves_graph(
            action, check_action,
        ):
            raise ValueError("paper S1 full translation failed graph validation")
        actions.append((action, check_action))
    if len({action[0] for action in actions}) != GROUP_ORDER:
        raise ValueError("paper S1 translation table contains duplicate actions")
    return tuple(actions)


def compile_paper_stage1_component_templates(
    relay_root: Path | str, *, p: float, basis: str, banks: int = 4,
    allow_suboptimal_bank_schedule: bool = False,
) -> ComponentTemplateImage:
    """Compile and validate exact Relay-paper S1 templates for one basis."""

    if banks < 1:
        raise ValueError("bank count must be positive")
    layout = load_paper_gross144_stage1_layout(relay_root, p=p, basis=basis)
    actions = _full_translation_actions(layout)
    by_check_coordinate = {checks[0]: variables for variables, checks in actions}
    if set(by_check_coordinate) != set(range(CHECKS_PER_SPATIAL_SLICE)):
        raise ValueError("paper translations do not transitively cover spatial checks")

    unassigned = set(range(layout.graph.num_variables))
    orbit_by_variable: dict[int, int] = {}
    representatives: list[int] = []
    while unassigned:
        representative = min(unassigned)
        orbit = {variables[representative] for variables, _checks in actions}
        if len(orbit) != GROUP_ORDER:
            raise ValueError("paper variable orbit is not free under translations")
        orbit_id = len(representatives)
        for variable in orbit:
            orbit_by_variable[variable] = orbit_id
        unassigned.difference_update(orbit)
        representatives.append(representative)

    coordinate_by_variable: dict[int, int] = {}
    for orbit_id, representative in enumerate(representatives):
        for coordinate, variables in by_check_coordinate.items():
            variable = variables[representative]
            if orbit_by_variable[variable] != orbit_id:
                raise ValueError("paper translation escaped variable orbit")
            coordinate_by_variable[variable] = coordinate
    if len(coordinate_by_variable) != layout.graph.num_variables:
        raise ValueError("paper orbit-coordinate map is incomplete")
    reindexed_variable = {
        variable: orbit_by_variable[variable] * GROUP_ORDER + coordinate_by_variable[variable]
        for variable in range(layout.graph.num_variables)
    }

    time_slices = len(layout.graph.checks) // CHECKS_PER_SPATIAL_SLICE
    templates: list[tuple[tuple[int, int], ...]] = []
    for time_index in range(time_slices):
        row = layout.graph.checks[time_index * CHECKS_PER_SPATIAL_SLICE]
        template = tuple(
            (orbit_by_variable[variable], coordinate_by_variable[variable])
            for variable in row.neighbors
        )
        templates.append(template)
        for coordinate, variables in by_check_coordinate.items():
            generated = tuple(
                orbit * GROUP_ORDER + _add_coordinates(anchor, coordinate)
                for orbit, anchor in template
            )
            expected = tuple(
                reindexed_variable[variable]
                for variable in layout.graph.checks[
                    time_index * CHECKS_PER_SPATIAL_SLICE + coordinate
                ].neighbors
            )
            if sorted(generated) != sorted(expected):
                raise ValueError("paper template fails exact translated-row reconstruction")

    colors, banked_templates = _bank_schedule(
        templates, orbit_count=len(representatives), basis=basis, bank_count=banks,
        require_lower_bound=not allow_suboptimal_bank_schedule,
    )
    for check_coordinate in range(CHECKS_PER_SPATIAL_SLICE):
        check_x = check_coordinate // 6
        for template, cycles in zip(templates, banked_templates):
            expected = {
                (orbit, anchor): (colors[orbit] + anchor // 6 + check_x) % banks
                for orbit, anchor in template
            }
            observed = {
                (orbit, anchor): (base_bank + check_x) % banks
                for cycle in cycles for base_bank, _edge, orbit, anchor in cycle
            }
            if expected != observed:
                raise ValueError("paper bank schedule is not translation-stable")

    for time_index in range(time_slices):
        for coordinate in range(CHECKS_PER_SPATIAL_SLICE // 2):
            left = set(layout.graph.checks[
                time_index * CHECKS_PER_SPATIAL_SLICE + coordinate
            ].neighbors)
            right = set(layout.graph.checks[
                time_index * CHECKS_PER_SPATIAL_SLICE + coordinate + 36
            ].neighbors)
            if left & right:
                raise ValueError("paper two-lane check pair has a posterior write conflict")

    payload = {
        "basis": basis,
        "p": p,
        "variables": layout.graph.num_variables,
        "checks": len(layout.graph.checks),
        "edges": layout.graph.edge_count(),
        "group_order": GROUP_ORDER,
        "variable_orbits": len(representatives),
        "templates": templates,
        "posterior_bank_count": banks,
        "orbit_bank_colors": colors,
        "banked_templates": banked_templates,
        "two_lane_pair_coordinate_delta": 36,
    }
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return ComponentTemplateImage(
        basis=basis,
        variables=layout.graph.num_variables,
        checks=len(layout.graph.checks),
        edges=layout.graph.edge_count(),
        group_order=GROUP_ORDER,
        variable_orbits=len(representatives),
        detector_time_templates=tuple(templates),
        max_template_degree=max(map(len, templates)),
        posterior_bank_count=banks,
        orbit_bank_colors=colors,
        banked_detector_time_templates=banked_templates,
        max_banked_cycles=max(map(len, banked_templates)),
        two_lane_pair_coordinate_delta=36,
        serialized_sha256=digest,
    )


def paper_stage1_storage_plan(
    image: ComponentTemplateImage, *, bram_block_bits: int = 18_432,
) -> dict[str, int | float]:
    """Exact-state memory budget for a banked paper-S1 implementation.

    Warm-up messages retain the two fixed box-plus passes.  Afterwards each
    check replaces its edge messages with min1/min2/argmin/sign state.  The
    plan therefore does not omit decoder state or change iteration count.
    """

    if image.group_order != GROUP_ORDER or image.posterior_bank_count < 1:
        raise ValueError("storage plan requires a banked 72-translation image")
    if bram_block_bits < 1:
        raise ValueError("BRAM block width must be positive")
    address_bits = math.ceil(math.log2(image.variables))
    template_edges = sum(map(len, image.detector_time_templates))
    bank_bits = max(1, math.ceil(math.log2(image.posterior_bank_count)))
    template_bits = template_edges * (7 + 7 + bank_bits + 6) + image.variable_orbits * bank_bits + \
        len(image.detector_time_templates) * 6
    explicit_topology_bits = image.edges * address_bits
    posterior_entries_per_bank = image.variables // image.posterior_bank_count
    warmup_edges_per_bank = image.edges // image.posterior_bank_count
    record_bits = sum(
        (CHECK_RECORD_FIXED_BITS + len(row)) * GROUP_ORDER
        for row in image.detector_time_templates
    )
    posterior_brams = image.posterior_bank_count * math.ceil(
        posterior_entries_per_bank * POSTERIOR_BITS / bram_block_bits
    )
    warmup_brams = image.posterior_bank_count * math.ceil(
        warmup_edges_per_bank * WARMUP_MESSAGE_BITS / bram_block_bits
    )
    record_brams = math.ceil(record_bits / bram_block_bits)
    template_brams = math.ceil(template_bits / bram_block_bits)
    return {
        "address_bits": address_bits,
        "explicit_topology_bits": explicit_topology_bits,
        "template_topology_bits": template_bits,
        "topology_compression_ratio": explicit_topology_bits / template_bits,
        "posterior_bits": image.variables * POSTERIOR_BITS,
        "warmup_message_bits": image.edges * WARMUP_MESSAGE_BITS,
        "compressed_check_record_bits": record_bits,
        "posterior_bram_blocks": posterior_brams,
        "warmup_message_bram_blocks": warmup_brams,
        "compressed_record_bram_blocks": record_brams,
        "template_bram_blocks": template_brams,
        "total_decoder_bram_blocks": posterior_brams + warmup_brams + record_brams + template_brams,
    }


def paper_stage1_cycle_plan(
    image: ComponentTemplateImage, *, warmup_iterations: int = 2,
    minsum_iterations: int = 6, clock_hz: float = 45_000_000.0,
) -> dict[str, int | float]:
    """Conservative one-engine S1 cycle plan for the exact quotient image.

    Warm-up uses the exact left-fold transducer scan: four-bank gather/scatter,
    then one prefix and one 32-state suffix-transition update per edge.  The
    six normalized-min-sum sweeps use the already verified four-bank stream
    primitive.  No two-lane overlap is credited, so this is a lower-risk
    throughput floor rather than an optimistic paired-engine number.
    """

    if warmup_iterations != 2 or minsum_iterations < 0 or clock_hz <= 0.0:
        raise ValueError("paper FPGA S1 requires two warm-ups and a positive clock")
    banks = image.posterior_bank_count
    warmup_per_pass = sum(
        2 * math.ceil(len(row) / banks) + 2 * len(row) + 2
        for row in image.detector_time_templates
    ) * GROUP_ORDER
    minsum_per_pass = sum(
        2 * math.ceil(len(row) / banks) + 2
        for row in image.detector_time_templates
    ) * GROUP_ORDER
    cycles_per_window = warmup_iterations * warmup_per_pass + minsum_iterations * minsum_per_pass
    seconds_per_window = cycles_per_window / clock_hz
    return {
        "clock_hz": clock_hz,
        "warmup_iterations": warmup_iterations,
        "minsum_iterations": minsum_iterations,
        "warmup_cycles_per_pass": warmup_per_pass,
        "minsum_cycles_per_pass": minsum_per_pass,
        "cycles_per_window": cycles_per_window,
        "microseconds_per_syndrome_round": seconds_per_window * 1e6 / 12,
        "syndrome_rounds_per_second": 12 / seconds_per_window,
    }
