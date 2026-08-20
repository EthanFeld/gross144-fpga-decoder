from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.graph_model import Graph  # noqa: E402
from gross144_decoder.minsum_reference import FixedDecodeResult  # noqa: E402
from gross144_decoder.relay_reference import (  # noqa: E402
    RelayConfig,
    RelayLegConfig,
    RelayStatus,
    run_relay,
    save_checkpoint,
)


class RelayReferenceTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph.from_neighbors(3, ((0, 1), (1, 2)))
        self.checkpoint = save_checkpoint((8, 8, 8), (0, 0))
        self.legs = tuple(RelayLegConfig(index, (0, 0, 0)) for index in range(3))

    def test_repeated_runs_are_deterministic(self):
        config = RelayConfig(self.legs, quorum=2)
        first = run_relay(self.graph, self.checkpoint, config)
        second = run_relay(self.graph, self.checkpoint, config)
        self.assertEqual(first, second)
        self.assertEqual(first.status, RelayStatus.SUCCESS)
        self.assertEqual(first.vote_counts, ((0, 3),))

    def test_failed_leg_cannot_vote(self):
        def decoder(graph, prior, syndrome, max_iterations, leg):
            if leg.leg_id == 0:
                return FixedDecodeResult((0, 0, 0), (8, 8, 8), syndrome,
                                         False, 1, ())
            return FixedDecodeResult((0, 0, 0), (8, 8, 8), syndrome,
                                     True, 0, ())
        result = run_relay(
            self.graph, self.checkpoint,
            RelayConfig(self.legs, quorum=3), decoder=decoder,
        )
        self.assertEqual(result.status, RelayStatus.NO_QUORUM)
        self.assertEqual(result.vote_counts, ((0, 2),))
        self.assertFalse(result.legs[0].success)

    def test_tie_and_no_quorum_are_explicit(self):
        logical_signatures = ((1, 0, 0),)

        def decoder(graph, prior, syndrome, max_iterations, leg):
            correction = (0, 0, 0) if leg.leg_id == 0 else (1, 1, 1)
            return FixedDecodeResult(correction, (8, 8, 8), syndrome,
                                     True, 0, ())

        tie = run_relay(
            self.graph, self.checkpoint,
            RelayConfig(self.legs[:2], quorum=1),
            logical_signatures=logical_signatures, decoder=decoder,
        )
        self.assertEqual(tie.status, RelayStatus.TIE)
        no_quorum = run_relay(
            self.graph, self.checkpoint,
            RelayConfig(self.legs[:2], quorum=2),
            logical_signatures=logical_signatures, decoder=decoder,
        )
        self.assertEqual(no_quorum.status, RelayStatus.NO_QUORUM)

    def test_disabled_leg_is_not_a_vote(self):
        config = RelayConfig((RelayLegConfig(0, enabled=False), RelayLegConfig(1)), quorum=1)
        result = run_relay(self.graph, self.checkpoint, config)
        self.assertEqual(result.status, RelayStatus.SUCCESS)
        self.assertEqual(result.vote_counts, ((0, 1),))
        self.assertEqual(result.legs[0].reason, "disabled")


if __name__ == "__main__":
    unittest.main()
