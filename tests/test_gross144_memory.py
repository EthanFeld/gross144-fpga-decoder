"""Regression coverage for the Gross144 scheduled circuit FPGA image."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from mitten.gross144 import build_gross144  # noqa: E402
from mitten.gross144_memory import (  # noqa: E402
    CHECKS_PER_TYPE,
    DATA_QUBITS,
    MEMORY_ROUNDS,
    Gross144CircuitFpgaAdapter,
    build_gross144_memory_circuit,
    syndrome_layers,
)
from mitten.gross144_component_templates import compile_component_templates  # noqa: E402
from mitten.hybrid_warmup import quantize_llr  # noqa: E402
from mitten.minsum_reference import fixed_point_layered_min_sum  # noqa: E402


class Gross144MemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.code = build_gross144()

    def test_group_element_schedule_preserves_declared_translations(self):
        for matrix, error_type in ((self.code.hx, "Z"), (self.code.hz, "X")):
            graph, _ = self.code.graph(error_type)
            variables = graph.group.variable_permutations[1]
            checks = graph.group.check_permutations[1]
            for layer in syndrome_layers(matrix):
                self.assertTrue(all(
                    variables[int(layer[check])] == int(layer[checks[check]])
                    for check in range(CHECKS_PER_TYPE)
                ))

    def test_circuits_have_frozen_detector_and_logical_widths(self):
        for basis in ("X", "Z"):
            circuit = build_gross144_memory_circuit(basis=basis)
            self.assertEqual(circuit.num_qubits, 2 * DATA_QUBITS)
            self.assertEqual(circuit.num_detectors,
                             (2 * MEMORY_ROUNDS + 1) * CHECKS_PER_TYPE)
            self.assertEqual(circuit.num_observables, self.code.k)

    def test_component_image_is_group_valid_and_zero_short_circuits(self):
        adapter = Gross144CircuitFpgaAdapter(self.code)
        self.assertEqual(adapter.config.llr_scale, 2)
        for basis, layout in adapter._layouts.items():
            self.assertGreater(layout.fault_count, DATA_QUBITS)
            self.assertLess(layout.fault_count, 24_336)
            self.assertTrue(all(
                layout.graph.preserves_graph(variable, check)
                for variable, check in zip(layout.graph.group.variable_permutations,
                                           layout.graph.group.check_permutations)
            ))
            result = adapter.decode(np.zeros(adapter.detector_count, dtype=np.uint8), basis=basis)
            self.assertTrue(result.accepted)
            self.assertEqual(result.final_stage, "S0")

    def test_component_templates_reconstruct_every_translated_row(self):
        adapter = Gross144CircuitFpgaAdapter(self.code)
        images = {basis: compile_component_templates(adapter, basis=basis)
                  for basis in ("X", "Z")}
        self.assertEqual(images["X"].group_order, 72)
        self.assertEqual(images["Z"].group_order, 72)
        self.assertEqual(images["X"].variable_orbits, 120)
        self.assertEqual(images["Z"].variable_orbits, 122)
        self.assertEqual(images["X"].max_template_degree, 41)
        self.assertEqual(images["Z"].max_template_degree, 41)
        # Degree 41 requires at least ceil(41/4)=11 beats on a four-bank
        # posterior RAM.  The frozen compiler reaches that lower bound for
        # every translated component template, so no bandwidth is left on the
        # table before widening the datapath again.
        for image in images.values():
            self.assertEqual(image.posterior_bank_count, 4)
            self.assertEqual(image.max_banked_cycles, 11)
            self.assertEqual(image.two_lane_pair_coordinate_delta, 36)
            self.assertEqual(len(image.orbit_bank_colors), image.variable_orbits)
            for row, banked_row in zip(image.detector_time_templates,
                                       image.banked_detector_time_templates):
                self.assertEqual(sorted((orbit, anchor) for cycle in banked_row
                                        for _bank, _edge, orbit, anchor in cycle), sorted(row))
                self.assertEqual(sorted(edge for cycle in banked_row
                                        for _bank, edge, _orbit, _anchor in cycle), list(range(len(row))))
                self.assertTrue(all(len({bank for bank, _edge, _orbit, _anchor in cycle}) == len(cycle)
                                    for cycle in banked_row))
            layout = adapter._layouts[image.basis]
            for time_index in range(13):
                for coordinate in range(36):
                    self.assertFalse(set(layout.graph.checks[time_index * 72 + coordinate].neighbors) &
                                     set(layout.graph.checks[time_index * 72 + coordinate + 36].neighbors))

    def test_scale_two_avoids_component_graph_saturation_regression(self):
        """The fixed datapath must solve the deterministic first X-memory shot.

        Scale four leaves this component syndrome unsatisfied because 5-bit
        check messages saturate; scale two uses the same arithmetic and graph
        but reaches the syndrome-valid correction.
        """

        adapter = Gross144CircuitFpgaAdapter(self.code)
        circuit = build_gross144_memory_circuit(basis="X", p=0.002, code=self.code)
        # Fixed seed captured from the per-shot runner's stable X domain.
        detectors, observables = circuit.compile_detector_sampler(seed=3212570424461913893).sample(
            1, separate_observables=True,
        )
        target = adapter._adapt(detectors[0], "X")
        layout = adapter._layouts["X"]
        graph = replace(layout.graph, syndrome=target)
        result = fixed_point_layered_min_sum(
            graph,
            tuple(quantize_llr(value, scale=adapter.config.llr_scale)
                  for value in layout.prior),
            syndrome=target,
            max_iterations=6,
        )
        self.assertTrue(result.success)
        predicted = tuple(
            sum(int(bit) * logical[fault] for fault, bit in enumerate(result.correction)) & 1
            for logical in layout.logical_signatures
        )
        self.assertEqual(predicted, tuple(int(value) for value in observables[0]))


if __name__ == "__main__":
    unittest.main()
