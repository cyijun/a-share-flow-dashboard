#!/usr/bin/env python3
"""把统一市场快照打包为 Data Analytics 标准 HTML 仪表盘 artifact.json。"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ("1d", "5d", "10d")
WINDOW_LABELS = {"1d": "当天", "5d": "一周", "10d": "两周"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value)) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def source(
    source_id: str,
    label: str,
    path: str,
    doc_url: str,
    description: str,
    generated_at: str,
    tables: list[str],
    filters: list[str],
    definitions: dict[str, str],
) -> dict[str, Any]:
    return {
        "id": source_id,
        "label": label,
        "path": path,
        "url": doc_url,
        "query": {
            "engine": "DuckDB over persisted Tushare API snapshots",
            "language": "sql",
            "description": description,
            "executed_at": generated_at,
            "tables_used": tables,
            "filters": filters,
            "metric_definitions": definitions,
            "sql": f"SELECT * FROM read_csv_auto('{path}')",
        },
    }


def ranking_chart(
    chart_id: str,
    title: str,
    subtitle: str,
    dataset: str,
    source_id: str,
    value_label: str,
    *,
    layout: str = "half",
) -> dict[str, Any]:
    return {
        "id": chart_id,
        "title": title,
        "subtitle": subtitle,
        "type": "bar",
        "dataset": dataset,
        "sourceId": source_id,
        "encodings": {
            "x": {"field": "name", "type": "nominal", "label": "名称"},
            "y": {"field": "value", "type": "quantitative", "label": value_label},
            "tooltip": [
                {"field": "rank", "type": "quantitative", "label": "排名"},
                {"field": "ts_code", "type": "nominal", "label": "代码"},
                {"field": "value", "type": "quantitative", "label": value_label},
            ],
        },
        "valueFormat": "number",
        "layout": layout,
    }


def ranking_block(chart_id: str) -> dict[str, Any]:
    return {"id": f"{chart_id}_block", "type": "chart", "chartId": chart_id, "layout": "half"}


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("用法: python3 scripts/build_market_dashboard.py outputs/YYYYMMDD")
    output_dir = (ROOT / sys.argv[1]).resolve() if not Path(sys.argv[1]).is_absolute() else Path(sys.argv[1])
    result_dir = output_dir / "results"
    summary = json.loads((result_dir / "summary.json").read_text(encoding="utf-8"))
    industry_summary = read_csv(result_dir / "industry_window_summary.csv")
    industry_rankings = read_csv(result_dir / "industry_flow_rankings.csv")
    industry_daily = read_csv(result_dir / "industry_daily_top5.csv")
    stock_summary = read_csv(result_dir / "stock_window_summary.csv")
    stock_rankings = read_csv(result_dir / "stock_flow_rankings.csv")
    largecap = read_csv(result_dir / "stock_largecap_flow_ratio.csv")
    rps10 = read_csv(result_dir / "stock_rps_10d_top5pct.csv")
    rps20 = read_csv(result_dir / "stock_rps_20d_top5pct.csv")
    etfs = read_csv(result_dir / "etf_summary.csv")
    etf_top15 = read_csv(result_dir / "etf_top15.csv")
    turnover = read_csv(result_dir / "market_daily_turnover.csv")
    checks = read_csv(result_dir / "validation_checks.csv")

    generated_at = summary["generated_at"]
    latest = summary["latest_trade_date"]
    latest_label = f"{latest[:4]}-{latest[4:6]}-{latest[6:]}"
    flow_dates = summary["flow_dates_10"]
    rps_dates = summary["trade_dates_21"]
    flow_range = f"{flow_dates[0][:4]}-{flow_dates[0][4:6]}-{flow_dates[0][6:]} 至 {latest_label}"
    rps_range = f"{rps_dates[0][:4]}-{rps_dates[0][4:6]}-{rps_dates[0][6:]} 至 {latest_label}"

    output_rel = output_dir.relative_to(ROOT).as_posix()
    docs = summary["source_documents"]
    sources = [
        source(
            "industry_flow",
            "同花顺行业资金流",
            f"{output_rel}/results/industry_window_summary.csv",
            docs["moneyflow_ind_ths"],
            "按同花顺90个资金流行业计算1、5、10个交易日净流入与净流出排名。",
            generated_at,
            ["Tushare.moneyflow_ind_ths", "Tushare.ths_index", "Tushare.ths_member"],
            [f"trade_date in {flow_dates}"],
            {
                "行业窗口净额": "SUM(net_amount)，接口单位亿元",
                "净流出排名": "按窗口净额从小到大排名，保留负号",
            },
        ),
        source(
            "stock_flow",
            "同花顺个股资金流与最新总市值",
            f"{output_rel}/results/stock_window_summary.csv",
            docs["moneyflow_ths"],
            "个股1、5、10日净额与最新交易日总市值合并。",
            generated_at,
            ["Tushare.moneyflow_ths", "Tushare.daily_basic", "Tushare.ths_member"],
            [f"trade_date in {flow_dates}", "总市值取最新交易日"],
            {
                "个股窗口净额": "SUM(net_amount)，原始单位万元，页面换算亿元",
                "资金流入比例": "窗口累计净额/最新交易日总市值；仅对总市值>=200亿元股票排名",
            },
        ),
        source(
            "stock_rps",
            "A股前复权行情与RPS",
            f"{output_rel}/results/stock_rps_all.csv",
            docs["daily"],
            "用daily收盘价和adj_factor计算10/20交易日收益，再做全市场百分位排名。",
            generated_at,
            ["Tushare.daily", "Tushare.adj_factor", "Tushare.stock_basic"],
            [f"trade_date in {rps_dates}", "要求最新日和窗口起点均有行情"],
            {"RPS": "100*(可比样本数-收益排名)/(可比样本数-1)，最高100；前5%为RPS>=95"},
        ),
        source(
            "etf",
            "ETF行情、份额与估算申赎资金",
            f"{output_rel}/results/etf_summary.csv",
            docs["fund_daily"],
            "ETF强度由10/20日RPS衡量，资金流由每日份额变化乘收盘价估算。",
            generated_at,
            ["Tushare.fund_daily", "Tushare.fund_share", "Tushare.fund_basic"],
            [f"trade_date in {rps_dates}", "份额缺失时资金流留空，不插值"],
            {
                "ETF强度分": "10日与20日RPS的可用值均值",
                "ETF估算资金流": "SUM((当日fd_share-前一交易日fd_share)*当日close)，单位万元",
            },
        ),
        source(
            "turnover",
            "A股日行情成交额",
            f"{output_rel}/results/market_daily_turnover.csv",
            docs["daily"],
            "逐日汇总全A股票成交额。",
            generated_at,
            ["Tushare.daily"],
            [f"trade_date in {rps_dates}"],
            {"全A成交额": "SUM(amount)/100000，daily.amount单位千元，结果单位亿元"},
        ),
        source(
            "validation",
            "数据质量验证结果",
            f"{output_rel}/results/validation_checks.csv",
            docs["moneyflow"],
            "验证交易日、主键、接口上限、资金算术、独立资金流、市值、RPS和ETF覆盖。",
            generated_at,
            [
                "Tushare.moneyflow_ths",
                "Tushare.moneyflow",
                "Tushare.daily",
                "Tushare.daily_basic",
                "Tushare.fund_daily",
                "Tushare.fund_share",
            ],
            [f"latest_trade_date={latest}"],
            {"可分享标准": "无critical/high级FAIL；WARN在页面保留解释"},
        ),
    ]

    top_ind_in = summary["top_industry_10d_inflow"]
    top_ind_out = summary["top_industry_10d_outflow"]
    top_stock_in = summary["top_stock_10d_inflow"]
    top_stock_out = summary["top_stock_10d_outflow"]
    latest_turnover = turnover[-1]
    pass_count = sum(row["status"] == "PASS" for row in checks)
    headline = [{
        "industry_in_yi": num(top_ind_in["net_10d_yi"]),
        "industry_out_yi": num(top_ind_out["net_10d_yi"]),
        "stock_in_yi": num(top_stock_in["net_10d_wan"]) / 10000.0,
        "stock_out_yi": num(top_stock_out["net_10d_wan"]) / 10000.0,
        "latest_turnover_yi": num(latest_turnover["turnover_yi"]),
        "rps10_top5_count": integer(summary["rps10_top5_count"]),
        "etf_count": integer(summary["validation"]["etf_daily_count"]),
        "validation_pass_rate": pass_count / len(checks) if checks else 0.0,
    }]

    industry_datasets: dict[str, list[dict[str, Any]]] = {}
    stock_datasets: dict[str, list[dict[str, Any]]] = {}
    for period in WINDOWS:
        for direction in ("inflow", "outflow"):
            key = f"industry_{period}_{direction}"
            rows = [row for row in industry_rankings if row["period"] == period and row["direction"] == direction]
            industry_datasets[key] = [{
                "rank": integer(row["rank"]),
                "name": row["name"],
                "ts_code": row["ts_code"],
                "value": num(row["net_flow"]),
            } for row in rows]
            key = f"stock_{period}_{direction}"
            rows = [row for row in stock_rankings if row["period"] == period and row["direction"] == direction][:10]
            stock_datasets[key] = [{
                "rank": integer(row["rank"]),
                "name": row["name"],
                "ts_code": row["ts_code"],
                "value": num(row["net_flow"]) / 10000.0,
            } for row in rows]

    industry_detail = [{
        "industry": row["industry"],
        "ts_code": row["ts_code"],
        "net_1d_yi": num(row["net_1d_yi"]),
        "net_5d_yi": num(row["net_5d_yi"]),
        "net_10d_yi": num(row["net_10d_yi"]),
        "positive_days_10d": integer(row["positive_days_10d"]),
        "return_10d_pct": num(row["return_10d_pct"]),
        "company_num": integer(row["company_num"]),
        "latest_lead_stock": row["latest_lead_stock"],
    } for row in industry_summary]

    selected_stock_codes: set[str] = set()
    for period in WINDOWS:
        selected_stock_codes.update(
            row["ts_code"] for row in stock_rankings if row["period"] == period and integer(row["rank"]) <= 20
        )
    stock_detail = [{
        "name": row["name"],
        "ts_code": row["ts_code"],
        "industry": row["industries"],
        "total_mv_yi": num(row["total_mv_yi"]),
        "net_1d_yi": num(row["net_1d_wan"]) / 10000.0,
        "net_5d_yi": num(row["net_5d_wan"]) / 10000.0,
        "net_10d_yi": num(row["net_10d_wan"]) / 10000.0,
        "ratio_1d_pct": num(row["flow_mv_ratio_1d_pct"]),
        "ratio_5d_pct": num(row["flow_mv_ratio_5d_pct"]),
        "ratio_10d_pct": num(row["flow_mv_ratio_10d_pct"]),
    } for row in stock_summary if row["ts_code"] in selected_stock_codes]
    stock_detail.sort(key=lambda row: -abs(row["net_10d_yi"]))

    largecap_detail = [{
        "name": row["name"],
        "ts_code": row["ts_code"],
        "industry": row["industries"],
        "total_mv_yi": num(row["total_mv_yi"]),
        "net_1d_yi": num(row["net_1d_wan"]) / 10000.0,
        "net_5d_yi": num(row["net_5d_wan"]) / 10000.0,
        "net_10d_yi": num(row["net_10d_wan"]) / 10000.0,
        "ratio_1d_pct": num(row["flow_mv_ratio_1d_pct"]),
        "ratio_5d_pct": num(row["flow_mv_ratio_5d_pct"]),
        "ratio_10d_pct": num(row["flow_mv_ratio_10d_pct"]),
    } for row in largecap]
    largecap_in = sorted(largecap_detail, key=lambda row: (-row["ratio_10d_pct"], row["ts_code"]))[:10]
    largecap_out = sorted(largecap_detail, key=lambda row: (row["ratio_10d_pct"], row["ts_code"]))[:10]

    def chart_rows(rows: list[dict[str, Any]], field: str, rank_field: str = "") -> list[dict[str, Any]]:
        return [{
            "rank": integer(row.get(rank_field), index) if rank_field else index,
            "name": row["name"],
            "ts_code": row["ts_code"],
            "value": num(row.get(field)),
        } for index, row in enumerate(rows, 1)]

    rps10_top15 = sorted(rps10, key=lambda row: integer(row["rank_10d"]))[:15]
    rps20_top15 = sorted(rps20, key=lambda row: integer(row["rank_20d"]))[:15]
    rps10_detail = [{
        "rank": integer(row["rank_10d"]),
        "name": row["name"],
        "ts_code": row["ts_code"],
        "industry": row["industries"],
        "market": row["market"],
        "return_pct": num(row["return_10d_pct"]),
        "rps": num(row["rps_10d"]),
        "total_mv_yi": num(row["total_mv_yi"]),
        "latest_amount_yi": num(row["latest_amount_yi"]),
    } for row in rps10]
    rps20_detail = [{
        "rank": integer(row["rank_20d"]),
        "name": row["name"],
        "ts_code": row["ts_code"],
        "industry": row["industries"],
        "market": row["market"],
        "return_pct": num(row["return_20d_pct"]),
        "rps": num(row["rps_20d"]),
        "total_mv_yi": num(row["total_mv_yi"]),
        "latest_amount_yi": num(row["latest_amount_yi"]),
    } for row in rps20]
    etf_detail = [{
        "rank": index,
        "name": row["name"],
        "ts_code": row["ts_code"],
        "fund_type": row["fund_type"],
        "strength_score": num(row["strength_score"]),
        "return_10d_pct": num(row["return_10d_pct"]),
        "rps_10d": num(row["rps_10d"]),
        "return_20d_pct": num(row["return_20d_pct"]),
        "rps_20d": num(row["rps_20d"]),
        "estimated_flow_10d_yi": num(row["estimated_flow_10d_wan"]) / 10000.0,
        "share_days_covered_10d": integer(row["share_days_covered_10d"]),
        "latest_amount_yi": num(row["latest_amount_yi"]),
    } for index, row in enumerate(etf_top15, 1)]
    # 货币/现金管理ETF的份额变化量级会淹没权益、债券、商品ETF，且不适合
    # 用作“国家队权益态度”观察窗。原始结果仍完整保留，主资金榜仅排除现金管理类。
    etf_flow_eligible = [
        row for row in etfs
        if row.get("estimated_flow_10d_wan") not in (None, "")
        and integer(row.get("share_days_covered_10d")) == 10
        and "货币" not in str(row.get("fund_type") or "")
        and all(word not in str(row.get("name") or "") for word in ("货币", "现金", "保证金"))
    ]
    etf_flow_in = sorted(etf_flow_eligible, key=lambda row: (-num(row["estimated_flow_10d_wan"]), row["ts_code"]))[:10]
    etf_flow_out = sorted(etf_flow_eligible, key=lambda row: (num(row["estimated_flow_10d_wan"]), row["ts_code"]))[:10]

    turnover_dataset = [{
        "trade_date": row["trade_date"],
        "turnover_yi": num(row["turnover_yi"]),
        "stock_count": integer(row["stock_count"]),
    } for row in turnover]
    industry_daily_dataset = [{
        "trade_date": row["trade_date"],
        "industry": row["industry"],
        "net_amount_yi": num(row["net_amount_yi"]),
        "return_pct": num(row["return_pct"]),
    } for row in industry_daily]
    validation_dataset = [{
        "check": row["check"],
        "status": row["status"],
        "severity": row["severity"],
        "value": row["value"],
        "threshold": row["threshold"],
        "note": row["note"],
    } for row in checks]

    charts: list[dict[str, Any]] = [
        {
            "id": "market_turnover_chart",
            "title": "全A每日成交额",
            "subtitle": f"{rps_range}，单位：亿元；21个交易日",
            "type": "bar",
            "dataset": "market_turnover",
            "sourceId": "turnover",
            "encodings": {
                "x": {"field": "trade_date", "type": "temporal", "label": "交易日"},
                "y": {"field": "turnover_yi", "type": "quantitative", "label": "成交额（亿元）"},
                "tooltip": [
                    {"field": "stock_count", "type": "quantitative", "label": "有行情股票数"},
                    {"field": "turnover_yi", "type": "quantitative", "label": "成交额（亿元）"},
                ],
            },
            "valueFormat": "number",
            "layout": "full",
        },
        {
            "id": "industry_daily_chart",
            "title": "两周净流入前五行业的每日资金净额",
            "subtitle": f"{flow_range}，单位：亿元；正值流入、负值流出",
            "type": "line",
            "dataset": "industry_daily_top5",
            "sourceId": "industry_flow",
            "encodings": {
                "x": {"field": "trade_date", "type": "temporal", "label": "交易日"},
                "y": {"field": "net_amount_yi", "type": "quantitative", "label": "净额（亿元）"},
                "color": {"field": "industry", "type": "nominal", "label": "行业"},
                "tooltip": [
                    {"field": "industry", "type": "nominal", "label": "行业"},
                    {"field": "return_pct", "type": "quantitative", "label": "行业涨跌幅（%）"},
                ],
            },
            "valueFormat": "number",
            "layout": "full",
        },
    ]
    for period in WINDOWS:
        label = WINDOW_LABELS[period]
        charts.extend([
            ranking_chart(
                f"industry_{period}_inflow_chart", f"行业{label}净流入前十", f"截至{latest_label}，单位：亿元",
                f"industry_{period}_inflow", "industry_flow", "净额（亿元）",
            ),
            ranking_chart(
                f"industry_{period}_outflow_chart", f"行业{label}净流出前十", f"截至{latest_label}，单位：亿元；保留负号",
                f"industry_{period}_outflow", "industry_flow", "净额（亿元）",
            ),
        ])
    for period in WINDOWS:
        label = WINDOW_LABELS[period]
        charts.extend([
            ranking_chart(
                f"stock_{period}_inflow_chart", f"个股{label}净流入前十", f"截至{latest_label}，单位：亿元",
                f"stock_{period}_inflow", "stock_flow", "净额（亿元）",
            ),
            ranking_chart(
                f"stock_{period}_outflow_chart", f"个股{label}净流出前十", f"截至{latest_label}，单位：亿元；保留负号",
                f"stock_{period}_outflow", "stock_flow", "净额（亿元）",
            ),
        ])
    charts.extend([
        ranking_chart(
            "largecap_ratio_in_chart", "200亿以上个股两周资金流入强度", "窗口累计净额/最新总市值，单位：%",
            "largecap_ratio_in", "stock_flow", "占总市值（%）",
        ),
        ranking_chart(
            "largecap_ratio_out_chart", "200亿以上个股两周资金流出压力", "窗口累计净额/最新总市值，单位：%；保留负号",
            "largecap_ratio_out", "stock_flow", "占总市值（%）",
        ),
        ranking_chart(
            "rps10_chart", "半月RPS最强个股前十五", "10个交易日前复权收益，RPS范围0—100",
            "rps10_top15", "stock_rps", "RPS",
        ),
        ranking_chart(
            "rps20_chart", "月RPS最强个股前十五", "20个交易日前复权收益，RPS范围0—100",
            "rps20_top15", "stock_rps", "RPS",
        ),
        ranking_chart(
            "etf_strength_chart", "ETF综合强度前十五", "强度分为10日与20日RPS均值",
            "etf_strength", "etf", "强度分",
            layout="full",
        ),
        ranking_chart(
            "etf_flow_in_chart", "非货币ETF两周估算资金流入前十", "份额变化×当日收盘价，单位：亿元；排除现金管理ETF",
            "etf_flow_in", "etf", "估算资金流（亿元）",
        ),
        ranking_chart(
            "etf_flow_out_chart", "非货币ETF两周估算资金流出前十", "份额变化×当日收盘价，单位：亿元；排除现金管理ETF并保留负号",
            "etf_flow_out", "etf", "估算资金流（亿元）",
        ),
    ])

    tables = [
        {
            "id": "industry_detail_table",
            "title": "同花顺行业多时间窗明细",
            "subtitle": "当天/一周/两周净额同表对照，单位：亿元",
            "dataset": "industry_detail",
            "sourceId": "industry_flow",
            "defaultSort": {"field": "net_10d_yi", "direction": "desc"},
            "columns": [
                {"field": "industry", "label": "行业", "type": "text"},
                {"field": "net_1d_yi", "label": "当天净额", "format": "number", "movement": True},
                {"field": "net_5d_yi", "label": "一周净额", "format": "number", "movement": True},
                {"field": "net_10d_yi", "label": "两周净额", "format": "number", "movement": True},
                {"field": "positive_days_10d", "label": "两周流入天数", "format": "number"},
                {"field": "return_10d_pct", "label": "两周涨幅（%）", "format": "number", "movement": True},
                {"field": "company_num", "label": "公司数", "format": "number"},
                {"field": "latest_lead_stock", "label": "最新领涨股", "type": "text"},
            ],
            "layout": "full",
        },
        {
            "id": "stock_detail_table",
            "title": "个股资金流入与流出头部明细",
            "subtitle": "当天/一周/两周榜单并集；净额单位亿元，比例以最新总市值为分母",
            "dataset": "stock_detail",
            "sourceId": "stock_flow",
            "defaultSort": {"field": "net_10d_yi", "direction": "desc"},
            "density": "dense",
            "columns": [
                {"field": "name", "label": "名称", "type": "text"},
                {"field": "ts_code", "label": "代码", "type": "text"},
                {"field": "industry", "label": "同花顺行业", "type": "text"},
                {"field": "total_mv_yi", "label": "总市值（亿元）", "format": "number"},
                {"field": "net_1d_yi", "label": "当天净额", "format": "number", "movement": True},
                {"field": "net_5d_yi", "label": "一周净额", "format": "number", "movement": True},
                {"field": "net_10d_yi", "label": "两周净额", "format": "number", "movement": True},
                {"field": "ratio_10d_pct", "label": "两周净额/市值（%）", "format": "number", "movement": True},
            ],
            "layout": "full",
        },
        {
            "id": "largecap_table",
            "title": "总市值200亿元以上个股资金相对强度",
            "subtitle": "保留绝对净额，同时展示净额占最新总市值比例",
            "dataset": "largecap_detail",
            "sourceId": "stock_flow",
            "defaultSort": {"field": "ratio_10d_pct", "direction": "desc"},
            "density": "dense",
            "columns": [
                {"field": "name", "label": "名称", "type": "text"},
                {"field": "ts_code", "label": "代码", "type": "text"},
                {"field": "industry", "label": "行业", "type": "text"},
                {"field": "total_mv_yi", "label": "总市值（亿元）", "format": "number"},
                {"field": "net_1d_yi", "label": "当天净额（亿元）", "format": "number", "movement": True},
                {"field": "ratio_1d_pct", "label": "当天占比（%）", "format": "number", "movement": True},
                {"field": "net_5d_yi", "label": "一周净额（亿元）", "format": "number", "movement": True},
                {"field": "ratio_5d_pct", "label": "一周占比（%）", "format": "number", "movement": True},
                {"field": "net_10d_yi", "label": "两周净额（亿元）", "format": "number", "movement": True},
                {"field": "ratio_10d_pct", "label": "两周占比（%）", "format": "number", "movement": True},
            ],
            "layout": "full",
        },
        {
            "id": "rps10_table",
            "title": "半月RPS前5%个股",
            "subtitle": f"{summary['validation']['rps10_eligible']}只可比股票，RPS>=95",
            "dataset": "rps10_detail",
            "sourceId": "stock_rps",
            "defaultSort": {"field": "rank", "direction": "asc"},
            "density": "dense",
            "columns": [
                {"field": "rank", "label": "排名", "format": "number"},
                {"field": "name", "label": "名称", "type": "text"},
                {"field": "ts_code", "label": "代码", "type": "text"},
                {"field": "industry", "label": "行业", "type": "text"},
                {"field": "return_pct", "label": "10日收益（%）", "format": "number", "movement": True},
                {"field": "rps", "label": "RPS", "format": "number"},
                {"field": "total_mv_yi", "label": "总市值（亿元）", "format": "number"},
                {"field": "latest_amount_yi", "label": "最新成交额（亿元）", "format": "number"},
            ],
            "layout": "full",
        },
        {
            "id": "rps20_table",
            "title": "月RPS前5%个股",
            "subtitle": f"{summary['validation']['rps20_eligible']}只可比股票，RPS>=95",
            "dataset": "rps20_detail",
            "sourceId": "stock_rps",
            "defaultSort": {"field": "rank", "direction": "asc"},
            "density": "dense",
            "columns": [
                {"field": "rank", "label": "排名", "format": "number"},
                {"field": "name", "label": "名称", "type": "text"},
                {"field": "ts_code", "label": "代码", "type": "text"},
                {"field": "industry", "label": "行业", "type": "text"},
                {"field": "return_pct", "label": "20日收益（%）", "format": "number", "movement": True},
                {"field": "rps", "label": "RPS", "format": "number"},
                {"field": "total_mv_yi", "label": "总市值（亿元）", "format": "number"},
                {"field": "latest_amount_yi", "label": "最新成交额（亿元）", "format": "number"},
            ],
            "layout": "full",
        },
        {
            "id": "etf_table",
            "title": "ETF综合强度前十五",
            "subtitle": "同时展示价格相对强度、成交额和两周估算申赎资金",
            "dataset": "etf_detail",
            "sourceId": "etf",
            "defaultSort": {"field": "rank", "direction": "asc"},
            "columns": [
                {"field": "rank", "label": "排名", "format": "number"},
                {"field": "name", "label": "ETF", "type": "text"},
                {"field": "ts_code", "label": "代码", "type": "text"},
                {"field": "fund_type", "label": "类型", "type": "text"},
                {"field": "strength_score", "label": "综合强度", "format": "number"},
                {"field": "return_10d_pct", "label": "10日收益（%）", "format": "number", "movement": True},
                {"field": "return_20d_pct", "label": "20日收益（%）", "format": "number", "movement": True},
                {"field": "estimated_flow_10d_yi", "label": "两周估算资金流（亿元）", "format": "number", "movement": True},
                {"field": "share_days_covered_10d", "label": "份额覆盖日", "format": "number"},
                {"field": "latest_amount_yi", "label": "最新成交额（亿元）", "format": "number"},
            ],
            "layout": "full",
        },
        {
            "id": "validation_table",
            "title": "数据质量验证清单",
            "subtitle": "交易日、主键、接口上限、资金算术、独立口径、市值、RPS及ETF覆盖",
            "dataset": "validation_checks",
            "sourceId": "validation",
            "defaultSort": {"field": "severity", "direction": "asc"},
            "columns": [
                {"field": "check", "label": "检查项", "type": "text"},
                {"field": "status", "label": "状态", "type": "text"},
                {"field": "severity", "label": "严重度", "type": "text"},
                {"field": "value", "label": "结果", "type": "text"},
                {"field": "threshold", "label": "阈值", "type": "text"},
                {"field": "note", "label": "说明", "type": "text"},
            ],
            "layout": "full",
        },
    ]

    cards = [
        {"id": "industry_in_card", "description": f"{top_ind_in['industry']}为两周行业净流入第一。", "dataset": "headline", "sourceId": "industry_flow", "metrics": [{"label": "行业两周最大净流入（亿元）", "field": "industry_in_yi", "format": "number"}]},
        {"id": "industry_out_card", "description": f"{top_ind_out['industry']}为两周行业净流出第一。", "dataset": "headline", "sourceId": "industry_flow", "metrics": [{"label": "行业两周最大净流出（亿元）", "field": "industry_out_yi", "format": "number"}]},
        {"id": "stock_in_card", "description": f"{top_stock_in['name']}为两周个股净流入第一。", "dataset": "headline", "sourceId": "stock_flow", "metrics": [{"label": "个股两周最大净流入（亿元）", "field": "stock_in_yi", "format": "number"}]},
        {"id": "stock_out_card", "description": f"{top_stock_out['name']}为两周个股净流出第一。", "dataset": "headline", "sourceId": "stock_flow", "metrics": [{"label": "个股两周最大净流出（亿元）", "field": "stock_out_yi", "format": "number"}]},
        {"id": "turnover_card", "description": f"{latest_label}全A日行情成交额汇总。", "dataset": "headline", "sourceId": "turnover", "metrics": [{"label": "最新成交额（亿元）", "field": "latest_turnover_yi", "format": "number"}]},
        {"id": "rps_count_card", "description": "半月RPS>=95的全市场股票数量。", "dataset": "headline", "sourceId": "stock_rps", "metrics": [{"label": "半月RPS前5%股票数", "field": "rps10_top5_count", "format": "number"}]},
        {"id": "etf_count_card", "description": "最新交易日有ETF日行情的基金数量。", "dataset": "headline", "sourceId": "etf", "metrics": [{"label": "ETF跟踪数量", "field": "etf_count", "format": "number"}]},
        {"id": "quality_card", "description": f"整体质量状态：{summary['quality_status']}。", "dataset": "headline", "sourceId": "validation", "metrics": [{"label": "自动检查通过率", "field": "validation_pass_rate", "format": "percent"}]},
    ]

    blocks: list[dict[str, Any]] = [
        {"id": "title", "type": "markdown", "body": f"# A股资金流、RPS与ETF统一仪表盘\n\n截至 **{latest_label}** 收盘；资金流覆盖当天/一周/两周，RPS覆盖半月/月。"},
        {"id": "headline_cards", "type": "metric-strip", "cardIds": [card["id"] for card in cards]},
        {"id": "pulse_heading", "type": "markdown", "body": "## 市场成交与行业轮动\n\n先看成交活跃度，再观察两周主线行业每日资金是否持续。"},
        {"id": "market_turnover_block", "type": "chart", "chartId": "market_turnover_chart", "layout": "full"},
        {"id": "industry_daily_block", "type": "chart", "chartId": "industry_daily_chart", "layout": "full"},
        {"id": "industry_heading", "type": "markdown", "body": "## 同花顺行业资金流\n\n每个时间窗同时保留净流入与净流出，避免只观察单侧资金。"},
    ]
    for period in WINDOWS:
        blocks.extend([ranking_block(f"industry_{period}_inflow_chart"), ranking_block(f"industry_{period}_outflow_chart")])
    blocks.extend([
        {"id": "industry_table_block", "type": "table", "tableId": "industry_detail_table", "layout": "full"},
        {"id": "stock_heading", "type": "markdown", "body": "## 个股资金流\n\n绝对净额用于识别大规模资金行为；200亿元以上个股另用市值归一化比例衡量相对强度。"},
    ])
    for period in WINDOWS:
        blocks.extend([ranking_block(f"stock_{period}_inflow_chart"), ranking_block(f"stock_{period}_outflow_chart")])
    blocks.extend([
        {"id": "stock_table_block", "type": "table", "tableId": "stock_detail_table", "layout": "full"},
        ranking_block("largecap_ratio_in_chart"),
        ranking_block("largecap_ratio_out_chart"),
        {"id": "largecap_table_block", "type": "table", "tableId": "largecap_table", "layout": "full"},
        {"id": "rps_heading", "type": "markdown", "body": "## 全市场RPS\n\nRPS衡量区间前复权收益在全A可比股票中的百分位，不等同于未来收益预测。"},
        ranking_block("rps10_chart"),
        ranking_block("rps20_chart"),
        {"id": "rps10_table_block", "type": "table", "tableId": "rps10_table", "layout": "full"},
        {"id": "rps20_table_block", "type": "table", "tableId": "rps20_table", "layout": "full"},
        {"id": "etf_heading", "type": "markdown", "body": "## ETF价格强度与申赎资金估算\n\nETF份额变化能观察申购赎回，但不能单凭该数据识别交易主体，更不能直接归因为央行或汇金。"},
        {"id": "etf_strength_block", "type": "chart", "chartId": "etf_strength_chart", "layout": "full"},
        ranking_block("etf_flow_in_chart"),
        ranking_block("etf_flow_out_chart"),
        {"id": "etf_table_block", "type": "table", "tableId": "etf_table", "layout": "full"},
        {"id": "validation_heading", "type": "markdown", "body": f"## 数据验证\n\n质量状态：**{summary['quality_status']}**。所有原始接口均按交易日分批，避免6000行上限截断。"},
        {"id": "validation_table_block", "type": "table", "tableId": "validation_table", "layout": "full"},
        {"id": "methodology", "type": "markdown", "body": "## 口径与限制\n\n- 当天/一周/两周分别为最近1/5/10个交易日；半月/月RPS分别为10/20个交易日。\n- 行业使用同花顺90个资金流行业；个股资金流使用 `moneyflow_ths`，标准 `moneyflow` 只作方向和相关性复核。\n- 个股资金比例以最新交易日总市值为分母，仅对总市值不低于200亿元的股票排名。\n- ETF强度池以正式基金简称含ETF识别；主资金榜排除货币、现金和保证金ETF，避免现金管理产品淹没权益等风险资产。\n- ETF申赎资金是份额变化乘当日收盘价的估算；份额缺失不插值，且ETF流向不能识别投资者身份。\n- 本仪表盘提供行为与相对强度证据，不构成投资建议。"},
    ])

    manifest = {
        "version": 1,
        "surface": "dashboard",
        "title": "A股资金流、RPS与ETF统一仪表盘",
        "description": f"截至{latest_label}，覆盖同花顺行业、个股资金流、RPS、ETF和数据质量验证。",
        "generatedAt": generated_at,
        "cards": cards,
        "charts": charts,
        "tables": tables,
        "sources": sources,
        "blocks": blocks,
    }
    datasets: dict[str, list[dict[str, Any]]] = {
        "headline": headline,
        "market_turnover": turnover_dataset,
        "industry_daily_top5": industry_daily_dataset,
        "industry_detail": industry_detail,
        "stock_detail": stock_detail,
        "largecap_detail": largecap_detail,
        "largecap_ratio_in": chart_rows(largecap_in, "ratio_10d_pct"),
        "largecap_ratio_out": chart_rows(largecap_out, "ratio_10d_pct"),
        "rps10_top15": chart_rows(rps10_top15, "rps_10d", "rank_10d"),
        "rps20_top15": chart_rows(rps20_top15, "rps_20d", "rank_20d"),
        "rps10_detail": rps10_detail,
        "rps20_detail": rps20_detail,
        "etf_strength": chart_rows(etf_top15, "strength_score"),
        "etf_flow_in": [{"rank": index, "name": row["name"], "ts_code": row["ts_code"], "value": num(row["estimated_flow_10d_wan"]) / 10000.0} for index, row in enumerate(etf_flow_in, 1)],
        "etf_flow_out": [{"rank": index, "name": row["name"], "ts_code": row["ts_code"], "value": num(row["estimated_flow_10d_wan"]) / 10000.0} for index, row in enumerate(etf_flow_out, 1)],
        "etf_detail": etf_detail,
        "validation_checks": validation_dataset,
        **industry_datasets,
        **stock_datasets,
    }
    artifact = {
        "surface": "dashboard",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready" if summary["quality_status"] != "FAIL" else "partial",
            "datasets": datasets,
            "accessIssues": [] if summary["quality_status"] != "FAIL" else ["存在关键数据质量失败，详情见验证表"],
        },
        "sources": sources,
    }
    destination = output_dir / "artifact.json"
    destination.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
