from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.wide_minsum import GROSS144_FOUR_LANE_PAIR_GROUPS  # noqa: E402
from tools.run_paper_gross144_four_lane_board_campaign import (  # noqa: E402
    _logical_words_batch,
    _pack_groups,
    _pack_groups_batch,
)


class BoardCampaignPackingTests(unittest.TestCase):
    def test_vectorized_pack_matches_wire_reference(self):
        rng = np.random.default_rng(144)
        syndromes = rng.integers(0, 2, size=(17, 936), dtype=np.uint8)
        expected = []
        for syndrome in syndromes:
            payload = bytearray(117)
            ordinal = 0
            for time_index in range(13):
                for group in GROSS144_FOUR_LANE_PAIR_GROUPS:
                    word = sum(
                        int(syndrome[time_index * 72 + coordinate]) << lane
                        for lane, coordinate in enumerate(group)
                    )
                    payload[ordinal >> 1] |= word << (4 * (ordinal & 1))
                    ordinal += 1
            expected.append(bytes(payload))

        batch = _pack_groups_batch(syndromes)
        self.assertEqual([bytes(row) for row in batch], expected)
        self.assertEqual(_pack_groups(syndromes[0]), expected[0])

    def test_vectorized_logical_word_pack(self):
        actual = np.zeros((3, 12), dtype=np.uint8)
        actual[0, [0, 3, 11]] = 1
        actual[1, [4, 8]] = 1
        actual[2, [1, 2, 10]] = 1
        np.testing.assert_array_equal(
            _logical_words_batch(actual),
            np.asarray((1 | 8 | (1 << 11), (1 << 4) | (1 << 8),
                        (1 << 1) | (1 << 2) | (1 << 10)), dtype=np.uint16),
        )


if __name__ == "__main__":
    unittest.main()
