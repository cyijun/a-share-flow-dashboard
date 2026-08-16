#!/usr/bin/env python3
"""统计同花顺行业口径最近五个交易日的 A 股资金流，并执行多层校验。

仅使用 Python 标准库，通过 Tushare REST API 取数。TUSHARE_TOKEN 从环境变量
或项目根目录 .env 读取，任何输出文件和日志都不会包含 token。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
API_URL = "https://api.tushare.pro"
DOC_URLS = {
    "ths_hot": "https://tushare.pro/document/2?doc_id=320",
    "moneyflow_ind_ths": "https://tushare.pro/document/2?doc_id=343",
    "moneyflow_ths": "https://tushare.pro/document/2?doc_id=348",
    "ths_member": "https://tushare.pro/document/2?doc_id=261",
    "moneyflow": "https://tushare.pro/document/2?doc_id=170",
}


def load_dotenv(path: Path) -> None:
    """加载最小 .env 语法，不覆盖已经存在的环境变量。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def number(value: Any, default: float | None = 0.0) -> float | None:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def integer(value: Any, default: int = 0) -> int:
    parsed = number(value, None)
    return int(parsed) if parsed is not None else default


def rounded(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denominator = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    if denominator == 0:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / denominator


def compounded_return(percentages: Iterable[float]) -> float:
    product = 1.0
    found = False
    for pct in percentages:
        product *= 1.0 + pct / 100.0
        found = True
    return (product - 1.0) * 100.0 if found else 0.0


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    fields.append(key)
                    seen.add(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class TushareClient:
    def __init__(self, token: str) -> None:
        if not token:
            raise RuntimeError("TUSHARE_TOKEN 未配置；请在环境变量或 .env 中设置")
        self._token = token

    def call(
        self,
        api_name: str,
        params: dict[str, Any],
        fields: str = "",
        retries: int = 3,
    ) -> list[dict[str, Any]]:
        payload = {
            "api_name": api_name,
            "token": self._token,
            "params": params,
            "fields": fields,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                request = urllib.request.Request(
                    API_URL,
                    data=body,
                    headers={"Content-Type": "application/json", "User-Agent": "flow-track/1.0"},
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    result = json.load(response)
                if result.get("code") != 0:
                    message = str(result.get("msg") or result.get("code"))
                    if any(word in message.lower() for word in ("频率", "rate", "每分钟", "稍后")):
                        raise RuntimeError(message)
                    raise ValueError(f"{api_name}: {message}")
                data = result.get("data") or {}
                names = data.get("fields") or []
                return [dict(zip(names, item)) for item in (data.get("items") or [])]
            except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(1.5 * (2**attempt))
        raise RuntimeError(f"{api_name} 请求失败：{last_error}")


def fetch_open_dates(client: TushareClient, as_of: str) -> list[str]:
    year = int(as_of[:4])
    start_date = f"{year - 1}1201"
    rows = client.call(
        "trade_cal",
        {"exchange": "SSE", "start_date": start_date, "end_date": as_of, "is_open": "1"},
        "exchange,cal_date,is_open,pretrade_date",
    )
    dates = sorted({str(row["cal_date"]) for row in rows if str(row.get("is_open")) == "1"})
    if len(dates) < 5:
        raise RuntimeError(f"截至 {as_of} 仅取得 {len(dates)} 个开市日，无法构造五日窗口")
    return dates[-5:]


def fetch_by_date(client: TushareClient, api_name: str, dates: list[str], fields: str = "") -> list[dict]:
    rows: list[dict] = []
    for trade_date in dates:
        batch = client.call(api_name, {"trade_date": trade_date}, fields)
        rows.extend(batch)
        print(f"{api_name} {trade_date}: {len(batch)} rows", flush=True)
    return rows


def key_duplicate_count(rows: list[dict], fields: tuple[str, ...]) -> int:
    keys = [tuple(row.get(field) for field in fields) for row in rows]
    return len(keys) - len(set(keys))


def missing_required_count(rows: list[dict], fields: tuple[str, ...]) -> int:
    return sum(1 for row in rows for field in fields if row.get(field) in (None, ""))


def aggregate_industries(rows: list[dict], dates: list[str]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["ts_code"])].append(row)
    output: list[dict] = []
    for ts_code, group in grouped.items():
        group.sort(key=lambda row: str(row["trade_date"]))
        latest = group[-1]
        output.append({
            "ts_code": ts_code,
            "industry": latest.get("industry", ""),
            "trade_start": dates[0],
            "trade_end": dates[-1],
            "days_covered": len({str(row["trade_date"]) for row in group}),
            "company_num": integer(latest.get("company_num")),
            "net_amount_5d_yi": rounded(sum(number(row.get("net_amount")) or 0.0 for row in group), 4),
            "gross_inflow_5d_yi": rounded(sum(number(row.get("net_buy_amount")) or 0.0 for row in group), 4),
            "gross_outflow_5d_yi": rounded(sum(number(row.get("net_sell_amount")) or 0.0 for row in group), 4),
            "positive_days": sum(1 for row in group if (number(row.get("net_amount")) or 0.0) > 0),
            "return_5d_pct": rounded(compounded_return(number(row.get("pct_change")) or 0.0 for row in group), 4),
            "latest_close": rounded(number(latest.get("close"), None), 4),
            "latest_lead_stock": latest.get("lead_stock", ""),
        })
    output.sort(key=lambda row: (-(row["net_amount_5d_yi"] or 0.0), row["ts_code"]))
    for rank, row in enumerate(output, 1):
        row["rank"] = rank
    return output


def fetch_members(
    client: TushareClient,
    industries: list[dict],
    max_workers: int,
) -> tuple[list[dict], dict[str, list[dict]]]:
    by_code: dict[str, list[dict]] = {}
    failures: dict[str, str] = {}

    def one(code: str) -> tuple[str, list[dict]]:
        return code, client.call("ths_member", {"ts_code": code})

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(one, str(row["ts_code"])): str(row["ts_code"]) for row in industries}
        for future in as_completed(futures):
            code = futures[future]
            try:
                returned_code, rows = future.result()
                by_code[returned_code] = rows
            except Exception as exc:  # 失败会在汇总后显式阻断
                failures[code] = str(exc)
    if failures:
        sample = "; ".join(f"{code}: {message}" for code, message in list(failures.items())[:5])
        raise RuntimeError(f"{len(failures)} 个行业成分接口失败：{sample}")

    combined: list[dict] = []
    for industry in industries:
        code = str(industry["ts_code"])
        name = str(industry["industry"])
        rows = by_code.get(code, [])
        normalized: list[dict] = []
        for row in rows:
            item = dict(row)
            item["industry"] = name
            normalized.append(item)
            combined.append(item)
        by_code[code] = normalized
    return combined, by_code


def aggregate_stocks(
    rows: list[dict],
    dates: list[str],
    membership_by_stock: dict[str, list[dict]],
) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["ts_code"])].append(row)
    output: list[dict] = []
    for ts_code, group in grouped.items():
        group.sort(key=lambda row: str(row["trade_date"]))
        latest = group[-1]
        memberships = membership_by_stock.get(ts_code, [])
        output.append({
            "ts_code": ts_code,
            "name": latest.get("name", ""),
            "industries": "；".join(sorted({str(row["industry"]) for row in memberships})),
            "industry_codes": "；".join(sorted({str(row["ts_code"]) for row in memberships})),
            "trade_start": dates[0],
            "trade_end": dates[-1],
            "days_covered": len({str(row["trade_date"]) for row in group}),
            "net_amount_5d_wan": rounded(sum(number(row.get("net_amount")) or 0.0 for row in group), 2),
            "large_order_net_5d_wan": rounded(sum(number(row.get("buy_lg_amount")) or 0.0 for row in group), 2),
            "medium_order_net_5d_wan": rounded(sum(number(row.get("buy_md_amount")) or 0.0 for row in group), 2),
            "small_order_net_5d_wan": rounded(sum(number(row.get("buy_sm_amount")) or 0.0 for row in group), 2),
            "positive_days": sum(1 for row in group if (number(row.get("net_amount")) or 0.0) > 0),
            "return_5d_pct": rounded(compounded_return(number(row.get("pct_change")) or 0.0 for row in group), 4),
            "latest_price": rounded(number(latest.get("latest"), None), 4),
            "reported_net_d5_wan": rounded(number(latest.get("net_d5_amount"), None), 2),
        })
    output.sort(key=lambda row: (-(row["net_amount_5d_wan"] or 0.0), row["ts_code"]))
    for rank, row in enumerate(output, 1):
        row["rank"] = rank
    return output


