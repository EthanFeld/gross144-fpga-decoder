from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.paper_gross144_hash import (  # noqa: E402
    HASH_WIDTH,
    add_coordinates,
    compile_residual_hash_image,
    transform_hash,
)


def test_hash_transform_is_a_z12_x_z6_group_action() -> None:
    sample = 0xD4A3_719B
    for left in range(72):
        for right in range(72):
            assert transform_hash(transform_hash(sample, left), right) == \
                transform_hash(sample, add_coordinates(left, right))


def test_frozen_p002_hash_images_have_full_rank_and_exact_columns() -> None:
    for basis in ("X", "Z"):
        path = ROOT / "artifacts" / "paper_gross144_s1_templates_p002" / \
            f"paper_gross144_s1_{basis}.json"
        image = compile_residual_hash_image(json.loads(path.read_text(encoding="utf-8")))
        assert image.syndrome_rank == HASH_WIDTH
        assert len(image.time_bases) == 13
        assert len(image.orbit_column_bases) == 122
