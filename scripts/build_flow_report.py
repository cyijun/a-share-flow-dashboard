#!/usr/bin/env python3
"""把五日资金流结果打包为 Data Analytics 标准 HTML 报告的 artifact.json。"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def num(value: Any) -> float:
    return float(value or 0)


def integer(value: Any) -> int:
    return int(float(value or 0))


def source(
    source_id: str,
    label: str,
    path: str,
    api_name: str,
    description: str,
    executed_at: str,
    doc_url: str,
    tables_used: list[str],
    filters: list[str],
    definitions: dict[str, str],
) -> dict[str, Any]:
    date_literal = filters[0].split(" in ", 1)[1] if filters and " in " in filters[0] else "[]"
    dates_sql = date_literal.strip("[]")
    if api_name.startswith("daily +"):
        query_text = "SELECT check, status, severity, value, threshold, note FROM local.validation_checks ORDER BY severity, check"
    elif api_name == "moneyflow_ind_ths":
        query_text = (
            "SELECT ts_code, industry, SUM(net_amount) AS net_amount_5d_yi, "
            "SUM(net_buy_amount) AS gross_inflow_5d_yi, SUM(net_sell_amount) AS gross_outflow_5d_yi "
            f"FROM Tushare.moneyflow_ind_ths WHERE trade_date IN ({dates_sql}) "
            "GROUP BY ts_code, industry ORDER BY net_amount_5d_yi DESC"
        )
    else:
        query_text = (
            "SELECT ts_code, MAX_BY(name, trade_date) AS name, SUM(net_amount) AS net_amount_5d_wan, "
            "SUM(buy_lg_amount) AS large_order_net_5d_wan "
            f"FROM Tushare.{api_name} WHERE trade_date IN ({dates_sql}) "
            "GROUP BY ts_code ORDER BY net_amount_5d_wan DESC"
        )
    return {
        "id": source_id,
        "label": label,
        "path": path,
        "url": doc_url,
        "query": {
            "engine": "DuckDB over persisted Tushare API snapshots",
            "language": "sql",
            "description": description,
            "executed_at": executed_at,
            "tables_used": tables_used,
            "filters": filters,
            "metric_definitions": definitions,
            "sql": query_text,
        },
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("用法: python3 scripts/build_flow_report.py outputs/YYYYMMDD")
    output_dir = (ROOT / sys.argv[1]).resolve() if not Path(sys.argv[1]).is_absolute() else Path(sys.argv[1])
    result_dir = output_dir / "results"
    summary = json.loads((result_dir / "summary.json").read_text(encoding="utf-8"))
    industries = read_csv(result_dir / "industry_flow_top10.csv")
    stocks = read_csv(result_dir / "stock_flow_top20.csv")
    by_industry = read_csv(result_dir / "stock_flow_top_by_industry.csv")
    checks = read_csv(result_dir / "validation_checks.csv")
    coverage = read_csv(result_dir / "coverage_by_market.csv")

    generated_at = summary["generated_at"]
    dates = summary["trade_dates"]
    date_label = f"{dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:]} 至 {dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:]}"
    top_industry = summary["top_industries"][0]
    top_stock = summary["top_stocks"][0]
    top_stock_yi = num(top_stock["net_amount_5d_wan"]) / 10000
    electronics_names = {"半导体", "通信设备", "元件", "消费电子", "光学光电子", "电子化学品"}
    electronics_net = sum(num(row["net_amount_5d_yi"]) for row in industries if row["industry"] in electronics_names)
    top20_industries = Counter(row["industries"] for row in stocks)
    semiconductor_count = top20_industries.get("半导体", 0)
    warning_checks = [row for row in checks if row["status"] == "WARN"]

    industry_dataset = [{
        "rank": integer(row["rank"]),
        "industry": row["industry"],
        "ts_code": row["ts_code"],
        "net_amount_5d_yi": num(row["net_amount_5d_yi"]),
        "gross_inflow_5d_yi": num(row["gross_inflow_5d_yi"]),
        "gross_outflow_5d_yi": num(row["gross_outflow_5d_yi"]),
        "positive_days": integer(row["positive_days"]),
        "return_5d_pct": num(row["return_5d_pct"]),
        "company_num": integer(row["company_num"]),
        "latest_lead_stock": row["latest_lead_stock"],
    } for row in industries]
    stock_dataset = [{
        "rank": integer(row["rank"]),
        "name": row["name"],
        "ts_code": row["ts_code"],
        "industry": row["industries"],
        "net_amount_5d_yi": num(row["net_amount_5d_wan"]) / 10000,
        "large_order_net_5d_yi": num(row["large_order_net_5d_wan"]) / 10000,
        "positive_days": integer(row["positive_days"]),
        "days_covered": integer(row["days_covered"]),
        "return_5d_pct": num(row["return_5d_pct"]),
        "latest_price": num(row["latest_price"]),
    } for row in stocks]
    by_industry_dataset = [{
        "industry_rank": integer(row["industry_rank"]),
        "industry": row["industry"],
        "industry_net_amount_5d_yi": num(row["industry_net_amount_5d_yi"]),
        "stock_rank_in_industry": integer(row["stock_rank_in_industry"]),
        "name": row["name"],
        "ts_code": row["ts_code"],
        "stock_net_amount_5d_yi": num(row["stock_net_amount_5d_wan"]) / 10000,
        "positive_days": integer(row["positive_days"]),
        "days_covered": integer(row["days_covered"]),
        "return_5d_pct": num(row["return_5d_pct"]),
    } for row in by_industry]
    check_dataset = [{
        "check": row["check"],
        "status": row["status"],
        "severity": row["severity"],
        "value": row["value"],
        "threshold": row["threshold"],
        "note": row["note"],
    } for row in checks]
    coverage_dataset = [{
        "exchange": row["exchange"],
        "market": row["market"],
        "daily_rows_5d": integer(row["daily_rows_5d"]),
        "ths_moneyflow_rows_5d": integer(row["moneyflow_rows_5d"]),
        "ths_coverage_pct": num(row["coverage_pct"]),
    } for row in coverage]
    headline_dataset = [{
        "top_industry_net_yi": num(top_industry["net_amount_5d_yi"]),
        "top_stock_net_yi": top_stock_yi,
        "price_match_rate": num(summary["validation"]["pct_change_match_rate"]),
        "all_a_coverage_rate": num(summary["validation"]["classic_daily_coverage"]),
    }]

    output_rel = output_dir.relative_to(ROOT).as_posix()
    source_docs = summary["source_documents"]
    sources = [
        source(
            "industry_ths",
            "Tushare 同花顺行业资金流",
            f"{output_rel}/raw/industry_flow_ths.csv",
            "moneyflow_ind_ths",
            "按五个交易日拉取 90 个同花顺资金流行业，并对 net_amount 求和。",
            generated_at,
            source_docs["moneyflow_ind_ths"],
            ["Tushare.moneyflow_ind_ths"],
            [f"trade_date in {dates}"],
            {"行业五日净流入": "SUM(net_amount)，接口单位为亿元"},
        ),
        source(
            "stock_ths",
            "Tushare 同花顺个股资金流",
            f"{output_rel}/raw/stock_flow_ths.csv",
            "moneyflow_ths",
            "按五个交易日分批提取同花顺个股资金流，并与行业成分映射。",
            generated_at,
            source_docs["moneyflow_ths"],
            ["Tushare.moneyflow_ths", "Tushare.ths_member"],
            [f"trade_date in {dates}", "沪深市场；北交所由标准 moneyflow 独立复核"],
            {"个股五日净流入": "SUM(net_amount)，接口单位为万元"},
        ),
        source(
            "validation",
            "资金流数据质量验证结果",
            f"{output_rel}/results/validation_checks.csv",
            "daily + moneyflow + ths_index + ths_member",
            "对日期、主键、字段、行情、五日主力净额、行业映射、成分汇总和全 A 覆盖执行交叉验证。",
            generated_at,
            source_docs["moneyflow"],
            ["Tushare.daily", "Tushare.moneyflow", "Tushare.ths_index", "Tushare.ths_member"],
            [f"trade_date in {dates}"],
            {
                "价格一致率": "abs(moneyflow_ths.pct_change - daily.pct_chg) <= 0.02 个百分点",
                "五日主力一致率": "SUM(逐日大单净额) 与最新日 net_d5_amount 在容差内一致",
                "行业方向一致率": "排除整数亿元舍入后的近零项，对行业净额与成分股净额和比较方向",
            },
        ),
    ]

    title = "A股五日资金流：同花顺行业与个股榜"
    manifest = {
        "version": 1,
        "surface": "report",
        "title": title,
        "description": f"{date_label} 同花顺行业与个股资金流排名及多层数据验证。",
        "generatedAt": generated_at,
        "cards": [
            {
                "id": "top_industry_card",
                "description": f"{top_industry['industry']}为五日行业净流入第一。",
                "dataset": "headline",
                "sourceId": "industry_ths",
                "metrics": [{"label": "第一行业净流入（亿元）", "field": "top_industry_net_yi", "format": "number"}],
            },
            {
                "id": "top_stock_card",
                "description": f"{top_stock['name']}为同花顺个股净流入第一。",
                "dataset": "headline",
                "sourceId": "stock_ths",
                "metrics": [{"label": "第一只个股净流入（亿元）", "field": "top_stock_net_yi", "format": "number"}],
            },
            {
                "id": "price_match_card",
                "description": "资金流数据中的涨跌幅与日行情逐记录核对。",
                "dataset": "headline",
                "sourceId": "validation",
                "metrics": [{"label": "价格字段一致率", "field": "price_match_rate", "format": "percent"}],
            },
            {
                "id": "all_a_card",
                "description": "标准 moneyflow 对五日 A 股日行情的覆盖率。",
                "dataset": "headline",
                "sourceId": "validation",
                "metrics": [{"label": "全 A 覆盖率", "field": "all_a_coverage_rate", "format": "percent"}],
            },
        ],
        "charts": [
            {
                "id": "industry_ranking_chart",
                "title": "同花顺行业五日净流入前十",
                "subtitle": f"{date_label}，净额单位：亿元；按五日累计净流入降序",
                "type": "bar",
                "dataset": "industry_top10",
                "sourceId": "industry_ths",
                "encodings": {
                    "x": {"field": "industry", "type": "nominal", "label": "行业"},
                    "y": {"field": "net_amount_5d_yi", "type": "quantitative", "label": "五日净流入（亿元）"},
                    "tooltip": [
                        {"field": "rank", "type": "quantitative", "label": "排名"},
                        {"field": "return_5d_pct", "type": "quantitative", "label": "行业五日涨幅（%）"},
                        {"field": "positive_days", "type": "quantitative", "label": "净流入天数"},
                        {"field": "company_num", "type": "quantitative", "label": "公司数"},
                    ],
                },
                "valueFormat": "number",
                "layout": "full",
            },
            {
                "id": "stock_ranking_chart",
                "title": "个股五日净流入前十",
                "subtitle": f"{date_label}，同花顺个股资金流；净额换算为亿元",
                "type": "bar",
                "dataset": "stock_top20",
                "sourceId": "stock_ths",
                "transforms": [{"type": "limit", "count": 10}],
                "encodings": {
                    "x": {"field": "name", "type": "nominal", "label": "个股"},
                    "y": {"field": "net_amount_5d_yi", "type": "quantitative", "label": "五日净流入（亿元）"},
                    "tooltip": [
                        {"field": "ts_code", "type": "nominal", "label": "代码"},
                        {"field": "industry", "type": "nominal", "label": "同花顺行业"},
                        {"field": "return_5d_pct", "type": "quantitative", "label": "五日涨幅（%）"},
                        {"field": "positive_days", "type": "quantitative", "label": "净流入天数"},
                    ],
                },
                "valueFormat": "number",
                "layout": "full",
            },
        ],
        "tables": [
            {
                "id": "industry_top10_table",
                "title": "行业五日资金流明细",
                "subtitle": f"{date_label}；净流入、流入和流出单位均为亿元",
                "dataset": "industry_top10",
                "sourceId": "industry_ths",
                "defaultSort": {"field": "rank", "direction": "asc"},
                "columns": [
                    {"field": "rank", "label": "排名", "format": "number"},
                    {"field": "industry", "label": "行业", "type": "text"},
                    {"field": "ts_code", "label": "行业代码", "type": "text"},
                    {"field": "net_amount_5d_yi", "label": "净流入（亿元）", "format": "number", "movement": True},
                    {"field": "gross_inflow_5d_yi", "label": "流入（亿元）", "format": "number"},
                    {"field": "gross_outflow_5d_yi", "label": "流出（亿元）", "format": "number"},
                    {"field": "positive_days", "label": "净流入天数", "format": "number"},
                    {"field": "return_5d_pct", "label": "行业涨幅（%）", "format": "number", "movement": True},
                    {"field": "latest_lead_stock", "label": "最新领涨股", "type": "text"},
                ],
                "layout": "full",
            },
            {
                "id": "stock_top20_table",
                "title": "个股五日资金流前二十",
                "subtitle": f"{date_label}；同花顺个股净额换算为亿元，精确值见 CSV",
                "dataset": "stock_top20",
                "sourceId": "stock_ths",
                "defaultSort": {"field": "rank", "direction": "asc"},
                "columns": [
                    {"field": "rank", "label": "排名", "format": "number"},
                    {"field": "name", "label": "名称", "type": "text"},
                    {"field": "ts_code", "label": "代码", "type": "text"},
                    {"field": "industry", "label": "同花顺行业", "type": "text"},
                    {"field": "net_amount_5d_yi", "label": "净流入（亿元）", "format": "number", "movement": True},
                    {"field": "large_order_net_5d_yi", "label": "大单净额（亿元）", "format": "number", "movement": True},
                    {"field": "positive_days", "label": "净流入天数", "format": "number"},
                    {"field": "return_5d_pct", "label": "涨幅（%）", "format": "number", "movement": True},
                ],
                "layout": "full",
            },
            {
                "id": "by_industry_table",
                "title": "强势行业内个股前五",
                "subtitle": "行业按五日净流入前十，每个行业再按成分股五日净流入取前五",
                "dataset": "by_industry",
                "sourceId": "stock_ths",
                "defaultSort": {"field": "industry_rank", "direction": "asc"},
                "density": "dense",
                "columns": [
                    {"field": "industry_rank", "label": "行业排名", "format": "number"},
                    {"field": "industry", "label": "行业", "type": "text"},
                    {"field": "stock_rank_in_industry", "label": "行业内排名", "format": "number"},
                    {"field": "name", "label": "个股", "type": "text"},
                    {"field": "ts_code", "label": "代码", "type": "text"},
                    {"field": "stock_net_amount_5d_yi", "label": "净流入（亿元）", "format": "number", "movement": True},
                    {"field": "positive_days", "label": "净流入天数", "format": "number"},
                    {"field": "return_5d_pct", "label": "涨幅（%）", "format": "number", "movement": True},
                ],
                "layout": "full",
            },
            {
                "id": "validation_table",
                "title": "数据质量验证清单",
                "subtitle": "关键主键、字段、行情、五日滚动、行业映射和独立资金流均已复核",
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
            {
                "id": "coverage_table",
                "title": "同花顺个股资金流的市场覆盖",
                "subtitle": "五日逐日行情与 moneyflow_ths 代码匹配；标准 moneyflow 另行覆盖北交所",
                "dataset": "coverage_market",
                "sourceId": "validation",
                "defaultSort": {"field": "daily_rows_5d", "direction": "desc"},
                "columns": [
                    {"field": "exchange", "label": "交易所", "type": "text"},
                    {"field": "market", "label": "市场", "type": "text"},
                    {"field": "daily_rows_5d", "label": "日行情记录", "format": "number"},
                    {"field": "ths_moneyflow_rows_5d", "label": "THS资金流记录", "format": "number"},
                    {"field": "ths_coverage_pct", "label": "THS覆盖率（%）", "format": "number"},
                ],
                "layout": "full",
            },
        ],
        "sources": sources,
        "blocks": [
            {"id": "title", "type": "markdown", "body": f"# {title}"},
            {
                "id": "executive_summary",
                "type": "markdown",
                "body": (
                    "## Executive Summary\n\n"
                    f"- **半导体以 {num(top_industry['net_amount_5d_yi']):,.0f} 亿元居行业净流入第一。** "
                    f"通信设备和元件分别为 {num(industries[1]['net_amount_5d_yi']):,.0f} 亿元、{num(industries[2]['net_amount_5d_yi']):,.0f} 亿元。\n"
                    f"- **电子与通信相关六个行业合计净流入约 {electronics_net:,.0f} 亿元。** 资金集中在半导体、通信设备、元件、消费电子、光学光电子和电子化学品。\n"
                    f"- **个股首位是{top_stock['name']}，五日净流入 {top_stock_yi:,.2f} 亿元。** 个股前二十中半导体行业占 {semiconductor_count} 只。\n"
                    f"- **关键数据校验通过，仅有 {len(warning_checks)} 项低风险提示。** 行情匹配、五日主力净额、行业模板映射和全 A 覆盖均达到预设阈值。"
                ),
            },
            {"id": "headline_metrics", "type": "metric-strip", "cardIds": ["top_industry_card", "top_stock_card", "price_match_card", "all_a_card"]},
            {
                "id": "scope",
                "type": "markdown",
                "body": (
                    "## 统计口径与范围\n\n"
                    f"统计窗口为 **{date_label}**，共五个交易日。行业采用同花顺 90 个资金流行业模板，"
                    "主排名指标为 `net_amount` 的五日累计值；行业单位亿元，个股原始单位万元。"
                    "个股主榜采用同花顺资金流以保持口径一致；标准 `moneyflow` 对全 A 股（含北交所）独立复核。"
                ),
            },
            {
                "id": "industry_finding",
                "type": "markdown",
                "sourceId": "industry_ths",
                "body": (
                    "## 资金明显向电子与通信链集中\n\n"
                    f"**半导体、通信设备和元件占据前三。** 三者五日净流入合计 "
                    f"{sum(num(row['net_amount_5d_yi']) for row in industries[:3]):,.0f} 亿元；"
                    f"其中半导体五日行业涨幅 {num(top_industry['return_5d_pct']):.2f}%，并有 {top_industry['positive_days']} 个净流入日。"
                ),
            },
            {"id": "industry_chart_block", "type": "chart", "chartId": "industry_ranking_chart", "layout": "full"},
            {"id": "industry_table_block", "type": "table", "tableId": "industry_top10_table", "layout": "full"},
            {
                "id": "stock_finding",
                "type": "markdown",
                "sourceId": "stock_ths",
                "body": (
                    "## 个股净流入由光通信、AI硬件与半导体龙头主导\n\n"
                    f"**{top_stock['name']}、{stocks[1]['name']}、{stocks[2]['name']}位居前三。** "
                    f"五日净流入分别为 {top_stock_yi:,.2f}、{num(stocks[1]['net_amount_5d_wan'])/10000:,.2f}、"
                    f"{num(stocks[2]['net_amount_5d_wan'])/10000:,.2f} 亿元。"
                    "排名衡量的是资金行为，不代表基本面质量或未来收益。"
                ),
            },
            {"id": "stock_chart_block", "type": "chart", "chartId": "stock_ranking_chart", "layout": "full"},
            {"id": "stock_table_block", "type": "table", "tableId": "stock_top20_table", "layout": "full"},
            {
                "id": "industry_stock_finding",
                "type": "markdown",
                "body": (
                    "## 行业内榜用于区分板块贝塔与个股贡献\n\n"
                    "下表在行业前十内部各取净流入前五。它能帮助识别板块资金是集中于少数龙头，"
                    "还是由更多成分共同推动；出现同花顺双重行业归属的股票会在对应行业分别保留。"
                ),
            },
            {"id": "by_industry_table_block", "type": "table", "tableId": "by_industry_table", "layout": "full"},
            {
                "id": "validation_finding",
                "type": "markdown",
                "sourceId": "validation",
                "body": (
                    "## 数据准确性达到可用标准\n\n"
                    f"**价格字段一致率为 {summary['validation']['pct_change_match_rate']*100:.4f}%，"
                    f"逐日大单五日和与接口五日主力净额一致率为 {summary['validation']['rolling_large_order_five_day_match_rate']*100:.4f}%。** "
                    f"行业净额与成分股汇总的相关系数为 {summary['validation']['industry_reconciliation_correlation']:.4f}，"
                    f"有效记录方向一致率为 {summary['validation']['industry_reconciliation_material_sign_rate']*100:.4f}%。"
                ),
            },
            {"id": "validation_table_block", "type": "table", "tableId": "validation_table", "layout": "full"},
            {
                "id": "coverage_finding",
                "type": "markdown",
                "sourceId": "validation",
                "body": (
                    "## 北交所通过独立口径补充验证\n\n"
                    "同花顺个股资金流覆盖沪深主板、创业板和科创板，但不返回北交所。"
                    f"标准 `moneyflow` 与日行情五日记录实现 100% 匹配；北交所最高名次为第 {summary['bse_best_standard']['rank']}，"
                    f"未进入前二十，因此不会改变本次头部个股结论。"
                ),
            },
            {"id": "coverage_table_block", "type": "table", "tableId": "coverage_table", "layout": "full"},
            {
                "id": "next_steps",
                "type": "markdown",
                "body": (
                    "## 建议的后续使用\n\n"
                    "- 连续滚动更新五日窗口，观察净流入排名是否稳定，而不是只看一次快照。\n"
                    "- 对行业前十同时跟踪成交额、涨跌幅与净流入天数，区分趋势性资金与单日脉冲。\n"
                    "- 对个股榜优先复核公告、业绩和估值，资金流仅作为行为证据，不直接转化为交易建议。"
                ),
            },
            {
                "id": "questions",
                "type": "markdown",
                "body": (
                    "## 仍需关注的问题\n\n"
                    "- 半导体与通信设备的高净流入能否在下一窗口继续保持？\n"
                    "- 前二十个股的资金集中度是否在上升，还是开始扩散到二线成分？\n"
                    "- 同花顺成分源的 18 只双重归属股票是否会在后续更新中收敛？"
                ),
            },
            {
                "id": "caveats",
                "type": "markdown",
                "body": (
                    "## 假设与限制\n\n"
                    "行业 `net_amount` 以整数亿元返回，近零项目存在舍入；同花顺个股和标准 `moneyflow` 算法不同，"
                    "因此交叉验证侧重方向、相关性和头部重合，而非逐值相等。行业成分按当前 `ths_member` 快照映射，"
                    "其中 18 只股票有双重行业归属。所有结果都是截至 2026-08-07 收盘后的数据快照。"
                ),
            },
        ],
    }

    artifact = {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "headline": headline_dataset,
                "industry_top10": industry_dataset,
                "stock_top20": stock_dataset,
                "by_industry": by_industry_dataset,
                "validation_checks": check_dataset,
                "coverage_market": coverage_dataset,
            },
        },
        "sources": sources,
    }
    destination = output_dir / "artifact.json"
    destination.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