def aggregate_classic_moneyflow(
    rows: list[dict],
    dates: list[str],
    stock_basic_by_code: dict[str, dict],
    membership_by_stock: dict[str, list[dict]],
) -> list[dict]:
    """聚合标准 moneyflow，作为覆盖北交所的全 A 股独立复核口径。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["ts_code"])].append(row)
    output: list[dict] = []
    for ts_code, group in grouped.items():
        basic = stock_basic_by_code.get(ts_code, {})
        memberships = membership_by_stock.get(ts_code, [])
        output.append({
            "ts_code": ts_code,
            "name": basic.get("name", ""),
            "exchange": basic.get("exchange", ts_code.split(".")[-1]),
            "market": basic.get("market", ""),
            "industries": "；".join(sorted({str(row["industry"]) for row in memberships})),
            "trade_start": dates[0],
            "trade_end": dates[-1],
            "days_covered": len({str(row["trade_date"]) for row in group}),
            "net_mf_amount_5d_wan": rounded(sum(number(row.get("net_mf_amount")) or 0.0 for row in group), 2),
        })
    output.sort(key=lambda row: (-(row["net_mf_amount_5d_wan"] or 0.0), row["ts_code"]))
    for rank, row in enumerate(output, 1):
        row["rank"] = rank
    return output


def make_check(
    check_id: str,
    label: str,
    status: str,
    severity: str,
    value: Any,
    threshold: str,
    note: str,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "check": label,
        "status": status,
        "severity": severity,
        "value": value,
        "threshold": threshold,
        "note": note,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        default=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d"),
        help="统计截止日，YYYYMMDD；自动裁剪到最近开市日",
    )
    parser.add_argument("--output-root", default=str(ROOT / "outputs"), help="输出根目录")
    parser.add_argument("--member-workers", type=int, default=6, help="行业成分并发数")
    args = parser.parse_args()

    if len(args.as_of) != 8 or not args.as_of.isdigit():
        raise ValueError("--as-of 必须为 YYYYMMDD")
    load_dotenv(ROOT / ".env")
    client = TushareClient(os.environ.get("TUSHARE_TOKEN", ""))
    generated_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    dates = fetch_open_dates(client, args.as_of)
    latest_date = dates[-1]
    output_dir = Path(args.output_root) / latest_date
    raw_dir = output_dir / "raw"
    result_dir = output_dir / "results"
    print(f"统计窗口：{dates[0]} 至 {dates[-1]}（{', '.join(dates)}）", flush=True)

    industry_raw = fetch_by_date(client, "moneyflow_ind_ths", dates)
    stock_flow_raw = fetch_by_date(client, "moneyflow_ths", dates)
    classic_flow_raw = fetch_by_date(
        client,
        "moneyflow",
        dates,
        "ts_code,trade_date,net_mf_vol,net_mf_amount",
    )
    daily_raw = fetch_by_date(
        client,
        "daily",
        dates,
        "ts_code,trade_date,close,pct_chg,amount",
    )
    stock_basic = client.call(
        "stock_basic",
        {"exchange": "", "list_status": "L"},
        "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date",
    )
    ths_industry_index = client.call("ths_index", {"exchange": "A", "type": "I"})

    write_csv(raw_dir / "industry_flow_ths.csv", industry_raw)
    write_csv(raw_dir / "stock_flow_ths.csv", stock_flow_raw)
    write_csv(raw_dir / "stock_flow_moneyflow.csv", classic_flow_raw)
    write_csv(raw_dir / "daily.csv", daily_raw)
    write_csv(raw_dir / "stock_basic.csv", stock_basic)
    write_csv(raw_dir / "ths_industry_index.csv", ths_industry_index)

    industry_agg = aggregate_industries(industry_raw, dates)
    members_raw, members_by_industry = fetch_members(client, industry_agg, args.member_workers)
    write_csv(raw_dir / "industry_members.csv", members_raw)

    membership_by_stock: dict[str, list[dict]] = defaultdict(list)
    for row in members_raw:
        membership_by_stock[str(row.get("con_code"))].append(row)
    stock_basic_lookup = {str(row["ts_code"]): row for row in stock_basic}
    stock_agg = aggregate_stocks(stock_flow_raw, dates, membership_by_stock)
    classic_stock_agg = aggregate_classic_moneyflow(
        classic_flow_raw, dates, stock_basic_lookup, membership_by_stock
    )
    stock_agg_by_code = {str(row["ts_code"]): row for row in stock_agg}
    top_industries = industry_agg[:10]
    top_stocks = stock_agg[:20]

    top_by_industry: list[dict] = []
    for industry in top_industries:
        code = str(industry["ts_code"])
        members = {str(row.get("con_code")) for row in members_by_industry.get(code, [])}
        candidates = [stock_agg_by_code[member] for member in members if member in stock_agg_by_code]
        candidates.sort(key=lambda row: (-(row["net_amount_5d_wan"] or 0.0), row["ts_code"]))
        for rank, stock in enumerate(candidates[:5], 1):
            top_by_industry.append({
                "industry_rank": industry["rank"],
                "industry_code": code,
                "industry": industry["industry"],
                "industry_net_amount_5d_yi": industry["net_amount_5d_yi"],
                "stock_rank_in_industry": rank,
                "ts_code": stock["ts_code"],
                "name": stock["name"],
                "stock_net_amount_5d_wan": stock["net_amount_5d_wan"],
                "positive_days": stock["positive_days"],
                "days_covered": stock["days_covered"],
                "return_5d_pct": stock["return_5d_pct"],
            })

    write_csv(result_dir / "industry_flow_5d.csv", industry_agg)
    write_csv(result_dir / "industry_flow_top10.csv", top_industries)
    write_csv(result_dir / "stock_flow_5d.csv", stock_agg)
    write_csv(result_dir / "stock_flow_top20.csv", top_stocks)
    write_csv(result_dir / "stock_flow_top_by_industry.csv", top_by_industry)
    write_csv(result_dir / "stock_flow_all_a_standard_5d.csv", classic_stock_agg)
    write_csv(result_dir / "stock_flow_all_a_standard_top20.csv", classic_stock_agg[:20])

    # 数据质量与交叉验证
    checks: list[dict[str, Any]] = []
    industry_dup = key_duplicate_count(industry_raw, ("trade_date", "ts_code"))
    stock_dup = key_duplicate_count(stock_flow_raw, ("trade_date", "ts_code"))
    classic_dup = key_duplicate_count(classic_flow_raw, ("trade_date", "ts_code"))
    daily_dup = key_duplicate_count(daily_raw, ("trade_date", "ts_code"))
    industry_required_missing = missing_required_count(
        industry_raw, ("trade_date", "ts_code", "industry", "net_amount")
    )
    stock_required_missing = missing_required_count(
        stock_flow_raw, ("trade_date", "ts_code", "name", "net_amount")
    )
    rows_by_date = {
        date: {
            "industry_rows": sum(1 for row in industry_raw if str(row.get("trade_date")) == date),
            "stock_flow_rows": sum(1 for row in stock_flow_raw if str(row.get("trade_date")) == date),
            "classic_flow_rows": sum(1 for row in classic_flow_raw if str(row.get("trade_date")) == date),
            "daily_rows": sum(1 for row in daily_raw if str(row.get("trade_date")) == date),
        }
        for date in dates
    }
    coverage_by_date: list[dict] = []
    for date in dates:
        flow_codes = {str(row["ts_code"]) for row in stock_flow_raw if str(row.get("trade_date")) == date}
        daily_codes = {str(row["ts_code"]) for row in daily_raw if str(row.get("trade_date")) == date}
        common = flow_codes & daily_codes
        coverage_by_date.append({
            "trade_date": date,
            **rows_by_date[date],
            "matched_codes": len(common),
            "moneyflow_coverage_of_daily_pct": rounded(100 * len(common) / len(daily_codes), 4) if daily_codes else None,
            "daily_without_ths_moneyflow": len(daily_codes - flow_codes),
            "ths_moneyflow_without_daily": len(flow_codes - daily_codes),
        })
    write_csv(result_dir / "coverage_by_date.csv", coverage_by_date)

    daily_lookup = {
        (str(row["trade_date"]), str(row["ts_code"])): row
        for row in daily_raw
    }
    pct_diffs: list[float] = []
    for row in stock_flow_raw:
        match = daily_lookup.get((str(row["trade_date"]), str(row["ts_code"])))
        left = number(row.get("pct_change"), None)
        right = number(match.get("pct_chg"), None) if match else None
        if left is not None and right is not None:
            pct_diffs.append(abs(left - right))
    pct_match_rate = (
        sum(1 for diff in pct_diffs if diff <= 0.02) / len(pct_diffs) if pct_diffs else 0.0
    )

    order_sum_diffs: list[float] = []
    order_sum_pass = 0
    for row in stock_flow_raw:
        net_amount = number(row.get("net_amount"), None)
        parts = [number(row.get(field), None) for field in ("buy_lg_amount", "buy_md_amount", "buy_sm_amount")]
        if net_amount is None or any(part is None for part in parts):
            continue
        diff = abs(net_amount - sum(part or 0.0 for part in parts))
        tolerance = max(5.0, abs(net_amount) * 0.002)
        order_sum_diffs.append(diff)
        order_sum_pass += int(diff <= tolerance)
    order_sum_rate = order_sum_pass / len(order_sum_diffs) if order_sum_diffs else 0.0

    rolling_diffs: list[float] = []
    rolling_values: list[float] = []
    rolling_reported: list[float] = []
    rolling_pass = 0
    for stock in stock_agg:
        # 文档中的 net_d5_amount 是“五日主力净额”，与逐日大单净额之和同口径，
        # 不能和包含大/中/小单的 net_amount 五日和比较。
        calculated = number(stock.get("large_order_net_5d_wan"), None)
        reported = number(stock.get("reported_net_d5_wan"), None)
        if stock.get("days_covered") != 5 or calculated is None or reported is None:
            continue
        diff = abs(calculated - reported)
        tolerance = max(10.0, abs(calculated) * 0.005)
        rolling_diffs.append(diff)
        rolling_values.append(calculated)
        rolling_reported.append(reported)
        rolling_pass += int(diff <= tolerance)
    rolling_match_rate = rolling_pass / len(rolling_diffs) if rolling_diffs else 0.0

    latest_industry = {
        str(row["ts_code"]): row for row in industry_raw if str(row.get("trade_date")) == latest_date
    }
    member_checks: list[dict] = []
    for industry in industry_agg:
        code = str(industry["ts_code"])
        expected = integer(latest_industry.get(code, {}).get("company_num"))
        actual = len({str(row.get("con_code")) for row in members_by_industry.get(code, [])})
        member_checks.append({
            "ts_code": code,
            "industry": industry["industry"],
            "reported_company_num": expected,
            "member_rows": actual,
            "difference": actual - expected,
            "exact_match": actual == expected,
        })
    member_exact_rate = sum(1 for row in member_checks if row["exact_match"]) / len(member_checks)
    member_near_rate = sum(1 for row in member_checks if abs(row["difference"]) <= 1) / len(member_checks)
    write_csv(result_dir / "industry_member_count_check.csv", member_checks)

    index_lookup = {str(row.get("ts_code")): row for row in ths_industry_index}
    industry_index_code_rate = sum(1 for row in industry_agg if row["ts_code"] in index_lookup) / len(industry_agg)
    industry_index_name_rate = sum(
        1
        for row in industry_agg
        if row["ts_code"] in index_lookup and str(index_lookup[row["ts_code"]].get("name")) == str(row["industry"])
    ) / len(industry_agg)

    stock_flow_lookup = {
        (str(row["trade_date"]), str(row["ts_code"])): number(row.get("net_amount")) or 0.0
        for row in stock_flow_raw
    }
    industry_reconciliation: list[dict] = []
    rec_reported: list[float] = []
    rec_components: list[float] = []
    for row in industry_raw:
        date = str(row["trade_date"])
        code = str(row["ts_code"])
        member_codes = {str(member.get("con_code")) for member in members_by_industry.get(code, [])}
        component_yi = sum(stock_flow_lookup.get((date, member), 0.0) for member in member_codes) / 10000.0
        reported_yi = number(row.get("net_amount")) or 0.0
        rec_reported.append(reported_yi)
        rec_components.append(component_yi)
        industry_reconciliation.append({
            "trade_date": date,
            "ts_code": code,
            "industry": row.get("industry", ""),
            "reported_industry_net_yi": rounded(reported_yi, 4),
            "sum_member_stock_net_yi": rounded(component_yi, 4),
            "difference_yi": rounded(component_yi - reported_yi, 4),
            "same_direction": (reported_yi == 0 and component_yi == 0) or (reported_yi * component_yi > 0),
            "members_with_flow": sum(1 for member in member_codes if (date, member) in stock_flow_lookup),
            "member_count": len(member_codes),
        })
    write_csv(result_dir / "industry_reconciliation.csv", industry_reconciliation)
    industry_rec_corr = pearson(rec_reported, rec_components)
    # 行业接口净额以整数亿元返回，0 附近的方向会被舍入。方向复核只比较
    # 两边绝对值都至少 1 亿元的有效记录。
    material_rec_pairs = [
        (reported, components)
        for reported, components in zip(rec_reported, rec_components)
        if abs(reported) >= 1.0 and abs(components) >= 1.0
    ]
    industry_rec_sign_rate = (
        sum(1 for reported, components in material_rec_pairs if reported * components > 0)
        / len(material_rec_pairs)
        if material_rec_pairs else 0.0
    )

    classic_group: dict[str, float] = defaultdict(float)
    for row in classic_flow_raw:
        classic_group[str(row["ts_code"])] += number(row.get("net_mf_amount")) or 0.0
    common_codes = [row["ts_code"] for row in stock_agg if row["ts_code"] in classic_group]
    ths_values = [number(stock_agg_by_code[code]["net_amount_5d_wan"]) or 0.0 for code in common_codes]
    classic_values = [classic_group[code] for code in common_codes]
    nontrivial_pairs = [(x, y) for x, y in zip(ths_values, classic_values) if abs(x) >= 10 or abs(y) >= 10]
    classic_sign_rate = (
        sum(1 for x, y in nontrivial_pairs if x * y > 0 or (x == 0 and y == 0)) / len(nontrivial_pairs)
        if nontrivial_pairs else 0.0
    )
    classic_corr = pearson(ths_values, classic_values)
    classic_top20 = {str(row["ts_code"]) for row in classic_stock_agg[:20]}
    ths_top20_codes = {str(row["ts_code"]) for row in top_stocks}
    top20_overlap = len(classic_top20 & ths_top20_codes)
    bse_stocks = [row for row in classic_stock_agg if row.get("exchange") == "BSE"]
    bse_best = bse_stocks[0] if bse_stocks else None
    classic_flow_keys = {(str(row["trade_date"]), str(row["ts_code"])) for row in classic_flow_raw}
    daily_keys = {(str(row["trade_date"]), str(row["ts_code"])) for row in daily_raw}
    classic_daily_coverage = len(classic_flow_keys & daily_keys) / len(daily_keys) if daily_keys else 0.0

    coverage_by_market_counter: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for row in daily_raw:
        date = str(row["trade_date"])
        code = str(row["ts_code"])
        basic = stock_basic_lookup.get(code, {})
        market = str(basic.get("market") or "未知")
        exchange = str(basic.get("exchange") or code.split(".")[-1])
        bucket = coverage_by_market_counter[(exchange, market)]
        bucket["daily_rows"] += 1
        if (date, code) in stock_flow_lookup:
            bucket["moneyflow_rows"] += 1
    coverage_by_market: list[dict] = []
    for (exchange, market), counter in sorted(coverage_by_market_counter.items()):
        coverage_by_market.append({
            "exchange": exchange,
            "market": market,
            "daily_rows_5d": counter["daily_rows"],
            "moneyflow_rows_5d": counter["moneyflow_rows"],
            "coverage_pct": rounded(100 * counter["moneyflow_rows"] / counter["daily_rows"], 4),
        })
    write_csv(result_dir / "coverage_by_market.csv", coverage_by_market)

    multi_membership_stocks = sum(1 for rows in membership_by_stock.values() if len(rows) > 1)
    checks.extend([
        make_check("dates", "交易日窗口完整", "PASS" if len(dates) == 5 else "FAIL", "critical", len(dates), "= 5", "SSE 交易日历，截止日自动裁剪"),
        make_check("industry_keys", "行业资金流主键唯一", "PASS" if industry_dup == 0 else "FAIL", "critical", industry_dup, "= 0", "主键为 trade_date + ts_code"),
        make_check("stock_keys", "个股资金流主键唯一", "PASS" if stock_dup == 0 else "FAIL", "critical", stock_dup, "= 0", "主键为 trade_date + ts_code"),
        make_check("classic_keys", "标准个股资金流主键唯一", "PASS" if classic_dup == 0 else "FAIL", "critical", classic_dup, "= 0", "用于全 A 股与北交所覆盖复核"),
        make_check("daily_keys", "日行情主键唯一", "PASS" if daily_dup == 0 else "FAIL", "critical", daily_dup, "= 0", "主键为 trade_date + ts_code"),
        make_check("required_fields", "关键字段完整", "PASS" if industry_required_missing + stock_required_missing == 0 else "FAIL", "high", industry_required_missing + stock_required_missing, "= 0", "日期、代码、名称、净流入额"),
        make_check("api_row_limit", "单日个股资金流未触及接口上限", "PASS" if max(row["stock_flow_rows"] for row in rows_by_date.values()) < 6000 else "FAIL", "critical", max(row["stock_flow_rows"] for row in rows_by_date.values()), "< 6000", "按交易日分批，防止区间请求截断"),
        make_check("price_crosscheck", "资金流涨跌幅与官方日行情一致", "PASS" if pct_match_rate >= 0.99 else "WARN", "high", rounded(100 * pct_match_rate, 4), ">= 99%", f"共同记录 {len(pct_diffs)} 条，绝对误差<=0.02个百分点"),
        make_check("order_arithmetic", "个股净额与大/中/小单净额和一致", "PASS" if order_sum_rate >= 0.99 else "WARN", "high", rounded(100 * order_sum_rate, 4), ">= 99%", f"可比记录 {len(order_sum_diffs)} 条"),
        make_check("rolling_five_day", "逐日大单五日和与接口五日主力净额一致", "PASS" if rolling_match_rate >= 0.95 else "WARN", "high", rounded(100 * rolling_match_rate, 4), ">= 95%", f"完整五日且可比股票 {len(rolling_diffs)} 只"),
        make_check("member_counts", "行业公司数与成分接口近似一致", "PASS" if member_near_rate >= 0.95 else "WARN", "medium", rounded(100 * member_near_rate, 4), ">= 95%（差异<=1只）", f"完全一致 {rounded(100 * member_exact_rate, 2)}%；两项 company_num 为成分数的 2 倍"),
        make_check("industry_index_codes", "资金流行业代码存在于同花顺行业模板", "PASS" if industry_index_code_rate == 1 else "FAIL", "critical", rounded(100 * industry_index_code_rate, 4), "= 100%", "moneyflow_ind_ths 对 ths_index(type=I)"),
        make_check("industry_index_names", "行业代码与名称映射一致", "PASS" if industry_index_name_rate >= 0.99 else "WARN", "high", rounded(100 * industry_index_name_rate, 4), ">= 99%", "代码和行业名双字段核对"),
        make_check("industry_reconciliation", "行业净额与当前成分股净额和方向一致", "PASS" if industry_rec_sign_rate >= 0.9 else "WARN", "medium", rounded(100 * industry_rec_sign_rate, 4), ">= 90%", f"排除整数亿元舍入后的近零记录；有效 {len(material_rec_pairs)} 条，相关系数 {rounded(industry_rec_corr, 4)}"),
        make_check("classic_crosscheck", "THS 与标准 moneyflow 方向一致", "PASS" if classic_sign_rate >= 0.7 else "WARN", "medium", rounded(100 * classic_sign_rate, 4), ">= 70%", f"两接口算法不同；相关系数 {rounded(classic_corr, 4)}，Top20重合 {top20_overlap}"),
        make_check("all_a_coverage", "标准 moneyflow 覆盖全 A 股日行情", "PASS" if classic_daily_coverage == 1 else "WARN", "high", rounded(100 * classic_daily_coverage, 4), "= 100%", f"THS 个股接口不含北交所；北交所最高标准口径排名 {bse_best['rank'] if bse_best else '无'}"),
        make_check("membership_uniqueness", "当前 90 个资金流行业内股票归属唯一", "PASS" if multi_membership_stocks == 0 else "WARN", "low", multi_membership_stocks, "= 0", "同花顺成分源存在少量重叠；整体榜保留全部归属，行业内榜按各自成分计算"),
    ])
    write_csv(result_dir / "validation_checks.csv", checks)

    hard_failures = [row for row in checks if row["status"] == "FAIL" and row["severity"] in {"critical", "high"}]
    warnings = [row for row in checks if row["status"] == "WARN"]
    quality_status = "PASS" if not hard_failures and not warnings else ("PASS_WITH_WARNINGS" if not hard_failures else "FAIL")

    summary = {
        "generated_at": generated_at,
        "as_of_requested": args.as_of,
        "trade_dates": dates,
        "latest_trade_date": latest_date,
        "classification": "同花顺行业（moneyflow_ind_ths / ths_index type=I / ths_member）",
        "ranking_metric": "五个交易日 net_amount 累计；行业单位亿元，个股单位万元",
        "quality_status": quality_status,
        "hard_failure_count": len(hard_failures),
        "warning_count": len(warnings),
        "top_industries": top_industries,
        "top_stocks": top_stocks,
        "top_stocks_by_industry": top_by_industry,
        "all_a_standard_top_stocks": classic_stock_agg[:20],
        "bse_best_standard": bse_best,
        "validation": {
            "checks": checks,
            "pct_change_match_rate": rounded(pct_match_rate, 6),
            "pct_change_abs_diff_median": rounded(statistics.median(pct_diffs), 6) if pct_diffs else None,
            "order_arithmetic_match_rate": rounded(order_sum_rate, 6),
            "order_arithmetic_abs_diff_median_wan": rounded(statistics.median(order_sum_diffs), 4) if order_sum_diffs else None,
            "rolling_large_order_five_day_match_rate": rounded(rolling_match_rate, 6),
            "rolling_large_order_five_day_abs_diff_median_wan": rounded(statistics.median(rolling_diffs), 4) if rolling_diffs else None,
            "rolling_large_order_five_day_correlation": rounded(pearson(rolling_values, rolling_reported), 6),
            "industry_member_exact_rate": rounded(member_exact_rate, 6),
            "industry_member_within_one_rate": rounded(member_near_rate, 6),
            "industry_reconciliation_correlation": rounded(industry_rec_corr, 6),
            "industry_reconciliation_material_sign_rate": rounded(industry_rec_sign_rate, 6),
            "industry_reconciliation_material_rows": len(material_rec_pairs),
            "classic_moneyflow_correlation": rounded(classic_corr, 6),
            "classic_moneyflow_sign_rate": rounded(classic_sign_rate, 6),
            "classic_top20_overlap": top20_overlap,
            "classic_daily_coverage": rounded(classic_daily_coverage, 6),
            "coverage_by_date": coverage_by_date,
            "coverage_by_market": coverage_by_market,
        },
        "source_documents": DOC_URLS,
        "output_dir": str(output_dir.relative_to(ROOT)),
    }
    write_json(result_dir / "summary.json", summary)
    metadata = {
        "generated_at": generated_at,
        "timezone": "Asia/Shanghai",
        "requested_as_of": args.as_of,
        "trade_dates": dates,
        "interfaces": [
            "trade_cal", "moneyflow_ind_ths", "moneyflow_ths", "moneyflow",
            "daily", "stock_basic", "ths_index", "ths_member",
        ],
        "source_documents": DOC_URLS,
        "raw_row_counts": {
            "industry_flow_ths": len(industry_raw),
            "stock_flow_ths": len(stock_flow_raw),
            "stock_flow_moneyflow": len(classic_flow_raw),
            "daily": len(daily_raw),
            "stock_basic": len(stock_basic),
            "ths_industry_index": len(ths_industry_index),
            "industry_members": len(members_raw),
        },
        "failed_segments": [],
        "token_persisted": False,
    }
    write_json(output_dir / "metadata.json", metadata)
    print(json.dumps({
        "output_dir": str(output_dir),
        "quality_status": quality_status,
        "top_industry": top_industries[0] if top_industries else None,
        "top_stock": top_stocks[0] if top_stocks else None,
        "warnings": [row["check_id"] for row in warnings],
        "hard_failures": [row["check_id"] for row in hard_failures],
    }, ensure_ascii=False, indent=2))
    return 0 if not hard_failures else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
