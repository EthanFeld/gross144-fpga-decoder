from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.candidate_score import make_candidate  # noqa: E402
from gross144_decoder.graph_model import Graph  # noqa: E402
from gross144_decoder.telescope import Stage, StageResult, StageStatus  # noqa: E402
from gross144_decoder.unified_stage_controller import (  # noqa: E402
    GraphType,
    StageBudget,
    UnifiedStageControllerConfig,
    run_unified_stage_controller,
)


class UnifiedStageControllerTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph.from_neighbors(2, ((0, 1),), syndrome=(1,))
        self.candidate_a = make_candidate(self.graph, (1, 0), source_stage="S1")
        self.candidate_b = make_candidate(self.graph, (0, 1), source_stage="S2")

    def test_static_path_terminates_after_s1a(self):
        calls = []

        def defer(stage):
            def adapter(_graph, _config):
                calls.append(stage)
                return StageResult(StageStatus.DEFER, work=2)
            return adapter

        result = run_unified_stage_controller(
            self.graph,
            config=UnifiedStageControllerConfig(graph_type=GraphType.STATIC),
            adapters={Stage.S1: defer(Stage.S1), Stage.S1A: defer(Stage.S1A),
                      Stage.S2: lambda *_: (_ for _ in ()).throw(AssertionError("S2 ran"))},
            zero_syndrome=False,
        )
        self.assertEqual(result.status, StageStatus.DEFER)
        self.assertEqual(calls, [Stage.S1, Stage.S1A])
        self.assertEqual([record.stage for record in result.stages], [Stage.S1, Stage.S1A])

    def test_circuit_order_reaches_s2_and_stops_on_accept(self):
        calls = []

        def defer(stage):
            def adapter(_graph, _config):
                calls.append(stage)
                return StageResult(StageStatus.DEFER, work=1)
            return adapter

        def success(_graph, _config):
            calls.append(Stage.S2)
            return StageResult(StageStatus.SUCCESS, self.candidate_b, work=4)

        result = run_unified_stage_controller(
            self.graph,
            config=UnifiedStageControllerConfig(graph_type=GraphType.CIRCUIT),
            adapters={Stage.S1: defer(Stage.S1), Stage.S1A: defer(Stage.S1A), Stage.S2: success,
                      Stage.S2R: lambda *_: (_ for _ in ()).throw(AssertionError("S2R ran"))},
            zero_syndrome=False,
        )
        self.assertEqual(result.status, StageStatus.SUCCESS)
        self.assertEqual(result.selected_stage, Stage.S2)
        self.assertEqual(calls, [Stage.S1, Stage.S1A, Stage.S2])

    def test_stage_budget_timeout_is_internal(self):
        result = run_unified_stage_controller(
            self.graph,
            config=UnifiedStageControllerConfig(
                stage_budgets=(StageBudget(Stage.S1, 5), StageBudget(Stage.S1A, 5)),
                total_work_budget=20,
            ),
            adapters={Stage.S1: lambda *_: StageResult(StageStatus.DEFER, work=6)},
            zero_syndrome=False,
        )
        self.assertEqual(result.status, StageStatus.FAIL_INTERNAL)
        self.assertEqual(result.timeout_stage, Stage.S1)
        self.assertTrue(result.stages[0].timed_out)

    def test_adapter_exception_and_invalid_success_are_internal(self):
        raised = run_unified_stage_controller(
            self.graph,
            adapters={Stage.S1: lambda *_: (_ for _ in ()).throw(RuntimeError("corrupt image"))},
            zero_syndrome=False,
        )
        self.assertEqual(raised.status, StageStatus.FAIL_INTERNAL)
        self.assertEqual(raised.internal_error_stage, Stage.S1)
        invalid = make_candidate(self.graph, (0, 0), source_stage="S1")
        bad = run_unified_stage_controller(
            self.graph,
            adapters={Stage.S1: lambda *_: StageResult(StageStatus.SUCCESS, invalid)},
            zero_syndrome=False,
        )
        self.assertEqual(bad.status, StageStatus.FAIL_INTERNAL)
        self.assertTrue(bad.stages[0].internal_error)

    def test_no_candidate_path_reports_full_circuit_order(self):
        result = run_unified_stage_controller(
            self.graph,
            config=UnifiedStageControllerConfig(graph_type=GraphType.CIRCUIT),
            adapters={stage: lambda *_: StageResult(StageStatus.DEFER, work=1)
                      for stage in (Stage.S1, Stage.S1A, Stage.S2, Stage.S2R, Stage.HOST)},
            zero_syndrome=False,
        )
        self.assertEqual(result.status, StageStatus.DEFER)
        self.assertEqual([record.stage for record in result.stages],
                         [Stage.S1, Stage.S1A, Stage.S2, Stage.S2R, Stage.HOST])
        self.assertIsNone(result.selected_candidate)

    def test_accepted_result_cannot_be_overwritten(self):
        calls = []

        def first(_graph, _config):
            calls.append("first")
            return StageResult(StageStatus.SUCCESS, self.candidate_a, work=2)

        def later(_graph, _config):
            calls.append("later")
            return StageResult(StageStatus.SUCCESS, self.candidate_b, work=1)

        result = run_unified_stage_controller(
            self.graph,
            config=UnifiedStageControllerConfig(graph_type=GraphType.CIRCUIT),
            adapters={Stage.S1: first, Stage.S1A: later}, zero_syndrome=False,
        )
        self.assertEqual(result.selected_candidate, self.candidate_a)
        self.assertEqual(calls, ["first"])

    def test_zero_syndrome_fast_path(self):
        graph = Graph.from_neighbors(2, ((0, 1),))
        result = run_unified_stage_controller(graph)
        self.assertEqual(result.selected_stage, Stage.S0)
        self.assertEqual(result.stages[0].reason, "zero syndrome")


if __name__ == "__main__":
    unittest.main()
