from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FoundationTests(unittest.TestCase):
    def test_required_layout_exists(self):
        required = (
            "python/gross144_decoder",
            "rtl/common",
            "rtl/decoder",
            "rtl/memory",
            "rtl/protocol",
            "rtl/top",
            "sim/vectors",
            "constraints",
            "config",
            "tools",
            "docs",
            "reports",
        )
        for relative in required:
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_dir())

    def test_bringup_contract(self):
        top = (ROOT / "rtl/top/tang_nano_20k_paper_s1w_four_lane_uart_top.sv").read_text(encoding="utf-8")
        self.assertIn("module tang_nano_20k_paper_s1w_four_lane_uart_top", top)
        self.assertIn("module tang_nano_20k_paper_s1w_four_lane_uart_fast_51_top", top)
        self.assertIn("clk27", top)
        self.assertIn("uart_rx_pin", top)
        self.assertIn("uart_tx_pin", top)


if __name__ == "__main__":
    unittest.main()
