#!/usr/bin/env python3
"""用 Python 标准库把分析快照渲染为自包含、响应式 HTML。"""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def fmt(value: Any, digits: int = 2) -> str:
    return f"{num(value):,.{digits}f}"


def signed_bar_chart(title: str, subtitle: str, rows: list[dict[str, Any]]) -> str:
    maximum = max((abs(num(row["value"])) for row in rows), default=1) or 1
    items: list[str] = []
    for index, row in enumerate(rows, 1):
        value = num(row["value"])
        width = max(2.0, 100 * abs(value) / maximum)
        tone = "positive" if value >= 0 else "negative"
        items.append(
            f'<div class="bar-row"><div class="bar-name"><span>{index}</span>{esc(row["name"])}'
            f'<small>{esc(row.get("code", ""))}</small></div><div class="bar-track">'
            f'<div class="bar {tone}" style="width:{width:.2f}%"></div></div>'
            f'<div class="bar-value {tone}-text">{value:,.2f}</div></div>'
        )
    return (
        f'<article class="panel chart"><header><h3>{esc(title)}</h3><p>{esc(subtitle)}</p></header>'
        + "".join(items) + "</article>"
    )


def table(title: str, rows: list[dict[str, Any]], columns: list[tuple[str, str, Callable[[Any], str] | None]]) -> str:
    head = "".join(f"<th>{esc(label)}</th>" for _, label, _ in columns)
    body: list[str] = []
    for row in rows:
        cells: list[str] = []
        for field, _, formatter in columns:
            raw = row.get(field, "")
            value = formatter(raw) if formatter else esc(raw)
            movement = ""
            if formatter and isinstance(raw, (int, float, str)):
                numeric = num(raw)
                movement = " pos" if numeric > 0 else (" neg" if numeric < 0 else "")
            cells.append(f'<td class="{movement.strip()}">{value}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<article class="panel table-panel"><h3>{esc(title)}</h3><div class="table-wrap">'
        f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div></article>'
    )


def rank_rows(
    rows: list[dict[str, str]], value_field: str, name_field: str, reverse: bool, limit: int = 10,
    divisor: float = 1.0,
) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda row: (num(row.get(value_field)), row.get("ts_code", "")), reverse=reverse)[:limit]
    return [{
        "name": row.get(name_field, ""), "code": row.get("ts_code", ""),
        "value": num(row.get(value_field)) / divisor,
    } for row in ranked]


