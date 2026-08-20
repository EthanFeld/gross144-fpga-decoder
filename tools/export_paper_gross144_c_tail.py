"""Export exact quotient S2R image for tools/c_tail_worker.c."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.paper_gross144_stage2 import (  # noqa: E402
    _compact_edge_faults,
    compile_paper_gross144_stage2_templates,
)


def _pack_array(fmt: str, values: object) -> bytes:
    values = tuple(values)  # type: ignore[arg-type]
    return struct.pack("<" + fmt * len(values), *values)


def export(relay_root: Path, output: Path, *, basis: str, p: float) -> None:
    image = compile_paper_gross144_stage2_templates(relay_root, p=p, basis=basis)
    offsets, edge_faults = _compact_edge_faults(image)
    dictionary = tuple(int(x) for x in image.logical_mask_dictionary)
    labels = tuple(
        int(x)
        for template in image.logical_label_templates
        for x in template
    )
    if len(labels) != len(image.logical_label_templates) * image.group_order:
        raise AssertionError("C tail logical label image shape drift")

    header = struct.pack(
        "<8sII10I", b"MTNCV1\0", 1, 0,
        image.variables, image.checks, image.edges, image.group_order,
        image.time_slices, image.variable_orbits, len(dictionary),
        len(image.logical_label_templates), len(image.neutral_completion_order),
        len(image.neutral_bridge_faults),
    )
    payload = bytearray(header)
    payload += _pack_array("h", image.orbit_prior_llr)
    payload += bytes(int(x) for x in image.orbit_start_slice)
    payload += bytes(int(x) for x in image.orbit_end_slice)
    payload += _pack_array("I", offsets)
    payload += _pack_array("I", edge_faults)

    # C loader reads all template degrees first, then all variable-length
    # (orbit, anchor) pairs.
    degrees = tuple(len(row) for row in image.detector_time_templates)
    payload += _pack_array("I", degrees)
    for row in image.detector_time_templates:
        for orbit, anchor in row:
            payload += struct.pack("<HB", int(orbit), int(anchor))

    payload += _pack_array("H", dictionary)
    payload += bytes(labels)
    payload += _pack_array("H", image.orbit_logical_label_template_ids)
    payload += _pack_array("i", image.neutral_completion_parents)
    payload += _pack_array("i", image.neutral_completion_faults)
    payload += _pack_array("I", image.neutral_completion_order)
    payload += _pack_array("i", image.neutral_component_by_check)
    payload += _pack_array("I", image.neutral_bridge_faults)
    mask = (1 << 64) - 1
    for values in (image.neutral_bridge_vectors, image.neutral_bridge_combinations):
        payload += _pack_array("Q", (int(x) & mask for x in values))
        payload += _pack_array("Q", (int(x) >> 64 for x in values))

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    print(f"{basis} image={output} bytes={len(payload)} sha={image.serialized_sha256}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--relay-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--basis", choices=("X", "Z"), required=True)
    parser.add_argument("--p", type=float, default=0.002)
    args = parser.parse_args()
    export(args.relay_root, args.output, basis=args.basis, p=args.p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
