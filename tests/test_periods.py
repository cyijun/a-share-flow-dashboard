from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import market_flow_dashboard as dashboard  # noqa: E402


class PeriodConfigurationTests(unittest.TestCase):
    def test_parse_sorts_and_deduplicates(self) -> None:
        self.assertEqual(dashboard.parse_day_list("10,1,5,5", "--flow-days"), [1, 5, 10])

    def test_parse_rejects_non_positive_days(self) -> None:
        with self.assertRaises(Exception):
            dashboard.parse_day_list("0,5", "--flow-days")

    def test_flow_labels_keep_familiar_defaults(self) -> None:
        self.assertEqual(dashboard.flow_label(1), "当天")
        self.assertEqual(dashboard.flow_label(5), "一周")
        self.assertEqual(dashboard.flow_label(10), "两周")
        self.assertEqual(dashboard.flow_label(9), "9日")


if __name__ == "__main__":
    unittest.main()