def turnover_chart(rows: list[dict[str, str]]) -> str:
    values = [num(row.get("turnover_yi")) for row in rows]
    maximum = max(values, default=1) or 1
    bars = []
    for row, value in zip(rows, values):
        height = max(4, 100 * value / maximum)
        date = str(row.get("trade_date", ""))
        bars.append(
            f'<div class="turnover-item"><div class="turnover-value">{value:,.0f}</div>'
            f'<div class="turnover-bar" style="height:{height:.2f}%"></div>'
            f'<div class="turnover-date">{esc(date[4:])}</div></div>'
        )
    return (
        '<article class="panel turnover"><header><h3>全A每日成交额</h3><p>单位：亿元；用于判断资金活跃度背景</p></header>'
        f'<div class="turnover-grid">{"".join(bars)}</div></article>'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="快照目录，例如 outputs/20260814")
    args = parser.parse_args()
    candidate = Path(args.snapshot)
    output_dir = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    results = output_dir / "results"
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    industry = read_csv(results / "industry_window_summary.csv")
    stocks = read_csv(results / "stock_window_summary.csv")
    largecap = read_csv(results / "stock_largecap_flow_ratio.csv")
    rps = read_csv(results / "stock_rps_all.csv")
    etfs = read_csv(results / "etf_summary.csv")
    turnover = read_csv(results / "market_daily_turnover.csv")
    validations = read_csv(results / "validation_checks.csv")
    flow_windows = summary.get("flow_windows", [
        {"key": "1d", "days": 1, "label": "当天"},
        {"key": "5d", "days": 5, "label": "一周"},
        {"key": "10d", "days": 10, "label": "两周"},
    ])
    rps_windows = summary.get("rps_windows", [
        {"key": "10d", "days": 10, "label": "10日"},
        {"key": "20d", "days": 20, "label": "20日"},
    ])
    primary_key = summary.get("primary_flow_key", flow_windows[-1]["key"])
    primary_days = next(int(item["days"]) for item in flow_windows if item["key"] == primary_key)
    cap_floor = num(summary.get("market_cap_floor_yi", 200))
    latest = summary["latest_trade_date"]
    latest_label = f"{latest[:4]}-{latest[4:6]}-{latest[6:]}"

    top_ind_in = summary.get("top_industry_primary_inflow", summary.get("top_industry_10d_inflow", {}))
    top_ind_out = summary.get("top_industry_primary_outflow", summary.get("top_industry_10d_outflow", {}))
    top_stock_in = summary.get("top_stock_primary_inflow", summary.get("top_stock_10d_inflow", {}))
    top_stock_out = summary.get("top_stock_primary_outflow", summary.get("top_stock_10d_outflow", {}))
    cards = [
        ("行业最大净流入", top_ind_in.get("industry", "—"), fmt(top_ind_in.get(f"net_{primary_key}_yi")) + " 亿元", "up"),
        ("行业最大净流出", top_ind_out.get("industry", "—"), fmt(top_ind_out.get(f"net_{primary_key}_yi")) + " 亿元", "down"),
        ("沪深个股最大净流入", top_stock_in.get("name", "—"), fmt(num(top_stock_in.get(f"net_{primary_key}_wan")) / 10000) + " 亿元", "up"),
        ("沪深个股最大净流出", top_stock_out.get("name", "—"), fmt(num(top_stock_out.get(f"net_{primary_key}_wan")) / 10000) + " 亿元", "down"),
    ]
    cards_html = "".join(
        f'<div class="kpi {tone}"><span>{esc(label)} · {primary_days}日</span><strong>{esc(name)}</strong><b>{esc(value)}</b></div>'
        for label, name, value, tone in cards
    )

    sections: list[str] = [turnover_chart(turnover)]
    for window in flow_windows:
        key, days, label = window["key"], int(window["days"]), window["label"]
        industry_window = [row for row in industry if row.get(f"{key}_inflow_rank") not in (None, "")]
        stock_window = [row for row in stocks if row.get(f"{key}_inflow_rank") not in (None, "")]
        sections.append(f'<div class="section-title"><span>{days:02d}</span><div><h2>{esc(label)}资金流</h2><p>最近{days}个交易日累计净额</p></div></div>')
        sections.append('<div class="grid two">')
        sections.append(signed_bar_chart(
            f"同花顺行业 · {label}净流入前十", "单位：亿元",
            rank_rows(industry_window, f"net_{key}_yi", "industry", True),
        ))
        sections.append(signed_bar_chart(
            f"同花顺行业 · {label}净流出前十", "单位：亿元；保留负号",
            rank_rows(industry_window, f"net_{key}_yi", "industry", False),
        ))
        sections.append(signed_bar_chart(
            f"沪深个股 · {label}净流入前十", "单位：亿元；仅完整覆盖窗口及最新日的样本",
            rank_rows(stock_window, f"net_{key}_wan", "name", True, divisor=10000),
        ))
        sections.append(signed_bar_chart(
            f"沪深个股 · {label}净流出前十", "单位：亿元；仅完整覆盖窗口及最新日的样本",
            rank_rows(stock_window, f"net_{key}_wan", "name", False, divisor=10000),
        ))
        sections.append("</div>")

    ratio_field = f"flow_mv_ratio_{primary_key}_pct"
    sections.append(f'<div class="section-title"><span>MV</span><div><h2>大市值资金强度</h2><p>总市值不低于{cap_floor:g}亿元，净额/最新总市值</p></div></div><div class="grid two">')
    sections.append(signed_bar_chart(
        f"{primary_days}日相对流入强度前十", "单位：%",
        rank_rows(largecap, ratio_field, "name", True),
    ))
    sections.append(signed_bar_chart(
        f"{primary_days}日相对流出压力前十", "单位：%；保留负号",
        rank_rows(largecap, ratio_field, "name", False),
    ))
    sections.append("</div>")
    largecap_top = sorted(largecap, key=lambda row: abs(num(row.get(ratio_field))), reverse=True)[:20]
    sections.append(table(
        "沪深大市值个股资金强度明细（绝对占比前20）", largecap_top,
        [("name", "名称", None), ("ts_code", "代码", None), ("total_mv_yi", "总市值/亿元", lambda x: fmt(x)),
         (f"net_{primary_key}_wan", f"{primary_days}日净额/亿元", lambda x: fmt(num(x) / 10000)),
         (ratio_field, "净额/市值 %", lambda x: fmt(x, 4))],
    ))

    sections.append('<div class="section-title"><span>RPS</span><div><h2>全市场相对强度</h2><p>前复权收益百分位，RPS≥95为前5%</p></div></div><div class="grid two">')
    for window in rps_windows:
        key, days = window["key"], int(window["days"])
        eligible = [row for row in rps if row.get(f"rps_{key}") not in (None, "")]
        strongest = sorted(eligible, key=lambda row: (num(row[f"rps_{key}"]), -num(row[f"rank_{key}"])), reverse=True)[:15]
        sections.append(signed_bar_chart(
            f"{days}日RPS前十五", f"可比股票 {len(eligible):,} 只",
            [{"name": row["name"], "code": row["ts_code"], "value": num(row[f"rps_{key}"])} for row in strongest],
        ))
    sections.append("</div>")

    sections.append('<div class="section-title"><span>ETF</span><div><h2>ETF价格强度与申赎估算</h2><p>份额变化×当日收盘价不能识别投资者身份</p></div></div><div class="grid two">')
    etf_strength = sorted(etfs, key=lambda row: num(row.get("strength_score")), reverse=True)[:15]
    sections.append(signed_bar_chart(
        "ETF综合RPS前十五", "所有已配置RPS窗口的均值",
        [{"name": row["name"], "code": row["ts_code"], "value": num(row["strength_score"])} for row in etf_strength],
    ))
    etf_field = f"estimated_flow_{primary_key}_wan"
    covered_field = f"share_days_covered_{primary_key}"
    etf_flow = [row for row in etfs if int(num(row.get(covered_field))) == primary_days and row.get(etf_field) not in (None, "")]
    sections.append(signed_bar_chart(
        f"ETF {primary_days}日估算净申赎前十五", "单位：亿元；按完整份额覆盖筛选",
        rank_rows(etf_flow, etf_field, "name", True, limit=15, divisor=10000),
    ))
    sections.append("</div>")

    validation_rows = [{
        "check": row.get("check", ""), "status": row.get("status", ""), "severity": row.get("severity", ""),
        "value": row.get("value", ""), "threshold": row.get("threshold", ""), "note": row.get("note", ""),
    } for row in validations]
    sections.append('<div class="section-title"><span>QA</span><div><h2>数据质量验证</h2><p>生产检查 + 原始CSV独立复算</p></div></div>')
    sections.append(table(
        "生产管道检查清单", validation_rows,
        [("check", "检查项", None), ("status", "状态", None), ("severity", "严重度", None),
         ("value", "结果", None), ("threshold", "阈值", None), ("note", "说明", None)],
    ))

    flow_text = " / ".join(f"{item['days']}日" for item in flow_windows)
    rps_text = " / ".join(f"{item['days']}日" for item in rps_windows)
    css = """
:root{--ink:#15221e;--muted:#68756f;--paper:#f4f1e8;--panel:#fffdf8;--line:#ded8ca;--up:#b74337;--down:#18705a;--gold:#d0a73d;--navy:#1b2f3a}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.hero{background:var(--navy);color:#fff;padding:56px max(24px,calc((100vw - 1280px)/2));position:relative;overflow:hidden}.hero:after{content:"";position:absolute;width:420px;height:420px;border:1px solid #ffffff24;border-radius:50%;right:-90px;top:-220px;box-shadow:0 0 0 70px #ffffff08,0 0 0 140px #ffffff06}.eyebrow{letter-spacing:.2em;text-transform:uppercase;color:#e8c970;font-size:12px}.hero h1{font-family:Georgia,"Noto Serif SC",serif;font-size:clamp(34px,5vw,64px);margin:12px 0 10px;line-height:1.05}.hero p{color:#cfdbd7;max-width:760px;line-height:1.8}.meta{display:flex;gap:22px;flex-wrap:wrap;margin-top:24px;font-size:13px}.meta b{color:#fff}.container{max-width:1280px;margin:auto;padding:28px 24px 80px}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:-54px;position:relative;z-index:2}.kpi{background:var(--panel);padding:20px;border:1px solid var(--line);box-shadow:0 12px 30px #1b2f3a12}.kpi span{font-size:12px;color:var(--muted)}.kpi strong,.kpi b{display:block}.kpi strong{font-size:20px;margin:10px 0 4px}.kpi b{font-size:16px}.kpi.up{border-top:4px solid var(--up)}.kpi.down{border-top:4px solid var(--down)}.section-title{display:flex;align-items:center;gap:16px;margin:52px 0 18px}.section-title>span{display:grid;place-items:center;width:54px;height:54px;border:1px solid var(--gold);border-radius:50%;font-family:Georgia,serif;color:#816510}.section-title h2{margin:0;font-family:Georgia,"Noto Serif SC",serif;font-size:28px}.section-title p,.panel header p{margin:5px 0 0;color:var(--muted);font-size:13px}.grid.two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.panel{background:var(--panel);border:1px solid var(--line);padding:22px;margin-bottom:16px}.panel h3{margin:0 0 6px;font-size:16px}.bar-row{display:grid;grid-template-columns:minmax(120px,1.15fr) minmax(90px,2fr) 90px;gap:12px;align-items:center;margin:12px 0}.bar-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px}.bar-name span{display:inline-block;color:#a19887;width:24px}.bar-name small{display:block;color:#9a9f9b;margin-left:24px;font-size:10px}.bar-track{height:8px;background:#e9e5dc;overflow:hidden}.bar{height:100%}.positive{background:var(--up)}.negative{background:var(--down)}.positive-text{color:var(--up)}.negative-text{color:var(--down)}.bar-value{text-align:right;font:600 12px ui-monospace,SFMono-Regular,Menlo,monospace}.turnover{margin-top:28px}.turnover-grid{height:260px;display:flex;align-items:flex-end;gap:5px;border-bottom:1px solid var(--line);padding-top:35px}.turnover-item{height:100%;flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;min-width:12px}.turnover-bar{width:min(28px,75%);background:linear-gradient(#d8bb62,#a77e22)}.turnover-value{font-size:9px;transform:rotate(-45deg);margin-bottom:12px;color:var(--muted)}.turnover-date{font-size:9px;margin-top:7px;color:var(--muted)}.table-panel{margin-top:16px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px}th{text-align:left;color:var(--muted);font-weight:600;background:#f1eee6;position:sticky;top:0}th,td{padding:10px;border-bottom:1px solid #e8e3d8;white-space:nowrap}td.pos{color:var(--up)}td.neg{color:var(--down)}.footer{margin-top:40px;padding-top:24px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;line-height:1.8}
@media(max-width:900px){.kpis{grid-template-columns:repeat(2,1fr)}.grid.two{grid-template-columns:1fr}.bar-row{grid-template-columns:minmax(110px,1fr) minmax(70px,1.4fr) 72px}.hero{padding-bottom:82px}}
@media(max-width:560px){.container{padding-left:14px;padding-right:14px}.kpis{grid-template-columns:1fr;margin-top:-48px}.hero{padding-left:20px;padding-right:20px}.panel{padding:16px}.turnover-grid{overflow-x:auto}.turnover-item{min-width:22px}.bar-name small{display:none}.bar-name span{width:18px}.section-title h2{font-size:23px}}
"""
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A股资金流、RPS与ETF仪表盘 · {latest_label}</title><style>{css}</style></head><body>
<section class="hero"><div class="eyebrow">A-SHARE CAPITAL FLOW MONITOR</div><h1>A股资金流<br>与相对强度仪表盘</h1><p>按同花顺行业模板观察资金主线；个股THS资金榜覆盖沪深市场，全市场RPS包含北交所，二者不混用样本口径。</p><div class="meta"><span>数据截止 <b>{latest_label}</b></span><span>资金流 <b>{esc(flow_text)}</b></span><span>RPS <b>{esc(rps_text)}</b></span><span>质量状态 <b>{esc(summary['quality_status'])}</b></span></div></section>
<main class="container"><section class="kpis">{cards_html}</section>{''.join(sections)}
<footer class="footer">数据源：Tushare Pro。行业使用 moneyflow_ind_ths；沪深个股使用 moneyflow_ths；北交所仅保留标准 moneyflow 独立复核数据，不与THS榜混排。ths_member是当前且非互斥的成员快照，行业不可跨板块求和。ETF资金为份额变化乘收盘价的估算，不能识别央行、汇金或其他具体主体。本页面不构成投资建议。<br>生成时间：{esc(summary['generated_at'])} · 快照：{esc(output_dir.name)}</footer></main></body></html>"""
    destination = output_dir / "report.html"
    destination.write_text(document, encoding="utf-8")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
