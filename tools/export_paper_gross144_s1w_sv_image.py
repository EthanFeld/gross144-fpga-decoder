"""Export a frozen paper S1 quotient image as fixed-width FPGA ROM files.

The JSON image remains the source-of-truth interchange format.  This tool
turns its compiler-checked topology into the exact-width ``$readmemb`` images
consumed by the production controller ROM closure:

* meta: ``degree[5:0] | beats[3:0]`` (10 bits, one word/time type)
* lanes: ``present | edge[5:0] | orbit[6:0] | anchor[6:0]`` (21 bits)
* orbit-config: five 11-bit orbit priors (scales 1..5), two-bit colour,
  three-bit logical-pattern ID.

All ``MAX_BANKED_BEATS * 4`` lane positions are emitted, including zeroed
padding.  Therefore the physical image has a fixed address map and cannot
silently depend on JSON list lengths at hardware build time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from mitten.hybrid_warmup import quantize_llr  # noqa: E402
from mitten.paper_gross144 import load_paper_gross144_stage1_layout  # noqa: E402
from mitten.paper_gross144_component_templates import _full_translation_actions  # noqa: E402
from mitten.paper_gross144_hash import (  # noqa: E402
    compile_residual_hash_image,
    gf2_word_rank,
    transform_hash,
)

TIME_SLICES = 13
MAX_BANKED_BEATS = 9
BANKS = 4
ORBIT_COUNT = 122
GROUP_ORDER = 72
POSTERIOR_WIDTH = 11
ORBIT_CONFIG_WIDTH = (5 * POSTERIOR_WIDTH) + 2 + 3


def _write_words(path: Path, words: list[int], width: int) -> str:
    if any(word < 0 or word >= (1 << width) for word in words):
        raise ValueError(f"word outside {width}-bit ROM width for {path.name}")
    payload = "".join(f"{word:0{width}b}\n" for word in words)
    path.write_text(payload, encoding="ascii")
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _project_fpga_hash(value: int, width: int) -> int:
    """Keep complete equivariant bit blocks for the resource-bounded FPGA gate.

    The 32-bit paper hash is four independent rotation blocks (12, 6, 12, 2).
    The 20-bit FPGA image keeps the complete 12-, 6-, and 2-bit blocks, so
    translation remains a permutation within the projected word.
    """

    value &= 0xFFFF_FFFF
    if width == 32:
        return value
    if width == 20:
        return (value & 0xFFF) | (((value >> 12) & 0x3F) << 12) | \
               (((value >> 30) & 0x3) << 18)
    raise ValueError("FPGA hash width must be 20 or 32")


def _reindexed_layout(*, relay_root: Path, p: float, basis: str):
    """Return the paper layout and exact compact variable-index maps.

    The compact topology image deliberately does not duplicate the 8,784
    source-variable IDs.  Reconstructing the same translation/orbit map here
    keeps the ROM coupled to the compiler-checked fixture, rather than to an
    incidental order in a JSON list.
    """

    layout = load_paper_gross144_stage1_layout(relay_root, p=p, basis=basis)
    actions = _full_translation_actions(layout)
    by_coordinate = {checks[0]: variables for variables, checks in actions}
    if set(by_coordinate) != set(range(GROUP_ORDER)):
        raise ValueError("paper translation table cannot index fixed priors")

    unassigned = set(range(layout.graph.num_variables))
    orbit_by_variable: dict[int, int] = {}
    representatives: list[int] = []
    while unassigned:
        representative = min(unassigned)
        orbit = {variables[representative] for variables, _checks in actions}
        if len(orbit) != GROUP_ORDER:
            raise ValueError("paper prior orbit is not free under translations")
        orbit_id = len(representatives)
        orbit_by_variable.update((variable, orbit_id) for variable in orbit)
        unassigned.difference_update(orbit)
        representatives.append(representative)
    if len(representatives) != ORBIT_COUNT:
        raise ValueError("paper fixed-prior orbit count drifted")

    coordinate_by_variable: dict[int, int] = {}
    for orbit_id, representative in enumerate(representatives):
        for coordinate, variables in by_coordinate.items():
            variable = variables[representative]
            if orbit_by_variable[variable] != orbit_id:
                raise ValueError("paper fixed-prior coordinate escaped its orbit")
            coordinate_by_variable[variable] = coordinate

    return layout, orbit_by_variable, coordinate_by_variable


def _reindexed_fixed_priors(*, relay_root: Path, p: float, basis: str,
                            scale: int = 4) -> list[int]:
    """Return exact fixed-point priors in ``orbit * 72 + coordinate`` order."""

    layout, orbit_by_variable, coordinate_by_variable = _reindexed_layout(
        relay_root=relay_root, p=p, basis=basis,
    )
    priors = [None] * layout.graph.num_variables
    for variable, prior in enumerate(layout.prior_llr):
        index = orbit_by_variable[variable] * GROUP_ORDER + coordinate_by_variable[variable]
        if priors[index] is not None:
            raise ValueError("paper fixed-prior ROM index collision")
        value = quantize_llr(prior, scale=scale)
        if not -(1 << (POSTERIOR_WIDTH - 1)) <= value < (1 << (POSTERIOR_WIDTH - 1)):
            raise ValueError("paper fixed prior is outside posterior width")
        priors[index] = value & ((1 << POSTERIOR_WIDTH) - 1)
    if any(value is None for value in priors):
        raise ValueError("paper fixed-prior ROM is incomplete")
    return [int(value) for value in priors]


def _orbit_priors(*, relay_root: Path, p: float, basis: str,
                  scale: int) -> list[int]:
    """Compress an exact prior image to one constant per translation orbit."""

    full = _reindexed_fixed_priors(
        relay_root=relay_root, p=p, basis=basis, scale=scale,
    )
    result = []
    for orbit in range(ORBIT_COUNT):
        values = full[orbit * GROUP_ORDER:(orbit + 1) * GROUP_ORDER]
        if len(set(values)) != 1:
            raise ValueError(f"scale-{scale} priors are not orbit-constant")
        result.append(values[0])
    return result


def _reindexed_logical_masks(*, relay_root: Path, p: float, basis: str) -> list[int]:
    """Return host-scoring logical masks in compact posterior order."""

    layout, orbit_by_variable, coordinate_by_variable = _reindexed_layout(
        relay_root=relay_root, p=p, basis=basis,
    )
    masks: list[int | None] = [None] * layout.graph.num_variables
    for variable in range(layout.graph.num_variables):
        index = orbit_by_variable[variable] * GROUP_ORDER + coordinate_by_variable[variable]
        masks[index] = sum(
            (int(logical[variable]) & 1) << logical_index
            for logical_index, logical in enumerate(layout.logical_signatures)
        )
    if any(value is None for value in masks):
        raise ValueError("paper compact logical-mask image is incomplete")
    return [int(value) for value in masks]


def _compress_logical_masks(masks: list[int]) -> tuple[list[int], list[int]]:
    """Return orbit pattern IDs and a deduplicated 72-coordinate dictionary."""

    patterns: list[tuple[int, ...]] = []
    pattern_ids: list[int] = []
    for orbit in range(ORBIT_COUNT):
        pattern = tuple(masks[orbit * GROUP_ORDER:(orbit + 1) * GROUP_ORDER])
        try:
            pattern_id = patterns.index(pattern)
        except ValueError:
            pattern_id = len(patterns)
            patterns.append(pattern)
        pattern_ids.append(pattern_id)
    if len(patterns) > 8:
        raise ValueError("logical-mask orbit dictionary no longer fits three-bit IDs")
    return pattern_ids, [word for pattern in patterns for word in pattern]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path,
                        default=ROOT / "artifacts" / "paper_gross144_s1_templates_p002" /
                        "paper_gross144_s1_X.json")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "build" / "generated" / "paper_gross144_s1w_x_p002")
    parser.add_argument("--relay-root", type=Path, default=ROOT / "build" / "relay",
                        help="checked-out Relay fixture root used to derive exact priors")
    parser.add_argument("--p", type=float, default=0.002,
                        help="physical error rate of the source template fixture")
    parser.add_argument("--fpga-hash-width", type=int, choices=(20, 32), default=20,
                        help="equivariant residual-hash width emitted for FPGA ROMs")
    args = parser.parse_args()

    image = json.loads(args.image.read_text(encoding="utf-8"))
    banking = image.get("posterior_banking", {})
    templates = banking.get("detector_time_templates")
    colors = banking.get("orbit_bank_colors")
    if image.get("schema") != "GROSS144-COMPONENT-TEMPLATE" or image.get("version") != 2:
        raise ValueError("not the frozen Paper Gross144 component-template image")
    if (image.get("group_order"), image.get("variable_orbits"), banking.get("bank_count")) != \
            (72, ORBIT_COUNT, BANKS):
        raise ValueError("paper S1 quotient dimensions drifted")
    if len(templates) != TIME_SLICES or len(colors) != ORBIT_COUNT:
        raise ValueError("paper S1 image dimensions drifted")
    p = args.p
    fpga_hash_width = args.fpga_hash_width
    basis = str(image.get("basis"))
    if p not in (0.001, 0.002) or basis not in ("X", "Z"):
        raise ValueError("paper S1 image has no supported fixture identity")

    meta_words: list[int] = []
    lane_words: list[int] = []
    hash_lane_words: list[int] = []
    residual_hash = compile_residual_hash_image(image)
    for time_index, (template, beats) in enumerate(zip(
        image["detector_time_templates"], templates,
    )):
        degree = len(template)
        if not 1 <= degree <= 35 or not 1 <= len(beats) <= MAX_BANKED_BEATS:
            raise ValueError(f"invalid template shape at time {time_index}")
        meta_words.append((degree << 4) | len(beats))
        seen_edges: set[int] = set()
        for beat_index in range(MAX_BANKED_BEATS):
            entries = beats[beat_index] if beat_index < len(beats) else ()
            seen_banks: set[int] = set()
            for slot in range(BANKS):
                if slot >= len(entries):
                    lane_words.append(0)
                    hash_lane_words.append(0)
                    continue
                bank, edge, orbit, anchor = map(int, entries[slot])
                # A beat may skip a physical bank, so lane position is a
                # dense serialized slot, not the bank number.  The RTL
                # recomputes the translated physical bank from orbit/anchor.
                if bank in seen_banks or edge in seen_edges:
                    raise ValueError(f"non-canonical bank schedule at time {time_index}")
                if not (0 <= edge < degree and 0 <= orbit < ORBIT_COUNT and 0 <= anchor < 72):
                    raise ValueError(f"lane out of range at time {time_index}")
                seen_banks.add(bank)
                seen_edges.add(edge)
                lane_words.append((1 << 20) | (edge << 14) | (orbit << 7) | anchor)
                hash_lane_words.append(_project_fpga_hash(transform_hash(
                    residual_hash.orbit_column_bases[orbit], anchor,
                ), fpga_hash_width))
        if seen_edges != set(range(degree)):
            raise ValueError(f"incomplete edge cover at time {time_index}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    fixed_priors = _reindexed_fixed_priors(
        relay_root=args.relay_root, p=p, basis=basis,
    )
    # Keep every scale selected by the hardware profile table.  The old image
    # carried only scales 2..4; profile 7 requested scale 5 and silently fell
    # through to scale 4 in RTL.  That made the advertised rescue profile a
    # different algorithm than the software/oracle profile.
    orbit_priors = {
        scale: _orbit_priors(
            relay_root=args.relay_root, p=p, basis=basis, scale=scale,
        )
        for scale in (1, 2, 3, 4, 5)
    }
    logical_masks = _reindexed_logical_masks(
        relay_root=args.relay_root, p=p, basis=basis,
    )
    logical_pattern_ids, logical_patterns = _compress_logical_masks(logical_masks)
    lane_slots = [lane_words[slot::BANKS] for slot in range(BANKS)]
    hash_slots = [hash_lane_words[slot::BANKS] for slot in range(BANKS)]
    # Gowin does not preserve bit numbering reliably when a wide ROM is inferred
    # as one native RAM. Emit one 23-bit descriptor plus the selected FPGA hash
    # width per physical slot. The 20-bit production projection retains three
    # complete equivariant blocks and avoids the 32-bit LUT overflow.
    template_slot_words = []
    for slot in range(BANKS):
        words = []
        for address in range(TIME_SLICES * MAX_BANKED_BEATS):
            lane = lane_slots[slot][address]
            orbit = (lane >> 7) & 0x7f
            colour = int(colors[orbit]) if (lane >> 20) & 1 else 0
            descriptor = lane | (colour << 21)
            words.append(descriptor | (hash_slots[slot][address] << 23))
        template_slot_words.append(words)
    # One synchronous FPGA read must return the complete four-slot template
    # beat.  Each descriptor also carries its fixed orbit bank colour so the
    # sixteen translated accesses do not replicate an asynchronous colour
    # table. Packing descriptors and residual-hash columns into one row lets
    # Gowin infer compact block RAM instead of a prohibitively
    # large asynchronous distributed ROM.
    template_rows = []
    for address in range(TIME_SLICES * MAX_BANKED_BEATS):
        row = 0
        for slot in range(BANKS):
            lane = lane_slots[slot][address]
            orbit = (lane >> 7) & 0x7f
            colour = int(colors[orbit]) if (lane >> 20) & 1 else 0
            descriptor = lane | (colour << 21)
            row |= descriptor << (slot * 23)
            row |= hash_slots[slot][address] << (
                BANKS * 23 + slot * fpga_hash_width
            )
        template_rows.append(row)
    orbit_config_rows = [
        orbit_priors[2][orbit]
        | (orbit_priors[3][orbit] << 11)
        | (orbit_priors[4][orbit] << 22)
        | (orbit_priors[5][orbit] << 33)
        | (orbit_priors[1][orbit] << 44)
        | (int(colors[orbit]) << 55)
        | (logical_pattern_ids[orbit] << 57)
        for orbit in range(ORBIT_COUNT)
    ]
    logical_quads = []
    for pattern_id in range(len(logical_patterns) // GROUP_ORDER):
        pattern = logical_patterns[pattern_id * GROUP_ORDER:(pattern_id + 1) * GROUP_ORDER]
        for colour in range(BANKS):
            for local in range(18):
                q, y = divmod(local, 6)
                word = 0
                for bank in range(BANKS):
                    residue = (bank - colour) % BANKS
                    coordinate = (q * BANKS + residue) * 6 + y
                    word |= pattern[coordinate] << (bank * 12)
                logical_quads.append(word)
    files = {
        "meta.memb": _write_words(args.output_dir / "meta.memb", meta_words, 10),
        "lanes.memb": _write_words(args.output_dir / "lanes.memb", lane_words, 21),
        "colors.memb": _write_words(args.output_dir / "colors.memb", [int(color) for color in colors], 2),
        "priors.memb": _write_words(args.output_dir / "priors.memb", fixed_priors, POSTERIOR_WIDTH),
        **{
            f"prior_orbits_scale{scale}.memb": _write_words(
                args.output_dir / f"prior_orbits_scale{scale}.memb",
                words, POSTERIOR_WIDTH,
            )
            for scale, words in orbit_priors.items()
        },
        "hash_time_bases.memb": _write_words(
            args.output_dir / "hash_time_bases.memb",
            [_project_fpga_hash(word, fpga_hash_width)
             for word in residual_hash.time_bases], fpga_hash_width,
        ),
        "hash_orbit_columns.memb": _write_words(
            args.output_dir / "hash_orbit_columns.memb",
            [_project_fpga_hash(word, fpga_hash_width)
             for word in residual_hash.orbit_column_bases], fpga_hash_width,
        ),
        "hash_lanes.memb": _write_words(
            args.output_dir / "hash_lanes.memb", hash_lane_words, fpga_hash_width,
        ),
        **{
            f"lane_slot{slot}.memb": _write_words(
                args.output_dir / f"lane_slot{slot}.memb", words, 21,
            )
            for slot, words in enumerate(lane_slots)
        },
        **{
            f"hash_slot{slot}.memb": _write_words(
                args.output_dir / f"hash_slot{slot}.memb", words, fpga_hash_width,
            )
            for slot, words in enumerate(hash_slots)
        },
        "template_rows.memb": _write_words(
            args.output_dir / "template_rows.memb", template_rows,
            BANKS * (23 + fpga_hash_width),
        ),
        **{
            f"template_slot{slot}.memb": _write_words(
                args.output_dir / f"template_slot{slot}.memb", words,
                23 + fpga_hash_width,
            )
            for slot, words in enumerate(template_slot_words)
        },
        "orbit_config.memb": _write_words(
            args.output_dir / "orbit_config.memb", orbit_config_rows,
            ORBIT_CONFIG_WIDTH,
        ),
        "logical_quads.memb": _write_words(
            args.output_dir / "logical_quads.memb", logical_quads, 48,
        ),
        **{
            f"logical_quad_bank{bank}.memb": _write_words(
                args.output_dir / f"logical_quad_bank{bank}.memb",
                [(word >> (bank * 12)) & 0xFFF for word in logical_quads], 12,
            )
            for bank in range(BANKS)
        },
        "logical_masks.memb": _write_words(
            args.output_dir / "logical_masks.memb", logical_masks, 12,
        ),
        "logical_pattern_ids.memb": _write_words(
            args.output_dir / "logical_pattern_ids.memb", logical_pattern_ids, 3,
        ),
        "logical_patterns.memb": _write_words(
            args.output_dir / "logical_patterns.memb", logical_patterns, 12,
        ),
    }
    manifest = {
        "schema": "PAPER_GROSS144_S1W_ROM_V1",
        "source_image": str(args.image),
        "source_image_sha256": image["serialized_sha256"],
        "physical_error_rate": p,
        "basis": basis,
        "time_slices": TIME_SLICES,
        "max_banked_beats": MAX_BANKED_BEATS,
        "banks": BANKS,
        "orbit_count": ORBIT_COUNT,
        "group_order": GROUP_ORDER,
        "posterior_width": POSTERIOR_WIDTH,
        "orbit_config_width": ORBIT_CONFIG_WIDTH,
        "words": {
            "meta": len(meta_words), "lanes": len(lane_words), "colors": len(colors),
            "priors": len(fixed_priors),
            **{f"prior_orbits_scale{scale}": len(words)
               for scale, words in orbit_priors.items()},
            "hash_time_bases": len(residual_hash.time_bases),
            "hash_orbit_columns": len(residual_hash.orbit_column_bases),
            "hash_lanes": len(hash_lane_words),
            **{f"lane_slot{slot}": len(words)
               for slot, words in enumerate(lane_slots)},
            **{f"hash_slot{slot}": len(words)
               for slot, words in enumerate(hash_slots)},
            "template_rows": len(template_rows),
            "orbit_config": len(orbit_config_rows),
            "logical_quads": len(logical_quads),
            "logical_masks": len(logical_masks),
            "logical_pattern_ids": len(logical_pattern_ids),
            "logical_patterns": len(logical_patterns),
        },
        "logical_pattern_count": len(logical_patterns) // GROUP_ORDER,
        "residual_hash": {
            "width": fpga_hash_width,
            "syndrome_rank": gf2_word_rank(
                [_project_fpga_hash(transform_hash(base, coordinate), fpga_hash_width)
                 for base in residual_hash.time_bases
                 for coordinate in range(GROUP_ORDER)],
                width=fpga_hash_width,
            ),
            "acceptance_contract": "zero hash requires exact full syndrome replay",
        },
        "sha256": files,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
