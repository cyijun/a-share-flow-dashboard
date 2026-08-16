#!/usr/bin/env python3
"""Tushare 接口小规模冒烟测试；永不打印 token。"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def call(api_name: str, params: dict[str, str], fields: str = "") -> list[dict]:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")
    payload = {"api_name": api_name, "token": token, "params": params, "fields": fields}
    request = urllib.request.Request(
        "https://api.tushare.pro",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        result = json.load(response)
    if result.get("code") != 0:
        raise RuntimeError(f"{api_name}: {result.get('msg') or result.get('code')}")
    data = result.get("data") or {}
    names = data.get("fields") or []
    return [dict(zip(names, item)) for item in data.get("items") or []]


def main() -> int:
    load_dotenv(ROOT / ".env")
    cal = call(
        "trade_cal",
        {"exchange": "SSE", "start_date": "20260727", "end_date": "20260809", "is_open": "1"},
        "exchange,cal_date,is_open,pretrade_date",
    )
    dates = sorted(row["cal_date"] for row in cal)[-5:]
    latest = dates[-1]
    industry = call("moneyflow_ind_ths", {"trade_date": latest})
    stocks = call("moneyflow_ths", {"trade_date": latest})
    ths_industry = call("ths_index", {"exchange": "A", "type": "I"})
    sample_code = industry[0]["ts_code"] if industry else ""
    members = call("ths_member", {"ts_code": sample_code}) if sample_code else []
    daily = call("daily", {"trade_date": latest}, "ts_code,trade_date,close,amount")
    stock_basic = call(
        "stock_basic",
        {"exchange": "", "list_status": "L"},
        "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date",
    )
    print(json.dumps({
        "dates": dates,
        "latest": latest,
        "industry_rows": len(industry),
        "industry_fields": sorted(industry[0]) if industry else [],
        "industry_sample": {k: industry[0].get(k) for k in ("ts_code", "industry", "company_num")} if industry else {},
        "moneyflow_ths_rows": len(stocks),
        "moneyflow_ths_fields": sorted(stocks[0]) if stocks else [],
        "ths_index_industry_rows": len(ths_industry),
        "ths_index_fields": sorted(ths_industry[0]) if ths_industry else [],
        "sample_industry_member_rows": len(members),
        "sample_industry_member_fields": sorted(members[0]) if members else [],
        "daily_rows": len(daily),
        "listed_stock_rows": len(stock_basic),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
