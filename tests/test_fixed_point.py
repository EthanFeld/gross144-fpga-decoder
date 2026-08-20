from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.fixed_point import (  # noqa: E402
    MESSAGE_MAG_MAX,
    POSTERIOR_MAX,
    POSTERIOR_MIN,
    absolute_signed,
    normalize_magnitude,
    saturating_add,
    saturating_sub,
    sign_magnitude_decode,
    sign_magnitude_encode,
    two_minima,
)


class FixedPointTests(unittest.TestCase):
    def test_all_5_bit_sign_magnitude_values(self):
        for raw in range(32):
            value = sign_magnitude_decode(raw)
            self.assertEqual(sign_magnitude_decode(sign_magnitude_encode(value)), value)
        self.assertEqual(sign_magnitude_decode(0b10000), 0)
        self.assertEqual(sign_magnitude_encode(-100), 0b11111)

    def test_posterior_boundaries(self):
        self.assertEqual(saturating_add(POSTERIOR_MAX, 1), POSTERIOR_MAX)
        self.assertEqual(saturating_add(POSTERIOR_MIN, -1), POSTERIOR_MIN)
        self.assertEqual(saturating_sub(POSTERIOR_MAX, -1), POSTERIOR_MAX)
        self.assertEqual(saturating_sub(POSTERIOR_MIN, 1), POSTERIOR_MIN)
        for value in range(POSTERIOR_MIN, POSTERIOR_MAX + 1):
            self.assertEqual(absolute_signed(value, 11), abs(value))

    def test_normalization_and_ties(self):
        self.assertEqual(normalize_magnitude(MESSAGE_MAG_MAX), 12)
        self.assertEqual(two_minima((3, 1, 1, 4)), (1, 1, 1))
        self.assertEqual(two_minima((0, 0, 0)), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
