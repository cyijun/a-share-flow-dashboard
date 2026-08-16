#!/usr/bin/env python3
"""生成 A 股资金流、RPS 与 ETF 统一仪表盘的数据快照。

所有时间窗均按交易日定义，可通过命令行参数调整。TUSHARE_TOKEN 仅从
环境变量或项目根目录 .env 读取。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ths_five_day_flow import (
    API_ROW_LIMITS,
    DOC_URLS,
    TushareClient,
    assert_api_batch,
    compounded_return,
    fetch_by_date,
    fetch_members,
    key_duplicate_count,
    load_dotenv,
    make_check,
    missing_required_count,
    number,
    pearson,
    rounded,
    write_csv,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = {"1d": 1, "5d": 5, "10d": 10}
WINDOW_LABELS = {"1d": "当天", "5d": "一周", "10d": "两周"}
RPS_WINDOWS = {"10d": 10, "20d": 20}
MARKET_CAP_FLOOR_YI = 200.0
DOCS = {
    **DOC_URLS,
    "daily": "https://tushare.pro/document/2?doc_id=27",
    "adj_factor": "https://tushare.pro/document/2?doc_id=28",
    "daily_basic": "https://tushare.pro/document/2?doc_id=32",
    "fund_basic": "https://tushare.pro/document/1?doc_id=19",
    "fund_daily": "https://tushare.pro/document/2?doc_id=127",
    "fund_share": "https://tushare.pro/document/2?doc_id=207",
}


def parse_day_list(value: str, option: str) -> list[int]:
    """解析逗号分隔的正整数交易日窗口，并保持升序去重。"""
    try:
        days = sorted({int(part.strip()) for part in value.split(",") if part.strip()})
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{option} 必须是逗号分隔的正整数") from exc
    if not days or any(day <= 0 for day in days):
        raise argparse.ArgumentTypeError(f"{option} 必须至少包含一个正整数")
    return days


def flow_label(days: int) -> str:
    return {1: "当天", 5: "一周", 10: "两周"}.get(days, f"{days}日")


def partition_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get("trade_date", ""))] += 1
    return dict(sorted(counts.items()))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def git_is_dirty() -> bool | None:
    try:
        return bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip())
    except (OSError, subprocess.SubprocessError):
        return None


def publish_snapshot(staging_dir: Path, final_dir: Path, generated_at: str) -> Path | None:
    """以目录重命名发布；已有快照先移入可恢复的_previous目录。"""
    backup: Path | None = None
    if final_dir.exists():
        backup_root = final_dir.parent / "_previous"
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = generated_at.replace(":", "").replace("+", "_")
        backup = backup_root / f"{final_dir.name}.{stamp}.{os.getpid()}"
        os.replace(final_dir, backup)
    os.replace(staging_dir, final_dir)
    return backup


def fetch_open_dates(client: TushareClient, as_of: str, count: int = 21) -> list[str]:
    year = int(as_of[:4])
    rows = client.call(
        "trade_cal",
        {"exchange": "SSE", "start_date": f"{year - 1}1201", "end_date": as_of, "is_open": "1"},
        "exchange,cal_date,is_open,pretrade_date",
    )
    dates = sorted({str(row["cal_date"]) for row in rows if str(row.get("is_open")) == "1"})
    if len(dates) < count:
        raise RuntimeError(f"截至 {as_of} 仅取得 {len(dates)} 个交易日，无法构造 {count} 日观察窗")
    return dates[-count:]


def rows_by_code(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["ts_code"])].append(row)
    for group in grouped.values():
        group.sort(key=lambda item: str(item.get("trade_date", "")))
    return grouped


def window_rows(group: list[dict[str, Any]], dates: list[str], days: int) -> list[dict[str, Any]]:
    selected = set(dates[-days:])
    return [row for row in group if str(row.get("trade_date")) in selected]


def apply_signed_ranks(rows: list[dict[str, Any]], field: str, prefix: str) -> None:
    inflow = sorted(rows, key=lambda row: (-(number(row.get(field)) or 0.0), str(row["ts_code"])))
    outflow = sorted(rows, key=lambda row: ((number(row.get(field)) or 0.0), str(row["ts_code"])))
    for rank, row in enumerate(inflow, 1):
        row[f"{prefix}_inflow_rank"] = rank
    for rank, row in enumerate(outflow, 1):
        row[f"{prefix}_outflow_rank"] = rank


def aggregate_industries(rows: list[dict[str, Any]], dates: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for ts_code, group in rows_by_code(rows).items():
        latest = group[-1]
        item: dict[str, Any] = {
            "ts_code": ts_code,
            "industry": latest.get("industry", ""),
            "latest_trade_date": str(latest.get("trade_date", "")),
            "company_num": int(number(latest.get("company_num")) or 0),
            "latest_close": rounded(number(latest.get("close"), None), 4),
            "latest_lead_stock": latest.get("lead_stock", ""),
        }
        for key, days in WINDOWS.items():
            selected = window_rows(group, dates, days)
            item[f"net_{key}_yi"] = rounded(sum(number(row.get("net_amount")) or 0.0 for row in selected), 4)
            item[f"gross_in_{key}_yi"] = rounded(sum(number(row.get("net_buy_amount")) or 0.0 for row in selected), 4)
            item[f"gross_out_{key}_yi"] = rounded(sum(number(row.get("net_sell_amount")) or 0.0 for row in selected), 4)
            item[f"positive_days_{key}"] = sum((number(row.get("net_amount")) or 0.0) > 0 for row in selected)
            item[f"return_{key}_pct"] = rounded(
                compounded_return(number(row.get("pct_change")) or 0.0 for row in selected), 4
            )
            item[f"days_covered_{key}"] = len({str(row.get("trade_date")) for row in selected})
        output.append(item)
    for key in WINDOWS:
        days = WINDOWS[key]
        eligible = [
            row for row in output
            if row["latest_trade_date"] == dates[-1] and row[f"days_covered_{key}"] == days
        ]
        apply_signed_ranks(eligible, f"net_{key}_yi", key)
    primary = max(WINDOWS, key=WINDOWS.get)
    output.sort(key=lambda row: (row.get(f"{primary}_inflow_rank", 10**9), row["ts_code"]))
    return output


def aggregate_stocks(
    rows: list[dict[str, Any]],
    dates: list[str],
    membership_by_stock: dict[str, list[dict[str, Any]]],
    daily_basic_by_code: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for ts_code, group in rows_by_code(rows).items():
        latest = group[-1]
        memberships = membership_by_stock.get(ts_code, [])
        basic = daily_basic_by_code.get(ts_code, {})
        total_mv_wan = number(basic.get("total_mv"), None)
        item: dict[str, Any] = {
            "ts_code": ts_code,
            "name": latest.get("name", ""),
            "industries": "；".join(sorted({str(row["industry"]) for row in memberships})),
            "industry_codes": "；".join(sorted({str(row["ts_code"]) for row in memberships})),
            "latest_trade_date": str(latest.get("trade_date", "")),
            "latest_price": rounded(number(latest.get("latest"), None), 4),
            "total_mv_yi": rounded(total_mv_wan / 10000.0, 4) if total_mv_wan is not None else None,
            "circ_mv_yi": rounded((number(basic.get("circ_mv")) or 0.0) / 10000.0, 4),
            "reported_net_d5_wan": rounded(number(latest.get("net_d5_amount"), None), 2),
        }
        for key, days in WINDOWS.items():
            selected = window_rows(group, dates, days)
            net = sum(number(row.get("net_amount")) or 0.0 for row in selected)
            item[f"net_{key}_wan"] = rounded(net, 2)
            item[f"large_net_{key}_wan"] = rounded(
                sum(number(row.get("buy_lg_amount")) or 0.0 for row in selected), 2
            )
            item[f"positive_days_{key}"] = sum((number(row.get("net_amount")) or 0.0) > 0 for row in selected)
            item[f"return_{key}_pct"] = rounded(
                compounded_return(number(row.get("pct_change")) or 0.0 for row in selected), 4
            )
            item[f"days_covered_{key}"] = len({str(row.get("trade_date")) for row in selected})
            item[f"flow_mv_ratio_{key}_pct"] = (
                rounded(100.0 * net / total_mv_wan, 6) if total_mv_wan not in (None, 0) else None
            )
        output.append(item)
    for key in WINDOWS:
        days = WINDOWS[key]
        eligible = [
            row for row in output
            if row["latest_trade_date"] == dates[-1] and row[f"days_covered_{key}"] == days
        ]
        apply_signed_ranks(eligible, f"net_{key}_wan", key)
    primary = max(WINDOWS, key=WINDOWS.get)
    output.sort(key=lambda row: (row.get(f"{primary}_inflow_rank", 10**9), row["ts_code"]))
    return output


def ranking_rows(
    rows: list[dict[str, Any]],
    value_suffix: str,
    name_field: str,
    top_n: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    latest_date = max((str(row.get("latest_trade_date", "")) for row in rows), default="")
    for key in WINDOWS:
        field = f"net_{key}_{value_suffix}"
        days = WINDOWS[key]
        eligible = [
            row for row in rows
            if row.get("latest_trade_date") and row["latest_trade_date"] == latest_date
            and row.get(f"days_covered_{key}") == days
        ]
        for direction, reverse in (("inflow", True), ("outflow", False)):
            ranked = sorted(
                eligible,
                key=lambda row: (
                    -(number(row.get(field)) or 0.0) if reverse else (number(row.get(field)) or 0.0),
                    str(row["ts_code"]),
                ),
            )[:top_n]
            for rank, row in enumerate(ranked, 1):
                result.append({
                    "period": key,
                    "period_label": WINDOW_LABELS[key],
                    "direction": direction,
                    "direction_label": "净流入" if direction == "inflow" else "净流出",
                    "rank": rank,
                    "ts_code": row["ts_code"],
                    "name": row[name_field],
                    "net_flow": row[field],
                    "net_flow_abs": rounded(abs(number(row[field]) or 0.0), 4),
                })
    return result


def assign_rps(rows: list[dict[str, Any]], return_field: str, rank_field: str, rps_field: str) -> None:
    eligible = [row for row in rows if number(row.get(return_field), None) is not None]
    eligible.sort(key=lambda row: (-(number(row[return_field]) or 0.0), str(row["ts_code"])))
    count = len(eligible)
    index = 0
    while index < count:
        value = number(eligible[index][return_field]) or 0.0
        end = index + 1
        while end < count and number(eligible[end][return_field]) == value:
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        displayed_rank: int | float = int(average_rank) if average_rank.is_integer() else average_rank
        percentile = 100.0 * (count - average_rank) / (count - 1) if count > 1 else 100.0
        for row in eligible[index:end]:
            row[rank_field] = displayed_rank
            row[rps_field] = rounded(percentile, 4)
        index = end


def build_stock_rps(
    daily_rows: list[dict[str, Any]],
    adj_rows: list[dict[str, Any]],
    dates: list[str],
    stock_basic_by_code: dict[str, dict[str, Any]],
    daily_basic_by_code: dict[str, dict[str, Any]],
    membership_by_stock: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    daily_lookup = {(str(row["trade_date"]), str(row["ts_code"])): row for row in daily_rows}
    adj_lookup = {(str(row["trade_date"]), str(row["ts_code"])): row for row in adj_rows}
    latest_codes = {str(row["ts_code"]) for row in daily_rows if str(row.get("trade_date")) == dates[-1]}
    output: list[dict[str, Any]] = []
    for ts_code in sorted(latest_codes):
        basic = stock_basic_by_code.get(ts_code, {})
        mv = daily_basic_by_code.get(ts_code, {})
        memberships = membership_by_stock.get(ts_code, [])
        item: dict[str, Any] = {
            "ts_code": ts_code,
            "name": basic.get("name", ""),
            "market": basic.get("market", ""),
            "exchange": basic.get("exchange", ""),
            "industries": "；".join(sorted({str(row["industry"]) for row in memberships})),
            "total_mv_yi": rounded((number(mv.get("total_mv")) or 0.0) / 10000.0, 4),
            "latest_amount_yi": rounded(
                (number(daily_lookup.get((dates[-1], ts_code), {}).get("amount")) or 0.0) / 100000.0, 4
            ),
        }
        for key, days in RPS_WINDOWS.items():
            base_date = dates[-(days + 1)]
            current_daily = daily_lookup.get((dates[-1], ts_code))
            base_daily = daily_lookup.get((base_date, ts_code))
            current_adj = adj_lookup.get((dates[-1], ts_code))
            base_adj = adj_lookup.get((base_date, ts_code))
            if current_daily and base_daily and current_adj and base_adj:
                current_price = (number(current_daily.get("close")) or 0.0) * (number(current_adj.get("adj_factor")) or 0.0)
                base_price = (number(base_daily.get("close")) or 0.0) * (number(base_adj.get("adj_factor")) or 0.0)
                raw_return = 100.0 * (current_price / base_price - 1.0) if base_price else None
                item[f"_return_{key}_raw"] = raw_return
                item[f"return_{key}_pct"] = rounded(raw_return, 4)
            else:
                item[f"_return_{key}_raw"] = None
                item[f"return_{key}_pct"] = None
        output.append(item)
    for key in RPS_WINDOWS:
        assign_rps(output, f"_return_{key}_raw", f"rank_{key}", f"rps_{key}")
    for row in output:
        for key in RPS_WINDOWS:
            row.pop(f"_return_{key}_raw", None)
    return output


def build_etfs(
    daily_rows: list[dict[str, Any]],
    share_rows: list[dict[str, Any]],
    fund_basic_by_code: dict[str, dict[str, Any]],
    dates: list[str],
) -> list[dict[str, Any]]:
    grouped = rows_by_code(daily_rows)
    share_lookup = {
        (str(row["trade_date"]), str(row["ts_code"])): number(row.get("fd_share"), None)
        for row in share_rows
    }
    output: list[dict[str, Any]] = []
    date_index = {date: index for index, date in enumerate(dates)}
    for ts_code, group in grouped.items():
        latest = group[-1]
        if str(latest.get("trade_date")) != dates[-1]:
            continue
        basic = fund_basic_by_code.get(ts_code, {})
        fund_name = str(basic.get("name") or "")
        # fund_daily 的实际返回范围包含少量 LOF 和场内封闭基金；用户要求 ETF，
        # 因此以公募基金列表中的正式简称明确含 ETF 作为最终产品池。
        if "ETF" not in fund_name.upper():
            continue
        by_date = {str(row["trade_date"]): row for row in group}
        item: dict[str, Any] = {
            "ts_code": ts_code,
            "name": fund_name,
            "fund_type": basic.get("fund_type", ""),
            "list_date": basic.get("list_date", ""),
            "latest_close": rounded(number(latest.get("close"), None), 4),
            "latest_amount_yi": rounded((number(latest.get("amount")) or 0.0) / 10000.0, 4),
        }
        for key, days in RPS_WINDOWS.items():
            selected = [by_date.get(date) for date in dates[-days:]]
            complete = [row for row in selected if row]
            raw_return = (
                compounded_return(number(row.get("pct_chg")) or 0.0 for row in complete)
                if len(complete) == days else None
            )
            item[f"_return_{key}_raw"] = raw_return
            item[f"return_{key}_pct"] = rounded(raw_return, 4)
        for key, days in WINDOWS.items():
            flow_wan = 0.0
            covered = 0
            for date in dates[-days:]:
                index = date_index[date]
                if index == 0:
                    continue
                previous = dates[index - 1]
                current_share = share_lookup.get((date, ts_code))
                previous_share = share_lookup.get((previous, ts_code))
                current_daily = by_date.get(date)
                close = number(current_daily.get("close"), None) if current_daily else None
                if current_share is None or previous_share is None or close is None:
                    continue
                flow_wan += (current_share - previous_share) * close
                covered += 1
            item[f"partial_estimated_flow_{key}_wan"] = rounded(flow_wan, 2) if covered else None
            item[f"estimated_flow_{key}_wan"] = rounded(flow_wan, 2) if covered == days else None
            item[f"share_days_covered_{key}"] = covered
        output.append(item)
    for key in RPS_WINDOWS:
        assign_rps(output, f"_return_{key}_raw", f"rank_{key}", f"rps_{key}")
    for row in output:
        for key in RPS_WINDOWS:
            row.pop(f"_return_{key}_raw", None)
        values = [number(row.get(f"rps_{key}"), None) for key in RPS_WINDOWS]
        present = [value for value in values if value is not None]
        row["strength_score"] = rounded(statistics.fmean(present), 4) if present else None
    for key in WINDOWS:
        eligible = [row for row in output if number(row.get(f"estimated_flow_{key}_wan"), None) is not None]
        apply_signed_ranks(eligible, f"estimated_flow_{key}_wan", f"flow_{key}")
    output.sort(key=lambda row: (-(number(row.get("strength_score")) or -math.inf), row["ts_code"]))
    return output


def market_turnover(daily_rows: list[dict[str, Any]], dates: list[str]) -> list[dict[str, Any]]:
    return [{
        "trade_date": date,
        "turnover_yi": rounded(
            sum(number(row.get("amount")) or 0.0 for row in daily_rows if str(row.get("trade_date")) == date)
            / 100000.0,
            2,
        ),
        "stock_count": sum(1 for row in daily_rows if str(row.get("trade_date")) == date),
    } for date in dates]


def token_leak_count(output_dir: Path, token: str) -> int:
    if not token:
        return 0
    count = 0
    for path in output_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".csv", ".json", ".html", ".md"}:
            try:
                count += path.read_text(encoding="utf-8", errors="ignore").count(token)
            except OSError:
                continue
    return count


def main() -> int:
    global WINDOWS, WINDOW_LABELS, RPS_WINDOWS, MARKET_CAP_FLOOR_YI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d"))
    parser.add_argument("--output-root", default=str(ROOT / "outputs"))
    parser.add_argument("--member-workers", type=int, default=6)
    parser.add_argument("--flow-days", default="1,5,10", help="资金流窗口，交易日数，逗号分隔")
    parser.add_argument("--rps-days", default="10,20", help="RPS窗口，交易日数，逗号分隔")
    parser.add_argument("--market-cap-floor", type=float, default=200.0, help="资金/市值比例榜最低总市值，亿元")
    args = parser.parse_args()
    if len(args.as_of) != 8 or not args.as_of.isdigit():
        raise ValueError("--as-of 必须为 YYYYMMDD")

    flow_days = parse_day_list(args.flow_days, "--flow-days")
    rps_days = parse_day_list(args.rps_days, "--rps-days")
    if args.market_cap_floor < 0:
        raise ValueError("--market-cap-floor 不能为负数")
    WINDOWS = {f"{days}d": days for days in flow_days}
    WINDOW_LABELS = {f"{days}d": flow_label(days) for days in flow_days}
    RPS_WINDOWS = {f"{days}d": days for days in rps_days}
    MARKET_CAP_FLOOR_YI = args.market_cap_floor
    primary_flow_key = f"{max(flow_days)}d"
    required_trade_days = max(max(flow_days) + 1, max(rps_days) + 1)

    load_dotenv(ROOT / ".env")
    token = os.environ.get("TUSHARE_TOKEN", "")
    client = TushareClient(token)
    generated_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    dates = fetch_open_dates(client, args.as_of, required_trade_days)
    flow_dates = dates[-max(flow_days):]
    latest_date = dates[-1]
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final_output_dir = output_root / latest_date
    output_dir = Path(tempfile.mkdtemp(prefix=f".{latest_date}.staging-", dir=output_root))
    raw_dir = output_dir / "raw"
    result_dir = output_dir / "results"
    print(f"观察窗口：{dates[0]} 至 {dates[-1]}；资金流窗口：{flow_dates[0]} 至 {flow_dates[-1]}", flush=True)

    industry_raw = fetch_by_date(client, "moneyflow_ind_ths", flow_dates)
    stock_flow_raw = fetch_by_date(client, "moneyflow_ths", flow_dates)
    classic_flow_raw = fetch_by_date(
        client, "moneyflow", flow_dates, "ts_code,trade_date,net_mf_vol,net_mf_amount"
    )
    daily_raw = fetch_by_date(client, "daily", dates, "ts_code,trade_date,close,pct_chg,amount")
    adj_raw = fetch_by_date(client, "adj_factor", dates, "ts_code,trade_date,adj_factor")
    daily_basic = client.call(
        "daily_basic", {"trade_date": latest_date}, "ts_code,trade_date,total_mv,circ_mv"
    )
    assert_api_batch("daily_basic", daily_basic, latest_date)
    stock_basic = client.call(
        "stock_basic",
        {"exchange": "", "list_status": "L"},
        "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date",
    )
    assert_api_batch("stock_basic", stock_basic, "list_status=L")
    ths_industry_index = client.call("ths_index", {"exchange": "A", "type": "I"})
    assert_api_batch("ths_index", ths_industry_index, "exchange=A,type=I")
    fund_basic = client.call(
        "fund_basic", {"market": "E", "status": "L"}, "ts_code,name,fund_type,list_date,market"
    )
    assert_api_batch("fund_basic", fund_basic, "market=E,status=L")
    fund_daily = fetch_by_date(
        client, "fund_daily", dates, "ts_code,trade_date,close,pre_close,pct_chg,vol,amount"
    )
    fund_share = fetch_by_date(client, "fund_share", dates, "ts_code,trade_date,fd_share")

    for path, rows in (
        (raw_dir / "industry_flow_ths.csv", industry_raw),
        (raw_dir / "stock_flow_ths.csv", stock_flow_raw),
        (raw_dir / "stock_flow_moneyflow.csv", classic_flow_raw),
        (raw_dir / "daily.csv", daily_raw),
        (raw_dir / "adj_factor.csv", adj_raw),
        (raw_dir / "daily_basic.csv", daily_basic),
        (raw_dir / "stock_basic.csv", stock_basic),
        (raw_dir / "ths_industry_index.csv", ths_industry_index),
        (raw_dir / "fund_basic.csv", fund_basic),
        (raw_dir / "fund_daily.csv", fund_daily),
        (raw_dir / "fund_share.csv", fund_share),
    ):
        write_csv(path, rows)

    industries = aggregate_industries(industry_raw, flow_dates)
    members_raw, members_by_industry = fetch_members(client, industries, args.member_workers)
    write_csv(raw_dir / "industry_members.csv", members_raw)
    membership_by_stock: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in members_raw:
        membership_by_stock[str(row.get("con_code"))].append(row)
    daily_basic_by_code = {str(row["ts_code"]): row for row in daily_basic}
    stock_basic_by_code = {str(row["ts_code"]): row for row in stock_basic}
    fund_basic_by_code = {str(row["ts_code"]): row for row in fund_basic}

    stocks = aggregate_stocks(stock_flow_raw, flow_dates, membership_by_stock, daily_basic_by_code)
    stock_rps = build_stock_rps(
        daily_raw, adj_raw, dates, stock_basic_by_code, daily_basic_by_code, membership_by_stock
    )
    etfs = build_etfs(fund_daily, fund_share, fund_basic_by_code, dates)
    industry_rankings = ranking_rows(industries, "yi", "industry", 10)
    stock_rankings = ranking_rows(stocks, "wan", "name", 20)
    largecap = [
        row for row in stocks
        if (number(row.get("total_mv_yi")) or 0.0) >= MARKET_CAP_FLOOR_YI
        and row.get("latest_trade_date") == latest_date
        and row.get(f"days_covered_{primary_flow_key}") == WINDOWS[primary_flow_key]
    ]
    for key in WINDOWS:
        eligible = [row for row in largecap if number(row.get(f"flow_mv_ratio_{key}_pct"), None) is not None]
        apply_signed_ranks(eligible, f"flow_mv_ratio_{key}_pct", f"ratio_{key}")
    largecap.sort(key=lambda row: (row.get(f"ratio_{primary_flow_key}_inflow_rank", 10**9), row["ts_code"]))

    top_rps: dict[str, list[dict[str, Any]]] = {}
    for key in RPS_WINDOWS:
        top_rps[key] = sorted(
            [row for row in stock_rps if number(row.get(f"rps_{key}"), -1.0) >= 95.0],
            key=lambda row: (row.get(f"rank_{key}", 10**9), row["ts_code"]),
        )
    etf_top15 = etfs[:15]
    turnover = market_turnover(daily_raw, dates)
    top_dynamic_names = {row["industry"] for row in industries[:5]}
    industry_daily = sorted(
        [{
            "trade_date": str(row["trade_date"]),
            "industry": row["industry"],
            "net_amount_yi": rounded(number(row.get("net_amount")) or 0.0, 4),
            "return_pct": rounded(number(row.get("pct_change")) or 0.0, 4),
        } for row in industry_raw if row.get("industry") in top_dynamic_names],
        key=lambda row: (row["trade_date"], row["industry"]),
    )

    write_csv(result_dir / "industry_window_summary.csv", industries)
    write_csv(result_dir / "industry_flow_rankings.csv", industry_rankings)
    write_csv(result_dir / "industry_daily_top5.csv", industry_daily)
    write_csv(result_dir / "stock_window_summary.csv", stocks)
    write_csv(result_dir / "stock_flow_rankings.csv", stock_rankings)
    write_csv(result_dir / "stock_largecap_flow_ratio.csv", largecap)
    write_csv(result_dir / "stock_rps_all.csv", stock_rps)
    for key, rows in top_rps.items():
        write_csv(result_dir / f"stock_rps_{key}_top5pct.csv", rows)
    write_csv(result_dir / "etf_summary.csv", etfs)
    write_csv(result_dir / "etf_top15.csv", etf_top15)
    write_csv(result_dir / "market_daily_turnover.csv", turnover)

    # 关键数据质量与独立口径验证。
    checks: list[dict[str, Any]] = []
    duplicates = {
        "industry": key_duplicate_count(industry_raw, ("trade_date", "ts_code")),
        "stock_flow": key_duplicate_count(stock_flow_raw, ("trade_date", "ts_code")),
        "classic": key_duplicate_count(classic_flow_raw, ("trade_date", "ts_code")),
        "daily": key_duplicate_count(daily_raw, ("trade_date", "ts_code")),
        "adj": key_duplicate_count(adj_raw, ("trade_date", "ts_code")),
        "fund_daily": key_duplicate_count(fund_daily, ("trade_date", "ts_code")),
        "fund_share": key_duplicate_count(fund_share, ("trade_date", "ts_code")),
    }
    required_missing = missing_required_count(
        industry_raw, ("trade_date", "ts_code", "industry", "net_amount")
    ) + missing_required_count(stock_flow_raw, ("trade_date", "ts_code", "name", "net_amount"))
    order_compared = 0
    order_passed = 0
    for row in stock_flow_raw:
        net = number(row.get("net_amount"), None)
        parts = [number(row.get(field), None) for field in ("buy_lg_amount", "buy_md_amount", "buy_sm_amount")]
        if net is None or any(value is None for value in parts):
            continue
        order_compared += 1
        tolerance = max(5.0, abs(net) * 0.002)
        order_passed += abs(net - sum(value or 0.0 for value in parts)) <= tolerance
    order_rate = order_passed / order_compared if order_compared else 0.0

    classic_group: dict[str, float] = defaultdict(float)
    for row in classic_flow_raw:
        classic_group[str(row["ts_code"])] += number(row.get("net_mf_amount")) or 0.0
    ths_by_code = {row["ts_code"]: row for row in stocks}
    common = sorted(
        code for code in set(ths_by_code) & set(classic_group)
        if ths_by_code[code].get("latest_trade_date") == latest_date
        and ths_by_code[code].get(f"days_covered_{primary_flow_key}") == WINDOWS[primary_flow_key]
    )
    ths_values = [number(ths_by_code[code].get(f"net_{primary_flow_key}_wan")) or 0.0 for code in common]
    classic_values = [classic_group[code] for code in common]
    comparable = [(left, right) for left, right in zip(ths_values, classic_values) if abs(left) >= 10 or abs(right) >= 10]
    classic_sign_rate = (
        sum(left * right > 0 or (left == 0 and right == 0) for left, right in comparable) / len(comparable)
        if comparable else 0.0
    )
    classic_corr = pearson(ths_values, classic_values)

    rolling_compared = 0
    rolling_passed = 0
    if 5 in flow_days:
        for row in stocks:
            calculated = number(row.get("large_net_5d_wan"), None)
            reported = number(row.get("reported_net_d5_wan"), None)
            if calculated is None or reported is None or row.get("days_covered_5d") != 5:
                continue
            rolling_compared += 1
            rolling_passed += abs(calculated - reported) <= max(10.0, abs(calculated) * 0.005)
    rolling_rate = rolling_passed / rolling_compared if rolling_compared else None
    rolling_status = (
        "SKIP" if 5 not in flow_days
        else ("PASS" if rolling_compared > 0 and (rolling_rate or 0.0) >= 0.95 else "FAIL")
    )

    largecap_ratio_errors = 0
    for row in largecap:
        source = daily_basic_by_code.get(str(row["ts_code"]), {})
        mv = number(source.get("total_mv"), None)
        if mv is None or mv < MARKET_CAP_FLOOR_YI * 10000:
            largecap_ratio_errors += 1
            continue
        expected = 100.0 * (number(row.get(f"net_{primary_flow_key}_wan")) or 0.0) / mv
        if abs(expected - (number(row.get(f"flow_mv_ratio_{primary_flow_key}_pct")) or 0.0)) > 0.000002:
            largecap_ratio_errors += 1

    rps_eligible = {
        key: [row for row in stock_rps if number(row.get(f"rps_{key}"), None) is not None]
        for key in RPS_WINDOWS
    }
    rps_shares = {
        key: len(top_rps[key]) / len(rps_eligible[key]) if rps_eligible[key] else 0.0
        for key in RPS_WINDOWS
    }
    rps_boundary_errors = sum(
        int(any((number(row.get(f"rps_{key}"), -1.0) or -1.0) < 95.0 for row in top_rps[key]))
        for key in RPS_WINDOWS
    )
    fund_daily_latest_all = {str(row["ts_code"]) for row in fund_daily if str(row.get("trade_date")) == latest_date}
    fund_daily_latest = {str(row["ts_code"]) for row in etfs}
    share_latest = {str(row["ts_code"]) for row in fund_share if str(row.get("trade_date")) == latest_date}
    share_coverage = len(fund_daily_latest & share_latest) / len(fund_daily_latest) if fund_daily_latest else 0.0
    fund_name_coverage = sum(code in fund_basic_by_code for code in fund_daily_latest) / len(fund_daily_latest) if fund_daily_latest else 0.0
    daily_latest_codes = {str(row["ts_code"]) for row in daily_raw if str(row.get("trade_date")) == latest_date}
    stock_flow_latest_codes = {
        str(row["ts_code"]) for row in stock_flow_raw if str(row.get("trade_date")) == latest_date
    }
    classic_latest_codes = {
        str(row["ts_code"]) for row in classic_flow_raw if str(row.get("trade_date")) == latest_date
    }
    market_cap_coverage = (
        len(stock_flow_latest_codes & set(daily_basic_by_code)) / len(stock_flow_latest_codes)
        if stock_flow_latest_codes else 0.0
    )
    exchange_by_code = {str(row["ts_code"]): str(row.get("exchange") or "UNKNOWN") for row in stock_basic}
    exchange_coverage: dict[str, dict[str, Any]] = {}
    for exchange in ("SSE", "SZSE", "BSE"):
        expected = {code for code in daily_latest_codes if exchange_by_code.get(code) == exchange}
        ths_matched = expected & stock_flow_latest_codes
        classic_matched = expected & classic_latest_codes
        exchange_coverage[exchange] = {
            "daily": len(expected),
            "moneyflow_ths": len(ths_matched),
            "moneyflow_ths_rate": rounded(len(ths_matched) / len(expected), 6) if expected else None,
            "moneyflow": len(classic_matched),
            "moneyflow_rate": rounded(len(classic_matched) / len(expected), 6) if expected else None,
        }
    mainland_expected = exchange_coverage["SSE"]["daily"] + exchange_coverage["SZSE"]["daily"]
    mainland_matched = exchange_coverage["SSE"]["moneyflow_ths"] + exchange_coverage["SZSE"]["moneyflow_ths"]
    mainland_ths_coverage = mainland_matched / mainland_expected if mainland_expected else 0.0
    bse_standard_coverage = number(exchange_coverage["BSE"].get("moneyflow_rate"), 0.0) or 0.0

    api_rows = {
        "moneyflow_ind_ths": industry_raw,
        "moneyflow_ths": stock_flow_raw,
        "moneyflow": classic_flow_raw,
        "daily": daily_raw,
        "adj_factor": adj_raw,
        "fund_daily": fund_daily,
        "fund_share": fund_share,
    }
    api_partition_metrics: dict[str, dict[str, Any]] = {}
    for api_name, rows in api_rows.items():
        counts = partition_counts(rows)
        limit = API_ROW_LIMITS[api_name]
        max_rows = max(counts.values(), default=0)
        api_partition_metrics[api_name] = {
            "limit": limit,
            "max_rows": max_rows,
            "max_utilization_pct": rounded(100.0 * max_rows / limit, 4),
            "partition_count": len(counts),
        }
    missing_partitions = {
        api_name: sorted(set(flow_dates if api_name in {"moneyflow_ind_ths", "moneyflow_ths", "moneyflow"} else dates) - set(partition_counts(rows)))
        for api_name, rows in api_rows.items()
    }
    api_guard_errors = sum(len(value) for value in missing_partitions.values()) + sum(
        metric["max_rows"] >= metric["limit"] for metric in api_partition_metrics.values()
    )
    member_duplicates = key_duplicate_count(members_raw, ("ts_code", "con_code"))
    multi_membership_count = sum(len({str(row.get("ts_code")) for row in rows}) > 1 for rows in membership_by_stock.values())

    checks.extend([
        make_check("trade_dates", "RPS观察窗交易日完整", "PASS" if len(dates) == required_trade_days else "FAIL", "critical", len(dates), f"= {required_trade_days}", f"最长窗口需要{required_trade_days}个收盘端点"),
        make_check("flow_dates", "资金流观察窗交易日完整", "PASS" if len(flow_dates) == max(flow_days) else "FAIL", "critical", len(flow_dates), f"= {max(flow_days)}", f"资金流窗口={flow_days}"),
        make_check("unique_keys", "全部原始事实表主键唯一", "PASS" if sum(duplicates.values()) == 0 else "FAIL", "critical", sum(duplicates.values()), "= 0", str(duplicates)),
        make_check("required_fields", "行业与个股资金流关键字段完整", "PASS" if required_missing == 0 else "FAIL", "high", required_missing, "= 0", "日期、代码、名称、净额"),
        make_check("api_partition_guard", "逐接口分区非空且未触及各自上限", "PASS" if api_guard_errors == 0 else "FAIL", "critical", api_guard_errors, "= 0", json.dumps(api_partition_metrics, ensure_ascii=False)),
        make_check("order_arithmetic", "个股净额等于大/中/小单净额和", "PASS" if order_rate >= 0.99 else "WARN", "high", rounded(100 * order_rate, 4), ">= 99%", f"可比记录 {order_compared} 条"),
        make_check("rolling_5d", "五日大单和与THS五日主力净额一致", rolling_status, "high", rounded(100 * rolling_rate, 4) if rolling_rate is not None else "N/A", ">= 95%", f"可比股票 {rolling_compared} 只；未配置5日窗口时明确SKIP"),
        make_check("classic_crosscheck", f"THS与标准moneyflow的{max(flow_days)}日方向一致", "PASS" if classic_sign_rate >= 0.7 else "WARN", "medium", rounded(100 * classic_sign_rate, 4), ">= 70%", f"相关系数 {rounded(classic_corr, 4)}；算法不同，仅作独立复核"),
        make_check("ths_mainland_coverage", "moneyflow_ths覆盖沪深最新交易股票", "PASS" if mainland_ths_coverage >= 0.99 else "FAIL", "high", rounded(100 * mainland_ths_coverage, 4), ">= 99%", "北交所不属于THS个股资金流榜，按交易所单独披露"),
        make_check("bse_standard_coverage", "标准moneyflow覆盖北交所用于独立复核", "PASS" if bse_standard_coverage >= 0.95 else "WARN", "medium", rounded(100 * bse_standard_coverage, 4), ">= 95%", "不与moneyflow_ths榜单混排"),
        make_check("market_cap_coverage", "最新日总市值覆盖沪深THS个股池", "PASS" if market_cap_coverage >= 0.99 else "WARN", "high", rounded(100 * market_cap_coverage, 4), ">= 99%", "daily_basic 对最新日moneyflow_ths代码"),
        make_check("largecap_ratio", f"{MARKET_CAP_FLOOR_YI:g}亿门槛与资金市值比例公式正确", "PASS" if largecap_ratio_errors == 0 else "FAIL", "critical", largecap_ratio_errors, "= 0", "SUM(net_amount)/latest total_mv，分子分母均为万元"),
        make_check("industry_membership_keys", "行业成员关系主键唯一", "PASS" if member_duplicates == 0 else "FAIL", "high", member_duplicates, "= 0", f"{multi_membership_count}只股票属于多个行业；行业不可跨板块求和"),
        make_check("etf_names", "ETF日线与基金名称映射完整", "PASS" if fund_name_coverage >= 0.98 else "WARN", "medium", rounded(100 * fund_name_coverage, 4), ">= 98%", "fund_daily 对 fund_basic(market=E,status=L)"),
        make_check("etf_share_coverage", "ETF份额数据覆盖可用", "PASS" if share_coverage >= 0.75 else "WARN", "medium", rounded(100 * share_coverage, 4), ">= 75%", "未覆盖ETF仍展示RPS，但估算资金流留空"),
    ])
    for key, days in RPS_WINDOWS.items():
        checks.append(make_check(
            f"rps_{key}_population", f"{days}日RPS全市场样本充足",
            "PASS" if len(rps_eligible[key]) >= 4000 else "WARN", "high",
            len(rps_eligible[key]), ">= 4000", f"需最新日及{days}日前复权收盘均存在",
        ))
    checks.append(make_check(
        "rps_top5_share", "RPS>=95筛选边界正确",
        "PASS" if rps_boundary_errors == 0 else "FAIL",
        "high", "; ".join(f"{RPS_WINDOWS[key]}日={100*share:.3f}%" for key, share in rps_shares.items()),
        "全部入选项RPS>=95", "使用未舍入收益与平均秩；边界并列可能使数量不严格等于5%",
    ))
    write_csv(result_dir / "validation_checks.csv", checks)

    hard_failures = [row for row in checks if row["status"] == "FAIL" and row["severity"] in {"critical", "high"}]
    warnings = [row for row in checks if row["status"] == "WARN"]
    quality_status = "PASS" if not hard_failures and not warnings else ("PASS_WITH_WARNINGS" if not hard_failures else "FAIL")
    primary_industries = [row for row in industries if row.get(f"{primary_flow_key}_inflow_rank") is not None]
    primary_stocks = [row for row in stocks if row.get(f"{primary_flow_key}_inflow_rank") is not None]
    if not primary_industries or not primary_stocks:
        raise RuntimeError("最长资金窗口没有完整行业或沪深个股样本，拒绝生成榜单")

    summary = {
        "generated_at": generated_at,
        "as_of_requested": args.as_of,
        "latest_trade_date": latest_date,
        "trade_dates": dates,
        "flow_dates": flow_dates,
        "flow_windows": [{"key": key, "days": days, "label": WINDOW_LABELS[key]} for key, days in WINDOWS.items()],
        "rps_windows": [{"key": key, "days": days, "label": f"{days}日"} for key, days in RPS_WINDOWS.items()],
        "primary_flow_key": primary_flow_key,
        "window_definitions": {
            **{key: f"最近{days}个交易日（{WINDOW_LABELS[key]}）" for key, days in WINDOWS.items()},
            **{f"rps_{key}": f"最近{days}个交易日RPS" for key, days in RPS_WINDOWS.items()},
        },
        "classification": "同花顺行业模板（moneyflow_ind_ths / ths_index type=I / ths_member）",
        "stock_flow_universe": "沪深上市股票；moneyflow_ths不覆盖北交所，北交所标准moneyflow只作独立复核且不混排",
        "rps_universe": "最新交易日全A日线可比股票，包含北交所",
        "exchange_coverage": exchange_coverage,
        "industry_membership_definition": "ths_member当前快照，允许一股多行业，不可跨行业加总；历史as-of不代表历史成分",
        "industry_membership_snapshot_at": generated_at,
        "market_cap_floor_yi": MARKET_CAP_FLOOR_YI,
        "market_cap_ratio_definition": f"市值>={MARKET_CAP_FLOOR_YI:g}亿元；窗口累计net_amount/最新交易日total_mv",
        "rps_definition": "区间前复权收益在全A可比股票中的百分位，最高100；展示RPS>=95",
        "etf_flow_definition": "SUM((当日份额-前一交易日份额)*当日收盘价)，单位万元，属于申赎资金估算",
        "quality_status": quality_status,
        "hard_failure_count": len(hard_failures),
        "warning_count": len(warnings),
        "top_industry_primary_inflow": min(primary_industries, key=lambda row: row[f"{primary_flow_key}_inflow_rank"]),
        "top_industry_primary_outflow": min(primary_industries, key=lambda row: row[f"{primary_flow_key}_outflow_rank"]),
        "top_stock_primary_inflow": min(primary_stocks, key=lambda row: row[f"{primary_flow_key}_inflow_rank"]),
        "top_stock_primary_outflow": min(primary_stocks, key=lambda row: row[f"{primary_flow_key}_outflow_rank"]),
        "rps_top5_counts": {key: len(rows) for key, rows in top_rps.items()},
        "etf_top15": etf_top15,
        "validation": {
            "checks": checks,
            "order_arithmetic_rate": rounded(order_rate, 6),
            "rolling_5d_rate": rounded(rolling_rate, 6),
            "api_partition_metrics": api_partition_metrics,
            "exchange_coverage": exchange_coverage,
            "industry_multi_membership_count": multi_membership_count,
            "classic_sign_rate": rounded(classic_sign_rate, 6),
            "classic_correlation": rounded(classic_corr, 6),
            "market_cap_coverage": rounded(market_cap_coverage, 6),
            "rps_eligible": {key: len(rows) for key, rows in rps_eligible.items()},
            "etf_daily_count": len(fund_daily_latest),
            "etf_share_coverage": rounded(share_coverage, 6),
        },
        "source_documents": DOCS,
    }
    write_json(result_dir / "summary.json", summary)
    leak_count = token_leak_count(output_dir, token)
    if leak_count:
        raise RuntimeError(f"安全检查失败：输出中检测到 {leak_count} 处 token 泄露")
    metadata = {
        "generated_at": generated_at,
        "latest_trade_date": latest_date,
        "git_sha": current_git_sha(),
        "git_dirty": git_is_dirty(),
        "code_sha256": {
            path.name: file_sha256(path)
            for path in (
                ROOT / "scripts/market_flow_dashboard.py",
                ROOT / "scripts/ths_five_day_flow.py",
                ROOT / "scripts/validate_market_dashboard.py",
                ROOT / "scripts/render_dashboard.py",
            )
        },
        "raw_files": sorted(path.name for path in raw_dir.glob("*.csv")),
        "result_files": sorted(path.name for path in result_dir.glob("*")),
        "raw_sha256": {path.name: file_sha256(path) for path in sorted(raw_dir.glob("*.csv"))},
        "result_sha256": {path.name: file_sha256(path) for path in sorted(result_dir.glob("*")) if path.is_file()},
        "token_leak_count": leak_count,
        "quality_status": quality_status,
    }
    write_json(output_dir / "metadata.json", metadata)
    print(f"质量状态：{quality_status}；关键失败 {len(hard_failures)}；警告 {len(warnings)}", flush=True)
    if hard_failures:
        print(f"失败快照保留在暂存目录：{output_dir.resolve()}", flush=True)
        return 2
    backup = publish_snapshot(output_dir, final_output_dir, generated_at)
    if backup:
        print(f"上一版快照已移至可恢复备份：{backup.resolve()}", flush=True)
    print(final_output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
