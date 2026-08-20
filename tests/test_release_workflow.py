from dataclasses import replace
from itertools import permutations
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.gari import GariGraph  # noqa: E402
from gross144_decoder.graph_model import Graph, GroupActionMetadata  # noqa: E402
from gross144_decoder.automorphism import select_decorrelated_group_trials  # noqa: E402
from gross144_decoder.hybrid_warmup import WarmupConfig  # noqa: E402
from gross144_decoder.relay_reference import RelayConfig, RelayLegConfig  # noqa: E402
from gross144_decoder.release_workflow import (  # noqa: E402
    ReleaseWorkflowConfig,
    run_release_workflow,
)
from gross144_decoder.stage2_integration import Stage2Cache  # noqa: E402
from gross144_decoder.telescope import Stage, StageStatus  # noqa: E402
from gross144_decoder.unified_stage_controller import GraphType  # noqa: E402


def triangle_group(graph: Graph) -> GroupActionMetadata:
    check_by_support = {frozenset(check.neighbors): check.id for check in graph.checks}
    variable_actions = tuple(tuple(action) for action in permutations(range(3)))
    check_actions = tuple(
        tuple(check_by_support[frozenset(action[variable] for variable in check.neighbors)]
              for check in graph.checks)
        for action in variable_actions
    )
    return GroupActionMetadata(variable_actions, check_actions)


class ReleaseWorkflowTests(unittest.TestCase):
    def test_static_path_runs_hybrid_then_group_selected_s1a(self):
        base = Graph.from_neighbors(3, ((0, 1), (1, 2), (0, 2)), syndrome=(1, 0, 0))
        graph = replace(base, group=triangle_group(base))
        result = run_release_workflow(graph, (-1.0, 1.0, 1.0))

        self.assertEqual(result.controller.status, StageStatus.DEFER)
        self.assertEqual([record.stage for record in result.controller.stages],
                         [Stage.S1, Stage.S1A])
        self.assertIsNotNone(result.hybrid)
        self.assertIsNotNone(result.automorphism_selection)
        self.assertIsNotNone(result.automorphism)
        self.assertEqual(len(result.automorphism_selection.trials), 4)
        self.assertEqual(
            len({(trial.variable_permutation, trial.check_permutation)
                 for trial in result.automorphism_selection.trials}),
            4,
        )
        self.assertGreater(result.automorphism_selection.minimum_pairwise_decorrelation, 0.0)

    def test_circuit_path_reuses_stage2_cache_and_reaches_relay_and_host(self):
        graph = Graph.from_neighbors(2, ((0, 1),), syndrome=(1,))
        gari = GariGraph.from_decoder_graph(
            graph, inverse_variable_map=(1, 0), check_types=("D_X",), original_graph=graph,
        )
        config = ReleaseWorkflowConfig(
            graph_type=GraphType.CIRCUIT,
            warmup=WarmupConfig(minsum_iterations=0),
            relay=RelayConfig(tuple(
                RelayLegConfig(index, max_iterations=0) for index in range(4)
            )),
        )
        cache = Stage2Cache()
        first = run_release_workflow(graph, (1.0, 1.0), config=config,
                                     gari=gari, stage2_cache=cache)
        second = run_release_workflow(graph, (1.0, 1.0), config=config,
                                      gari=gari, stage2_cache=cache)

        expected = [Stage.S1, Stage.S1A, Stage.S2, Stage.S2R, Stage.HOST]
        self.assertEqual([record.stage for record in first.controller.stages], expected)
        self.assertEqual(first.controller.status, StageStatus.DEFER)
        self.assertIsNotNone(first.stage2)
        self.assertIsNotNone(first.relay)
        self.assertFalse(first.stage2.cache_hit)
        self.assertTrue(second.stage2.cache_hit)
        self.assertEqual((cache.cold_loads, cache.warm_hits), (1, 1))

    def test_static_recovery_path_uses_gari_and_relay_without_host(self):
        graph = Graph.from_neighbors(2, ((0, 1),), syndrome=(1,))
        gari = GariGraph.from_decoder_graph(
            graph, check_types=("D_X",), original_graph=graph,
        )
        config = ReleaseWorkflowConfig(
            graph_type=GraphType.STATIC_RECOVERY,
            warmup=WarmupConfig(minsum_iterations=0),
            relay=RelayConfig(tuple(
                RelayLegConfig(index, max_iterations=0) for index in range(4)
            )),
        )
        result = run_release_workflow(graph, (1.0, 1.0), config=config, gari=gari)

        self.assertEqual(
            [record.stage for record in result.controller.stages],
            [Stage.S1, Stage.S1A, Stage.S2, Stage.S2R],
        )
        self.assertEqual(result.controller.status, StageStatus.DEFER)
        self.assertIsNotNone(result.stage2)
        self.assertIsNotNone(result.relay)

    def test_success_short_circuits_the_slower_stages(self):
        graph = Graph.from_neighbors(2, ((0, 1),), syndrome=(1,))
        result = run_release_workflow(graph, (-1.0, 1.0))

        self.assertEqual(result.controller.status, StageStatus.SUCCESS)
        self.assertEqual(result.controller.selected_stage, Stage.S1)
        self.assertEqual([record.stage for record in result.controller.stages], [Stage.S1])
        self.assertIsNotNone(result.hybrid)
        self.assertIsNone(result.automorphism)
        self.assertIsNone(result.stage2)
        self.assertIsNone(result.relay)

    def test_precompiled_automorphism_selection_is_accepted(self):
        base = Graph.from_neighbors(3, ((0, 1), (1, 2), (0, 2)), syndrome=(1, 0, 0))
        graph = replace(base, group=triangle_group(base))
        selection = select_decorrelated_group_trials(graph)
        result = run_release_workflow(
            graph, (-1.0, 1.0, 1.0), automorphism_selection=selection,
        )
        self.assertIsNotNone(result.automorphism_selection)
        self.assertEqual(result.automorphism_selection, selection)


if __name__ == "__main__":
    unittest.main()
