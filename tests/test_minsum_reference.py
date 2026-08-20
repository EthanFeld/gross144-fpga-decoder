from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.graph_model import Graph  # noqa: E402
from gross144_decoder.minsum_reference import check_update, layered_min_sum  # noqa: E402


class MinSumReferenceTests(unittest.TestCase):
    def test_hand_calculated_check_updates(self):
        self.assertEqual(check_update((2.0, -3.0), normalization=0.75), (-2.0, 1.0))
        self.assertEqual(check_update((2.0, -3.0), syndrome_bit=1, normalization=0.75),
                         (2.0, -1.0))
        self.assertEqual(check_update((1.0, 2.0, 3.0), normalization=1.0), (2.0, 1.0, 1.0))

    def test_zero_syndrome_stops_without_change(self):
        graph = Graph.from_neighbors(2, ((0, 1),))
        result = layered_min_sum(graph, (8.0, 8.0), max_iterations=5)
        self.assertTrue(result.success)
        self.assertEqual(result.iterations, 0)
        self.assertEqual(result.correction, (0, 0))

    def test_repeat_is_deterministic_and_success_is_syndrome_gated(self):
        graph = Graph.from_neighbors(2, ((0, 1),))
        first = layered_min_sum(graph, (-8.0, 8.0), syndrome=(1,), max_iterations=4)
        second = layered_min_sum(graph, (-8.0, 8.0), syndrome=(1,), max_iterations=4)
        self.assertEqual(first, second)
        self.assertTrue(first.success)
        self.assertEqual(first.syndrome, (1,))
        failed = layered_min_sum(graph, (0.0, 0.0), syndrome=(1,), max_iterations=1)
        self.assertFalse(failed.success)


if __name__ == "__main__":
    unittest.main()
