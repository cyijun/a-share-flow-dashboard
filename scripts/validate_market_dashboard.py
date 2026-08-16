#!/usr/bin/env python3
"""从持久化原始 CSV 独立复算关键指标，不调用生产聚合函数。"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def num(value: Any) -> float:
    return float(value or 0)


def grouped(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    output: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        output[row["ts_code"]].append(row)
    for values in output.values():
        values.sort(key=lambda row: row["trade_date"])
    return output


def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="快照目录，例如 outputs/20260814")
    args = parser.parse_args()
    candidate = Path(args.snapshot)
    output_dir = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    raw = output_dir / "raw"
    results = output_dir / "results"
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    flow_windows = {
        item["key"]: int(item["days"])
        for item in summary.get("flow_windows", [
            {"key": "1d", "days": 1}, {"key": "5d", "days": 5}, {"key": "10d", "days": 10}
        ])
    }
    rps_windows = {
        item["key"]: int(item["days"])
        for item in summary.get("rps_windows", [
            {"key": "10d", "days": 10}, {"key": "20d", "days": 20}
        ])
    }
    flow_dates = summary.get("flow_dates", summary.get("flow_dates_10", []))
    dates = summary.get("trade_dates", summary.get("trade_dates_21", []))
    primary_key = summary.get("primary_flow_key", max(flow_windows, key=flow_windows.get))
    cap_floor = float(summary.get("market_cap_floor_yi", 200))

    industry_raw = read_csv(raw / "industry_flow_ths.csv")
    stock_raw = read_csv(raw / "stock_flow_ths.csv")
    daily_raw = read_csv(raw / "daily.csv")
    adj_raw = read_csv(raw / "adj_factor.csv")
    daily_basic = read_csv(raw / "daily_basic.csv")
    fund_daily = read_csv(raw / "fund_daily.csv")
    fund_share = read_csv(raw / "fund_share.csv")
    industry_result = read_csv(results / "industry_window_summary.csv")
    stock_result = read_csv(results / "stock_window_summary.csv")
    largecap_result = read_csv(results / "stock_largecap_flow_ratio.csv")
    rps_result = read_csv(results / "stock_rps_all.csv")
    etf_result = read_csv(results / "etf_summary.csv")
    checks: list[dict[str, Any]] = []

    expected_dates = max(max(flow_windows.values()) + 1, max(rps_windows.values()) + 1)
    checks.append(check(
        "交易日窗口", len(flow_dates) == max(flow_windows.values()) and len(dates) == expected_dates,
        f"flow={len(flow_dates)}/{max(flow_windows.values())}, endpoints={len(dates)}/{expected_dates}",
    ))

    industry_group = grouped(industry_raw)
    industry_by_code = {row["ts_code"]: row for row in industry_result}
    industry_errors = 0
    industry_top_errors = 0
    for key, days in flow_windows.items():
        recalculated: list[tuple[str, float]] = []
        wanted = set(flow_dates[-days:])
        for code, rows in industry_group.items():
            value = sum(num(row["net_amount"]) for row in rows if row["trade_date"] in wanted)
            recalculated.append((code, value))
            if abs(value - num(industry_by_code[code][f"net_{key}_yi"])) > 1e-6:
                industry_errors += 1
        expected_in = max(recalculated, key=lambda item: (item[1], item[0]))[0]
        expected_out = min(recalculated, key=lambda item: (item[1], item[0]))[0]
        actual_in = min(industry_result, key=lambda row: int(float(row[f"{key}_inflow_rank"])))["ts_code"]
        actual_out = min(industry_result, key=lambda row: int(float(row[f"{key}_outflow_rank"])))["ts_code"]
        industry_top_errors += int(expected_in != actual_in) + int(expected_out != actual_out)
    checks.append(check(
        f"行业{','.join(map(str, flow_windows.values()))}日复算",
        industry_errors == 0 and industry_top_errors == 0,
        f"数值错误={industry_errors}, 榜首错误={industry_top_errors}",
    ))

    stock_group = grouped(stock_raw)
    stock_by_code = {row["ts_code"]: row for row in stock_result}
    stock_errors = 0
    stock_top_errors = 0
    for key, days in flow_windows.items():
        recalculated: list[tuple[str, float]] = []
        wanted = set(flow_dates[-days:])
        for code, rows in stock_group.items():
            value = sum(num(row["net_amount"]) for row in rows if row["trade_date"] in wanted)
            recalculated.append((code, value))
            if abs(value - num(stock_by_code[code][f"net_{key}_wan"])) > 0.011:
                stock_errors += 1
        expected_in = max(recalculated, key=lambda item: (item[1], item[0]))[0]
        expected_out = min(recalculated, key=lambda item: (item[1], item[0]))[0]
        actual_in = min(stock_result, key=lambda row: int(float(row[f"{key}_inflow_rank"])))["ts_code"]
        actual_out = min(stock_result, key=lambda row: int(float(row[f"{key}_outflow_rank"])))["ts_code"]
        stock_top_errors += int(expected_in != actual_in) + int(expected_out != actual_out)
    checks.append(check(
        f"个股{','.join(map(str, flow_windows.values()))}日复算",
        stock_errors == 0 and stock_top_errors == 0,
        f"数值错误={stock_errors}, 榜首错误={stock_top_errors}",
    ))

    mv_by_code = {row["ts_code"]: num(row["total_mv"]) for row in daily_basic}
    ratio_errors = 0
    threshold_errors = 0
    largecap_codes = {row["ts_code"] for row in largecap_result}
    for row in stock_result:
        mv = mv_by_code.get(row["ts_code"], 0.0)
        should_include = mv >= cap_floor * 10000
        threshold_errors += int((row["ts_code"] in largecap_codes) != should_include)
        if mv:
            for key in flow_windows:
                expected = 100 * num(row[f"net_{key}_wan"]) / mv
                if abs(expected - num(row[f"flow_mv_ratio_{key}_pct"])) > 0.000002:
                    ratio_errors += 1
    checks.append(check(
        "市值门槛与资金比例", ratio_errors == 0 and threshold_errors == 0,
        f"比例错误={ratio_errors}, 门槛错误={threshold_errors}, 门槛={cap_floor:g}亿元",
    ))

    daily_lookup = {(row["trade_date"], row["ts_code"]): row for row in daily_raw}
    adj_lookup = {(row["trade_date"], row["ts_code"]): row for row in adj_raw}
    rps_by_code = {row["ts_code"]: row for row in rps_result}
    return_errors = 0
    for key, days in rps_windows.items():
        base = dates[-(days + 1)]
        for code, row in rps_by_code.items():
            legs = (
                daily_lookup.get((dates[-1], code)), daily_lookup.get((base, code)),
                adj_lookup.get((dates[-1], code)), adj_lookup.get((base, code)),
            )
            if not all(legs):
                continue
            current = num(legs[0]["close"]) * num(legs[2]["adj_factor"])
            start = num(legs[1]["close"]) * num(legs[3]["adj_factor"])
            expected = 100 * (current / start - 1)
            if abs(expected - num(row[f"return_{key}_pct"])) > 0.00011:
                return_errors += 1
    rank_errors = 0
    for key in rps_windows:
        eligible = [row for row in rps_result if row.get(f"return_{key}_pct") not in (None, "")]
        eligible.sort(key=lambda row: (-num(row[f"return_{key}_pct"]), row["ts_code"]))
        count = len(eligible)
        for rank, row in enumerate(eligible, 1):
            expected = 100 * (count - rank) / (count - 1) if count > 1 else 100
            if int(float(row[f"rank_{key}"])) != rank or abs(expected - num(row[f"rps_{key}"])) > 0.00011:
                rank_errors += 1
    checks.append(check(
        "RPS收益与百分位独立复算", return_errors == 0 and rank_errors == 0,
        f"收益错误={return_errors}, 排名错误={rank_errors}",
    ))

    fund_group = grouped(fund_daily)
    share_lookup = {(row["trade_date"], row["ts_code"]): num(row["fd_share"]) for row in fund_share}
    etf_by_code = {row["ts_code"]: row for row in etf_result}
    date_index = {date: index for index, date in enumerate(dates)}
    etf_return_errors = 0
    etf_flow_errors = 0
    for code, result in etf_by_code.items():
        by_date = {row["trade_date"]: row for row in fund_group.get(code, [])}
        for key, days in rps_windows.items():
            selected = [by_date.get(date) for date in dates[-days:]]
            if all(selected):
                product = 1.0
                for row in selected:
                    product *= 1 + num(row["pct_chg"]) / 100
                if abs(100 * (product - 1) - num(result[f"return_{key}_pct"])) > 0.00011:
                    etf_return_errors += 1
        for key, days in flow_windows.items():
            expected_flow = 0.0
            covered = 0
            for date in dates[-days:]:
                index = date_index[date]
                if index == 0:
                    continue
                previous = dates[index - 1]
                current_share = share_lookup.get((date, code))
                previous_share = share_lookup.get((previous, code))
                day = by_date.get(date)
                if current_share is None or previous_share is None or not day:
                    continue
                expected_flow += (current_share - previous_share) * num(day["close"])
                covered += 1
            actual_covered = int(float(result[f"share_days_covered_{key}"]))
            actual_flow = result.get(f"estimated_flow_{key}_wan", "")
            if covered != actual_covered or (covered and abs(expected_flow - num(actual_flow)) > 0.011):
                etf_flow_errors += 1
    checks.append(check(
        "ETF收益与申赎估算复算", etf_return_errors == 0 and etf_flow_errors == 0,
        f"收益错误={etf_return_errors}, 资金错误={etf_flow_errors}",
    ))

    report_path = output_dir / "report.html"
    checks.append(check("网页文件完整", report_path.exists() and report_path.stat().st_size > 10_000, str(report_path)))
    primary_top = min(stock_result, key=lambda row: int(float(row[f"{primary_key}_inflow_rank"])))
    checks.append(check(
        "摘要榜首一致", summary.get("top_stock_primary_inflow", primary_top).get("ts_code") == primary_top["ts_code"],
        f"{primary_key}={primary_top['ts_code']}",
    ))

    failed = [row for row in checks if row["status"] == "FAIL"]
    payload = {
        "status": "PASS" if not failed else "FAIL",
        "validated_output": str(output_dir),
        "latest_trade_date": summary["latest_trade_date"],
        "flow_windows": flow_windows,
        "rps_windows": rps_windows,
        "checks": checks,
        "failure_count": len(failed),
        "method": "从raw CSV独立聚合，不调用生产聚合函数",
    }
    destination = results / "independent_validation.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
