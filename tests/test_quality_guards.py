from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import market_flow_dashboard as dashboard  # noqa: E402
import update_dashboard as updater  # noqa: E402
from ths_five_day_flow import API_ROW_LIMITS, assert_api_batch, fetch_by_date  # noqa: E402


class FakeClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def call(self, api_name: str, params: dict, fields: str = "") -> list[dict]:
        return list(self.rows)


class ApiGuardTests(unittest.TestCase):
    def test_empty_partition_is_blocked(self) -> None:
        with self.assertRaises(RuntimeError):
            fetch_by_date(FakeClient([]), "daily", ["20260105"])

    def test_endpoint_specific_limit_is_blocked(self) -> None:
        rows = [{"trade_date": "20260105"}] * API_ROW_LIMITS["fund_share"]
        with self.assertRaises(RuntimeError):
            assert_api_batch("fund_share", rows, "20260105")

    def test_below_limit_is_allowed(self) -> None:
        assert_api_batch("fund_share", [{"trade_date": "20260105"}] * 1999, "20260105")


class PublishGuardTests(unittest.TestCase):
    def test_validated_snapshot_replaces_formal_snapshot_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "20260105"
            formal.mkdir()
            (formal / "version.txt").write_text("old", encoding="utf-8")
            staging = root / ".pipeline" / "20260105"
            staging.mkdir(parents=True)
            (staging / "version.txt").write_text("new", encoding="utf-8")
            published, backup = updater.publish_validated_snapshot(staging, root)
            self.assertEqual((published / "version.txt").read_text(encoding="utf-8"), "new")
            self.assertIsNotNone(backup)
            self.assertEqual((backup / "version.txt").read_text(encoding="utf-8"), "old")


class DashboardPaletteTests(unittest.TestCase):
    def test_china_market_uses_red_up_green_down(self) -> None:
        source = (ROOT / "scripts/render_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("--up:#b74337", source)
        self.assertIn("--down:#18705a", source)
        self.assertIn(".positive{background:var(--up)}", source)
        self.assertIn(".negative{background:var(--down)}", source)
        self.assertIn("td.pos{color:var(--up)}td.neg{color:var(--down)}", source)


class RankingGuardTests(unittest.TestCase):
    def test_rps_uses_raw_return_and_average_rank_for_true_ties(self) -> None:
        rows = [
            {"ts_code": "A", "raw": 1.00004, "shown": 1.0},
            {"ts_code": "B", "raw": 1.00003, "shown": 1.0},
            {"ts_code": "C", "raw": 0.5, "shown": 0.5},
            {"ts_code": "D", "raw": 0.5, "shown": 0.5},
        ]
        dashboard.assign_rps(rows, "raw", "rank", "rps")
        by_code = {row["ts_code"]: row for row in rows}
        self.assertEqual(by_code["A"]["rank"], 1)
        self.assertEqual(by_code["B"]["rank"], 2)
        self.assertEqual(by_code["C"]["rank"], 3.5)
        self.assertEqual(by_code["D"]["rank"], 3.5)

    def test_incomplete_stock_window_gets_no_rank_and_keeps_actual_latest_date(self) -> None:
        original = dashboard.WINDOWS
        dashboard.WINDOWS = {"3d": 3}
        try:
            dates = ["20260101", "20260102", "20260103"]
            rows = [
                {"ts_code": "A", "trade_date": "20260101", "name": "A", "net_amount": 1},
                {"ts_code": "A", "trade_date": "20260102", "name": "A", "net_amount": 2},
            ]
            rows += [
                {"ts_code": "B", "trade_date": date, "name": "B", "net_amount": 1}
                for date in dates
            ]
            result = dashboard.aggregate_stocks(rows, dates, {}, {})
            by_code = {row["ts_code"]: row for row in result}
            self.assertEqual(by_code["A"]["latest_trade_date"], "20260102")
            self.assertNotIn("3d_inflow_rank", by_code["A"])
            self.assertEqual(by_code["B"]["3d_inflow_rank"], 1)
        finally:
            dashboard.WINDOWS = original

    def test_partial_etf_flow_is_not_official_or_ranked(self) -> None:
        original_windows = dashboard.WINDOWS
        original_rps = dashboard.RPS_WINDOWS
        dashboard.WINDOWS = {"2d": 2}
        dashboard.RPS_WINDOWS = {"2d": 2}
        try:
            dates = ["20260101", "20260102", "20260103"]
            daily = [
                {"ts_code": "510000.SH", "trade_date": date, "close": 1, "pct_chg": 0, "amount": 1}
                for date in dates
            ]
            shares = [
                {"ts_code": "510000.SH", "trade_date": "20260101", "fd_share": 100},
                {"ts_code": "510000.SH", "trade_date": "20260102", "fd_share": 110},
            ]
            result = dashboard.build_etfs(
                daily, shares, {"510000.SH": {"name": "测试ETF", "fund_type": "股票型"}}, dates
            )[0]
            self.assertEqual(result["share_days_covered_2d"], 1)
            self.assertEqual(result["partial_estimated_flow_2d_wan"], 10)
            self.assertIsNone(result["estimated_flow_2d_wan"])
            self.assertNotIn("flow_2d_inflow_rank", result)
        finally:
            dashboard.WINDOWS = original_windows
            dashboard.RPS_WINDOWS = original_rps


if __name__ == "__main__":
    unittest.main()
