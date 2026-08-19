from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from mitten.automorphism import (  # noqa: E402
    AutomorphismConfig,
    AutomorphismTrial,
    identity_and_cyclic_trials,
    run_automorphism_ensemble,
    transform_graph,
)
from mitten.graph_model import Graph  # noqa: E402
from mitten.minsum_reference import layered_min_sum  # noqa: E402


class AutomorphismTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph.from_neighbors(
            3, ((0, 1), (1, 2), (0, 2)), syndrome=(1, 0, 1)
        )
        self.trials = identity_and_cyclic_trials(self.graph)

    def test_transform_preserves_graph_and_inverse_candidates(self):
        transformed = transform_graph(self.graph, (1, 2, 0), (1, 2, 0))
        self.assertEqual(transformed.to_matrix(), self.graph.to_matrix())
        result = run_automorphism_ensemble(
            self.graph, (-6.0, 6.0, 6.0),
            trials=self.trials,
        )
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.state_context_count, 1)
        self.assertEqual(result.restore_count, 4)
        self.assertEqual(result.trial_order, (0, 1, 2, 3))
        self.assertTrue(all(
            trial.candidate is not None and trial.candidate.syndrome_satisfied
            for trial in result.trials
        ))

    def test_identity_trial_matches_baseline_and_nonidentity_trace_differs(self):
        prior = (-6.0, 6.0, 6.0)
        baseline = layered_min_sum(
            self.graph, prior, syndrome=self.graph.syndrome, max_iterations=10
        )
        result = run_automorphism_ensemble(self.graph, prior, trials=self.trials)
        identity = result.trials[0]
        self.assertEqual(identity.candidate.correction, baseline.correction)
        self.assertEqual(identity.iterations, baseline.iterations)
        self.assertGreaterEqual(result.distinct_trace_count, 2)

    def test_early_stop_is_explicit_and_deterministic(self):
        result = run_automorphism_ensemble(
            self.graph, (-6.0, 6.0, 6.0), trials=self.trials,
            config=AutomorphismConfig(early_stop_score=0.0),
        )
        self.assertEqual(result.trial_order, (0,))
        self.assertEqual(result.restore_count, 1)
        with self.assertRaisesRegex(ValueError, "exactly four"):
            run_automorphism_ensemble(
                self.graph, (-6.0, 6.0, 6.0), trials=self.trials[:3]
            )

    def test_qualification_subset_requires_explicit_flag(self):
        with self.assertRaisesRegex(ValueError, "exactly four"):
            run_automorphism_ensemble(
                self.graph, (-6.0, 6.0, 6.0), trials=self.trials[:2],
                config=AutomorphismConfig(trials=2),
            )
        result = run_automorphism_ensemble(
            self.graph, (-6.0, 6.0, 6.0), trials=self.trials[:2],
            config=AutomorphismConfig(trials=2, qualification_subset=True),
        )
        self.assertEqual(len(result.trials), 2)

    def test_non_automorphism_rejected(self):
        bad = (
            AutomorphismTrial(0, (0, 1, 2), (0, 1, 2)),
            AutomorphismTrial(1, (0, 2, 1), (0, 1, 2)),
            AutomorphismTrial(2, (1, 2, 0), (1, 2, 0)),
            AutomorphismTrial(3, (2, 0, 1), (2, 0, 1)),
        )
        with self.assertRaisesRegex(ValueError, "not a graph automorphism"):
            run_automorphism_ensemble(
                self.graph, (-6.0, 6.0, 6.0), trials=bad
            )


if __name__ == "__main__":
    unittest.main()
