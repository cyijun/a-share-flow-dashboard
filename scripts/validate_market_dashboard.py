#!/usr/bin/env python3
"""从原始 CSV 独立推导日期、样本与指标，验证生产快照。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
API_LIMITS = {
    "moneyflow_ind_ths": 5000,
    "moneyflow_ths": 6000,
    "moneyflow": 6000,
    "daily": 6000,
    "adj_factor": 6000,
    "fund_daily": 5000,
    "fund_share": 2000,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def num(value: Any) -> float:
    return float(value or 0)


def present(value: Any) -> bool:
    return value not in (None, "")


def grouped(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    output: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        output[row["ts_code"]].append(row)
    for values in output.values():
        values.sort(key=lambda row: row["trade_date"])
    return output


def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def partition_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["trade_date"]] += 1
    return dict(counts)


def average_ranks(values: dict[str, float]) -> dict[str, tuple[float, float]]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    count = len(ordered)
    result: dict[str, tuple[float, float]] = {}
    index = 0
    while index < count:
        value = ordered[index][1]
        end = index + 1
        while end < count and ordered[end][1] == value:
            end += 1
        rank = ((index + 1) + end) / 2.0
        rps = 100.0 * (count - rank) / (count - 1) if count > 1 else 100.0
        for code, _ in ordered[index:end]:
            result[code] = (rank, rps)
        index = end
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="快照目录，例如 outputs/20260814")
    args = parser.parse_args()
    candidate = Path(args.snapshot)
    output_dir = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    raw = output_dir / "raw"
    results = output_dir / "results"
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    flow_windows = {item["key"]: int(item["days"]) for item in summary["flow_windows"]}
    rps_windows = {item["key"]: int(item["days"]) for item in summary["rps_windows"]}
    primary_key = max(flow_windows, key=flow_windows.get)
    cap_floor = float(summary["market_cap_floor_yi"])

    industry_raw = read_csv(raw / "industry_flow_ths.csv")
    stock_raw = read_csv(raw / "stock_flow_ths.csv")
    classic_raw = read_csv(raw / "stock_flow_moneyflow.csv")
    daily_raw = read_csv(raw / "daily.csv")
    adj_raw = read_csv(raw / "adj_factor.csv")
    daily_basic = read_csv(raw / "daily_basic.csv")
    stock_basic = read_csv(raw / "stock_basic.csv")
    fund_basic = read_csv(raw / "fund_basic.csv")
    fund_daily = read_csv(raw / "fund_daily.csv")
    fund_share = read_csv(raw / "fund_share.csv")
    members = read_csv(raw / "industry_members.csv")
    industry_result = read_csv(results / "industry_window_summary.csv")
    stock_result = read_csv(results / "stock_window_summary.csv")
    largecap_result = read_csv(results / "stock_largecap_flow_ratio.csv")
    rps_result = read_csv(results / "stock_rps_all.csv")
    etf_result = read_csv(results / "etf_summary.csv")
    production_checks = read_csv(results / "validation_checks.csv")
    checks: list[dict[str, Any]] = []

    # 日期完全从原始事实表推导，summary 只作为待验证对象。
    dates = sorted({row["trade_date"] for row in daily_raw})
    flow_dates = sorted({row["trade_date"] for row in stock_raw})
    expected_date_count = max(max(flow_windows.values()) + 1, max(rps_windows.values()) + 1)
    other_flow_dates = {
        "industry": sorted({row["trade_date"] for row in industry_raw}),
        "classic": sorted({row["trade_date"] for row in classic_raw}),
    }
    date_ok = (
        len(dates) == expected_date_count
        and len(flow_dates) == max(flow_windows.values())
        and flow_dates == dates[-max(flow_windows.values()):]
        and all(value == flow_dates for value in other_flow_dates.values())
        and summary["trade_dates"] == dates
        and summary["flow_dates"] == flow_dates
        and summary["latest_trade_date"] == dates[-1]
    )
    checks.append(check(
        "原始事实表独立推导交易日", date_ok,
        f"flow={len(flow_dates)}/{max(flow_windows.values())}, endpoints={len(dates)}/{expected_date_count}",
    ))
    latest = dates[-1]

    api_rows = {
        "moneyflow_ind_ths": industry_raw, "moneyflow_ths": stock_raw, "moneyflow": classic_raw,
        "daily": daily_raw, "adj_factor": adj_raw, "fund_daily": fund_daily, "fund_share": fund_share,
    }
    partition_errors = 0
    partition_detail: dict[str, Any] = {}
    for api_name, rows in api_rows.items():
        counts = partition_counts(rows)
        wanted = flow_dates if api_name in {"moneyflow_ind_ths", "moneyflow_ths", "moneyflow"} else dates
        missing = sorted(set(wanted) - set(counts))
        touched = sorted(date for date, count in counts.items() if count >= API_LIMITS[api_name])
        partition_errors += len(missing) + len(touched)
        partition_detail[api_name] = {"max": max(counts.values(), default=0), "limit": API_LIMITS[api_name], "missing": missing, "touched": touched}
    checks.append(check("逐接口分区与真实上限", partition_errors == 0, json.dumps(partition_detail, ensure_ascii=False)))

    industry_group = grouped(industry_raw)
    industry_by_code = {row["ts_code"]: row for row in industry_result}
    industry_errors = int(set(industry_group) != set(industry_by_code))
    industry_rank_errors = 0
    for key, days in flow_windows.items():
        values: dict[str, float] = {}
        wanted = set(flow_dates[-days:])
        for code, rows in industry_group.items():
            selected = [row for row in rows if row["trade_date"] in wanted]
            expected = sum(num(row["net_amount"]) for row in selected)
            result = industry_by_code[code]
            industry_errors += int(abs(expected - num(result[f"net_{key}_yi"])) > 1e-6)
            industry_errors += int(result["latest_trade_date"] != rows[-1]["trade_date"])
            complete = len({row["trade_date"] for row in selected}) == days and rows[-1]["trade_date"] == latest
            industry_errors += int(present(result.get(f"{key}_inflow_rank")) != complete)
            if complete:
                values[code] = expected
        expected_in = sorted(values, key=lambda code: (-values[code], code))[0]
        expected_out = sorted(values, key=lambda code: (values[code], code))[0]
        actual_in = min((row for row in industry_result if present(row.get(f"{key}_inflow_rank"))), key=lambda row: num(row[f"{key}_inflow_rank"]))["ts_code"]
        actual_out = min((row for row in industry_result if present(row.get(f"{key}_outflow_rank"))), key=lambda row: num(row[f"{key}_outflow_rank"]))["ts_code"]
        industry_rank_errors += int(expected_in != actual_in) + int(expected_out != actual_out)
    checks.append(check("行业窗口、资格与榜首独立复算", industry_errors == 0 and industry_rank_errors == 0, f"数值/资格错误={industry_errors}, 榜首错误={industry_rank_errors}"))

    stock_group = grouped(stock_raw)
    stock_by_code = {row["ts_code"]: row for row in stock_result}
    stock_errors = int(set(stock_group) != set(stock_by_code))
    stock_rank_errors = 0
    for key, days in flow_windows.items():
        values: dict[str, float] = {}
        wanted = set(flow_dates[-days:])
        for code, rows in stock_group.items():
            selected = [row for row in rows if row["trade_date"] in wanted]
            expected = sum(num(row["net_amount"]) for row in selected)
            result = stock_by_code[code]
            stock_errors += int(abs(expected - num(result[f"net_{key}_wan"])) > 0.011)
            stock_errors += int(result["latest_trade_date"] != rows[-1]["trade_date"])
            complete = len({row["trade_date"] for row in selected}) == days and rows[-1]["trade_date"] == latest
            stock_errors += int(present(result.get(f"{key}_inflow_rank")) != complete)
            if complete:
                values[code] = expected
        expected_in = sorted(values, key=lambda code: (-values[code], code))[0]
        expected_out = sorted(values, key=lambda code: (values[code], code))[0]
        actual_in = min((row for row in stock_result if present(row.get(f"{key}_inflow_rank"))), key=lambda row: num(row[f"{key}_inflow_rank"]))["ts_code"]
        actual_out = min((row for row in stock_result if present(row.get(f"{key}_outflow_rank"))), key=lambda row: num(row[f"{key}_outflow_rank"]))["ts_code"]
        stock_rank_errors += int(expected_in != actual_in) + int(expected_out != actual_out)
    checks.append(check("沪深个股窗口、资格与榜首独立复算", stock_errors == 0 and stock_rank_errors == 0, f"数值/资格错误={stock_errors}, 榜首错误={stock_rank_errors}"))

    mv_by_code = {row["ts_code"]: num(row["total_mv"]) for row in daily_basic}
    largecap_codes = {row["ts_code"] for row in largecap_result}
    ratio_errors = 0
    expected_largecap: set[str] = set()
    primary_days = flow_windows[primary_key]
    for row in stock_result:
        code = row["ts_code"]
        mv = mv_by_code.get(code, 0.0)
        eligible = row["latest_trade_date"] == latest and int(num(row[f"days_covered_{primary_key}"])) == primary_days
        if mv >= cap_floor * 10000 and eligible:
            expected_largecap.add(code)
        if mv:
            for key in flow_windows:
                expected = 100 * num(row[f"net_{key}_wan"]) / mv
                ratio_errors += int(abs(expected - num(row[f"flow_mv_ratio_{key}_pct"])) > 0.000002)
    checks.append(check("市值门槛、资格与比例", ratio_errors == 0 and largecap_codes == expected_largecap, f"比例错误={ratio_errors}, 集合差异={len(largecap_codes ^ expected_largecap)}"))

    basic_exchange = {row["ts_code"]: row.get("exchange", "") for row in stock_basic}
    daily_latest = {row["ts_code"] for row in daily_raw if row["trade_date"] == latest}
    ths_latest = {row["ts_code"] for row in stock_raw if row["trade_date"] == latest}
    classic_latest = {row["ts_code"] for row in classic_raw if row["trade_date"] == latest}
    mainland = {code for code in daily_latest if basic_exchange.get(code) in {"SSE", "SZSE"}}
    bse = {code for code in daily_latest if basic_exchange.get(code) == "BSE"}
    mainland_rate = len(mainland & ths_latest) / len(mainland) if mainland else 0.0
    bse_standard_rate = len(bse & classic_latest) / len(bse) if bse else 0.0
    universe_ok = mainland_rate >= 0.99 and bse_standard_rate >= 0.95 and "沪深" in summary.get("stock_flow_universe", "") and not (bse & ths_latest)
    checks.append(check("按交易所核对资金流样本池", universe_ok, f"沪深THS={mainland_rate:.4%}, 北交所standard={bse_standard_rate:.4%}, 北交所THS={len(bse & ths_latest)}"))

    daily_lookup = {(row["trade_date"], row["ts_code"]): row for row in daily_raw}
    adj_lookup = {(row["trade_date"], row["ts_code"]): row for row in adj_raw}
    rps_by_code = {row["ts_code"]: row for row in rps_result}
    latest_daily_codes = {row["ts_code"] for row in daily_raw if row["trade_date"] == latest}
    rps_errors = int(set(rps_by_code) != latest_daily_codes)
    for key, days in rps_windows.items():
        base = dates[-(days + 1)]
        raw_returns: dict[str, float] = {}
        for code, result in rps_by_code.items():
            legs = (daily_lookup.get((latest, code)), daily_lookup.get((base, code)), adj_lookup.get((latest, code)), adj_lookup.get((base, code)))
            if all(legs):
                current = num(legs[0]["close"]) * num(legs[2]["adj_factor"])
                start = num(legs[1]["close"]) * num(legs[3]["adj_factor"])
                expected = 100 * (current / start - 1)
                raw_returns[code] = expected
                rps_errors += int(not present(result.get(f"return_{key}_pct")) or abs(expected - num(result[f"return_{key}_pct"])) > 0.00011)
            else:
                rps_errors += int(present(result.get(f"return_{key}_pct")) or present(result.get(f"rank_{key}")))
        ranks = average_ranks(raw_returns)
        for code, (rank, rps_value) in ranks.items():
            result = rps_by_code[code]
            rps_errors += int(abs(rank - num(result[f"rank_{key}"])) > 1e-9 or abs(rps_value - num(result[f"rps_{key}"])) > 0.00011)
        top_file = read_csv(results / f"stock_rps_{key}_top5pct.csv")
        expected_top = {code for code, (_, value) in ranks.items() if value >= 95.0}
        rps_errors += len(expected_top ^ {row["ts_code"] for row in top_file})
    checks.append(check("RPS原始收益、平均秩与前5%", rps_errors == 0, f"错误={rps_errors}"))

    fund_group = grouped(fund_daily)
    fund_names = {row["ts_code"]: row.get("name", "") for row in fund_basic}
    share_lookup = {(row["trade_date"], row["ts_code"]): num(row["fd_share"]) for row in fund_share}
    etf_by_code = {row["ts_code"]: row for row in etf_result}
    expected_etfs = {code for code, rows in fund_group.items() if rows[-1]["trade_date"] == latest and "ETF" in fund_names.get(code, "").upper()}
    etf_errors = int(set(etf_by_code) != expected_etfs)
    date_index = {date: index for index, date in enumerate(dates)}
    for code, result in etf_by_code.items():
        by_date = {row["trade_date"]: row for row in fund_group[code]}
        for key, days in rps_windows.items():
            selected = [by_date.get(date) for date in dates[-days:]]
            complete = all(selected)
            if complete:
                product = 1.0
                for row in selected:
                    product *= 1 + num(row["pct_chg"]) / 100
                etf_errors += int(abs(100 * (product - 1) - num(result[f"return_{key}_pct"])) > 0.00011)
            else:
                etf_errors += int(present(result.get(f"return_{key}_pct")) or present(result.get(f"rank_{key}")))
        for key, days in flow_windows.items():
            expected_flow = 0.0
            covered = 0
            for date in dates[-days:]:
                previous = dates[date_index[date] - 1]
                current_share = share_lookup.get((date, code))
                previous_share = share_lookup.get((previous, code))
                day = by_date.get(date)
                if current_share is None or previous_share is None or not day:
                    continue
                expected_flow += (current_share - previous_share) * num(day["close"])
                covered += 1
            etf_errors += int(covered != int(num(result[f"share_days_covered_{key}"])))
            partial = result.get(f"partial_estimated_flow_{key}_wan")
            etf_errors += int((covered > 0) != present(partial))
            if covered and abs(expected_flow - num(partial)) > 0.011:
                etf_errors += 1
            official = result.get(f"estimated_flow_{key}_wan")
            rank_present = present(result.get(f"flow_{key}_inflow_rank"))
            if covered == days:
                etf_errors += int(not present(official) or abs(expected_flow - num(official)) > 0.011 or not rank_present)
            else:
                etf_errors += int(present(official) or rank_present)
    checks.append(check("ETF身份、收益与完整窗口申赎", etf_errors == 0, f"错误={etf_errors}"))

    member_duplicates = len(members) - len({(row.get("ts_code"), row.get("con_code")) for row in members})
    checks.append(check("行业成员关系唯一且披露非互斥", member_duplicates == 0 and "不可跨行业加总" in summary.get("industry_membership_definition", ""), f"重复={member_duplicates}"))

    rolling = next((row for row in production_checks if row.get("check_id") == "rolling_5d"), {})
    rolling_ok = (5 in flow_windows.values() and rolling.get("status") == "PASS" and num(rolling.get("value")) >= 95) or (5 not in flow_windows.values() and rolling.get("status") == "SKIP")
    checks.append(check("五日专项校验零样本不得PASS", rolling_ok, f"status={rolling.get('status')}, value={rolling.get('value')}"))

    hash_errors = 0
    for name, expected in metadata.get("raw_sha256", {}).items():
        hash_errors += int(sha256(raw / name) != expected)
    for name, expected in metadata.get("result_sha256", {}).items():
        hash_errors += int(sha256(results / name) != expected)
    hash_errors += int(not metadata.get("git_sha"))
    checks.append(check("快照文件哈希与代码版本元数据", hash_errors == 0, f"错误={hash_errors}, git={metadata.get('git_sha')}"))

    report_path = output_dir / "report.html"
    checks.append(check("网页文件完整", report_path.exists() and report_path.stat().st_size > 10_000, str(report_path)))
    primary_top = min((row for row in stock_result if present(row.get(f"{primary_key}_inflow_rank"))), key=lambda row: num(row[f"{primary_key}_inflow_rank"]))
    checks.append(check("摘要榜首一致", summary["top_stock_primary_inflow"]["ts_code"] == primary_top["ts_code"], f"{primary_key}={primary_top['ts_code']}"))

    failed = [row for row in checks if row["status"] == "FAIL"]
    payload = {
        "status": "PASS" if not failed else "FAIL",
        "validated_output": output_dir.name,
        "latest_trade_date": latest,
        "flow_windows": flow_windows,
        "rps_windows": rps_windows,
        "checks": checks,
        "failure_count": len(failed),
        "method": "日期、端点、样本资格、排名和接口限额均从raw CSV独立推导",
    }
    destination = results / "independent_validation.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
