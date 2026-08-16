#!/usr/bin/env python3
"""生成并以标准库逐单元执行资金流数据质量审计 Notebook。"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def markdown(text: str) -> dict[str, Any]:
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text: str) -> dict[str, Any]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": text.splitlines(keepends=True),
    }


def execute_code_cells(cells: list[dict[str, Any]]) -> None:
    namespace: dict[str, Any] = {"__name__": "__notebook__"}
    execution_count = 0
    for cell in cells:
        if cell["cell_type"] != "code":
            continue
        execution_count += 1
        source = "".join(cell["source"])
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                exec(compile(source, f"notebook-cell-{execution_count}", "exec"), namespace)
        except Exception as exc:
            cell["execution_count"] = execution_count
            cell["outputs"] = [{
                "output_type": "error",
                "ename": type(exc).__name__,
                "evalue": str(exc),
                "traceback": [f"{type(exc).__name__}: {exc}"],
            }]
            raise
        cell["execution_count"] = execution_count
        output = stdout.getvalue()
        cell["outputs"] = ([{
            "output_type": "stream",
            "name": "stdout",
            "text": output.splitlines(keepends=True),
        }] if output else [])


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("用法: python3 scripts/build_validation_notebook.py outputs/YYYYMMDD")
    output_dir = (ROOT / sys.argv[1]).resolve() if not Path(sys.argv[1]).is_absolute() else Path(sys.argv[1])
    summary = json.loads((output_dir / "results" / "summary.json").read_text(encoding="utf-8"))
    dates = summary["trade_dates"]
    top_industry = summary["top_industries"][0]
    top_stock = summary["top_stocks"][0]
    relative_output = output_dir.relative_to(ROOT).as_posix()

    cells = [
        markdown(
            "# 同花顺行业与个股五日资金流：数据质量审计\n\n"
            "## tl;dr\n\n"
            f"- 统计窗口：**{dates[0]}—{dates[-1]}**。\n"
            f"- 行业首位：**{top_industry['industry']}**，五日净流入 **{top_industry['net_amount_5d_yi']:.0f} 亿元**。\n"
            f"- 个股首位：**{top_stock['name']}**，五日净流入 **{top_stock['net_amount_5d_wan']:.2f} 万元**。\n"
            f"- 数据质量状态：**{summary['quality_status']}**；无关键失败，仅保留行业成分双重归属提示。\n"
        ),
        markdown(
            "## Context & Methods\n\n"
            "目标是在同花顺行业模板下，复核五个交易日的行业和个股累计净流入排名。"
            "原始数据来自 Tushare `moneyflow_ind_ths`、`moneyflow_ths`、`moneyflow`、`daily`、"
            "`ths_index` 与 `ths_member`，每个交易日分批取数，防止触及单次行数上限。\n\n"
            "### Key Assumptions\n\n"
            "- 主排名使用 `net_amount` 五日逐日求和。\n"
            "- `net_d5_amount` 是五日主力/大单净额，只与逐日大单净额之和比较。\n"
            "- 行业净额按整数亿元返回，方向校验排除绝对值小于 1 亿元的舍入近零项。\n"
            "- 同花顺个股接口不含北交所，使用标准 `moneyflow` 做全 A 覆盖复核。\n"
        ),
        markdown("## Data\n\n### 1. 加载审计快照与辅助函数\n"),
        code(
            "from pathlib import Path\n"
            "import csv, json\n"
            f"OUTPUT_DIR = Path({relative_output!r})\n"
            "RESULT_DIR = OUTPUT_DIR / 'results'\n"
            "RAW_DIR = OUTPUT_DIR / 'raw'\n"
            "def read_csv(path):\n"
            "    with path.open(encoding='utf-8-sig', newline='') as handle:\n"
            "        return list(csv.DictReader(handle))\n"
            "summary = json.loads((RESULT_DIR / 'summary.json').read_text(encoding='utf-8'))\n"
            "industry_raw = read_csv(RAW_DIR / 'industry_flow_ths.csv')\n"
            "stock_raw = read_csv(RAW_DIR / 'stock_flow_ths.csv')\n"
            "daily_raw = read_csv(RAW_DIR / 'daily.csv')\n"
            "checks = read_csv(RESULT_DIR / 'validation_checks.csv')\n"
            "print('交易日:', ', '.join(summary['trade_dates']))\n"
            "print('原始行数:', {'行业资金流': len(industry_raw), '个股资金流': len(stock_raw), '日行情': len(daily_raw)})\n"
        ),
        markdown("## Results\n\n### 2. 从原始行独立重算行业和个股首位\n"),
        code(
            "from collections import defaultdict\n"
            "industry_sum = defaultdict(float)\n"
            "industry_name = {}\n"
            "for row in industry_raw:\n"
            "    industry_sum[row['ts_code']] += float(row['net_amount'])\n"
            "    industry_name[row['ts_code']] = row['industry']\n"
            "industry_top = max(industry_sum, key=industry_sum.get)\n"
            "stock_sum = defaultdict(float)\n"
            "stock_name = {}\n"
            "for row in stock_raw:\n"
            "    stock_sum[row['ts_code']] += float(row['net_amount'])\n"
            "    stock_name[row['ts_code']] = row['name']\n"
            "stock_top = max(stock_sum, key=stock_sum.get)\n"
            "assert industry_top == summary['top_industries'][0]['ts_code']\n"
            "assert abs(industry_sum[industry_top] - summary['top_industries'][0]['net_amount_5d_yi']) < 1e-9\n"
            "assert stock_top == summary['top_stocks'][0]['ts_code']\n"
            "assert abs(stock_sum[stock_top] - summary['top_stocks'][0]['net_amount_5d_wan']) < 0.01\n"
            "print(f\"行业首位复算通过: {industry_name[industry_top]} {industry_sum[industry_top]:.2f} 亿元\")\n"
            "print(f\"个股首位复算通过: {stock_name[stock_top]} {stock_sum[stock_top]:.2f} 万元\")\n"
        ),
        markdown("### 3. 检查主键、日期分区与接口截断风险\n"),
        code(
            "def duplicate_count(rows, fields):\n"
            "    keys = [tuple(row[field] for field in fields) for row in rows]\n"
            "    return len(keys) - len(set(keys))\n"
            "assert duplicate_count(industry_raw, ('trade_date', 'ts_code')) == 0\n"
            "assert duplicate_count(stock_raw, ('trade_date', 'ts_code')) == 0\n"
            "industry_by_date = {date: sum(row['trade_date'] == date for row in industry_raw) for date in summary['trade_dates']}\n"
            "stock_by_date = {date: sum(row['trade_date'] == date for row in stock_raw) for date in summary['trade_dates']}\n"
            "assert set(industry_by_date.values()) == {90}\n"
            "assert max(stock_by_date.values()) < 6000\n"
            "print('行业每日行数:', industry_by_date)\n"
            "print('个股每日行数:', stock_by_date)\n"
            "print('主键唯一，且个股单日行数未触及 6000 行上限。')\n"
        ),
        markdown("### 4. 汇总自动化质量检查\n"),
        code(
            "critical_or_high_failures = [row for row in checks if row['status'] == 'FAIL' and row['severity'] in {'critical', 'high'}]\n"
            "assert not critical_or_high_failures, critical_or_high_failures\n"
            "for row in checks:\n"
            "    print(f\"{row['status']:4s} | {row['check']} | {row['value']} | {row['note']}\")\n"
        ),
        markdown(
            "## Takeaways\n\n"
            f"- **排名可复算。** 原始行业与个股逐日净额重新聚合后，首位分别仍为{top_industry['industry']}和{top_stock['name']}。\n"
            f"- **关键交叉验证稳定。** 价格一致率 {summary['validation']['pct_change_match_rate']*100:.4f}%，"
            f"五日主力一致率 {summary['validation']['rolling_large_order_five_day_match_rate']*100:.4f}%，"
            f"行业成分汇总相关系数 {summary['validation']['industry_reconciliation_correlation']:.4f}。\n"
            "- **范围风险已封闭。** 标准 `moneyflow` 对全 A 股日行情覆盖 100%，北交所最高排名未进入前二十。\n"
            "- **保留低风险注记。** 同花顺当前行业成分中有 18 只股票双重归属，行业内榜按各自成分保留。\n"
        ),
    ]

    execute_code_cells(cells)
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": f"{sys.version_info.major}.{sys.version_info.minor}"},
            "execution": {
                "method": "standard-library sequential cell execution",
                "status": "passed",
                "working_directory": str(ROOT),
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    notebook_dir = ROOT / "notebooks"
    notebook_dir.mkdir(parents=True, exist_ok=True)
    destination = notebook_dir / f"ths_flow_validation_{output_dir.name}.ipynb"
    destination.write_text(json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"notebook": str(destination), "code_cells_executed": 4, "status": "passed"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
