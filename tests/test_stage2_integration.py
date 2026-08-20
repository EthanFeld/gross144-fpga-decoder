from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.candidate_score import make_candidate  # noqa: E402
from gross144_decoder.gari import GariGraph  # noqa: E402
from gross144_decoder.graph_model import Graph  # noqa: E402
from gross144_decoder.stage2_integration import (  # noqa: E402
    Stage2Cache,
    Stage2IntegrationConfig,
    Stage2Profile,
    run_stage2_integration,
    run_telescope_with_stage2,
)
from gross144_decoder.telescope import Stage, StageResult, StageStatus  # noqa: E402


class Stage2IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.original = Graph.from_neighbors(2, ((0, 1),), syndrome=(1,))
        self.gari = GariGraph.from_decoder_graph(
            self.original,
            inverse_variable_map=(1, 0),
            check_types=("D_X",),
            original_graph=self.original,
        )
        self.prior = (-8, 8)
        self.config = Stage2IntegrationConfig(
            profile=Stage2Profile(cold_load_cycles=50, warm_load_cycles=5),
        )

    def test_defer_runs_gari_inverse_maps_and_b07_validates(self):
        result = run_stage2_integration(
            self.gari, self.prior, stage1_status=StageStatus.DEFER,
            config=self.config, cache=Stage2Cache(),
        )
        self.assertEqual(result.status, StageStatus.SUCCESS)
        self.assertTrue(result.stage2_ran)
        self.assertEqual(result.candidate.correction, (0, 1))
        self.assertTrue(result.candidate.syndrome_satisfied)
        self.assertEqual(result.load_cycles, 50)
        self.assertEqual(result.total_cycles, 51)

    def test_stage1_success_short_circuits_without_cache_load(self):
        cache = Stage2Cache()
        candidate = make_candidate(self.original, (0, 1), source_stage="S1")
        result = run_stage2_integration(
            self.gari, self.prior, stage1_status=StageStatus.SUCCESS,
            stage1_candidate=candidate, config=self.config, cache=cache,
        )
        self.assertEqual(result.status, StageStatus.SUCCESS)
        self.assertFalse(result.stage2_ran)
        self.assertEqual(cache.cold_loads, 0)
        self.assertEqual(cache.warm_hits, 0)

    def test_cache_reports_cold_then_warm_latency(self):
        cache = Stage2Cache()
        cold = run_stage2_integration(
            self.gari, self.prior, stage1_status=StageStatus.DEFER,
            config=self.config, cache=cache,
        )
        warm = run_stage2_integration(
            self.gari, self.prior, stage1_status=StageStatus.DEFER,
            config=self.config, cache=cache,
        )
        self.assertFalse(cold.cache_hit)
        self.assertTrue(warm.cache_hit)
        self.assertEqual((cold.load_cycles, warm.load_cycles), (50, 5))
        self.assertEqual((cache.cold_loads, cache.warm_hits), (1, 1))

    def test_b08_trace_reaches_s2_only_after_defer(self):
        def defer(_graph, _config):
            return StageResult(StageStatus.DEFER, work=3, reason="stage1 deferred")

        trace = run_telescope_with_stage2(
            self.original, self.gari, self.prior,
            stage1_adapter=defer, config=self.config, cache=Stage2Cache(),
        )
        self.assertEqual(trace.status, StageStatus.SUCCESS)
        self.assertEqual(trace.selected_stage, Stage.S2)
        self.assertEqual([stage for stage, _ in trace.stages], [Stage.S1, Stage.S1A, Stage.S2])

        calls = []

        def stage1_success(_graph, _config):
            calls.append("s1")
            candidate = make_candidate(self.original, (0, 1), source_stage="S1")
            return StageResult(StageStatus.SUCCESS, candidate, work=2)

        def should_not_run(_graph, _config):
            calls.append("s2")
            return StageResult(StageStatus.FAIL_INTERNAL, reason="unexpected")

        # The B08 controller itself short-circuits S2 after Stage-1 success;
        # this is the integration-level defer policy guard.
        from gross144_decoder.telescope import run_telescope
        short = run_telescope(
            self.original,
            adapters={Stage.S1: stage1_success, Stage.S2: should_not_run},
            zero_syndrome=False,
        )
        self.assertEqual(short.selected_stage, Stage.S1)
        self.assertEqual(calls, ["s1"])

    def test_invalid_profile_is_internal_and_no_convergence_defers(self):
        invalid = run_stage2_integration(
            self.gari, self.prior, stage1_status=StageStatus.DEFER,
            config=Stage2IntegrationConfig(
                profile=Stage2Profile(required_sections=("U",)),
            ),
        )
        self.assertEqual(invalid.status, StageStatus.FAIL_INTERNAL)
        graph = Graph.from_neighbors(
            4, ((0, 1), (1, 2), (2,), (3,)), syndrome=(1, 1, 0, 0),
        )
        no_conv = GariGraph.from_decoder_graph(graph)
        result = run_stage2_integration(
            no_conv, (-8, 8, 8, 8), stage1_status=StageStatus.DEFER,
            config=Stage2IntegrationConfig(max_iterations=2),
        )
        self.assertEqual(result.status, StageStatus.DEFER)
        self.assertTrue(result.stage2_ran)
        self.assertIsNone(result.candidate)


if __name__ == "__main__":
    unittest.main()
