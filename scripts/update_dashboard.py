#!/usr/bin/env python3
"""一键更新数据、构建统一网页并执行独立验证。"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("运行：" + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def latest_snapshot(output_root: Path) -> Path:
    candidates = sorted(
        path for path in output_root.iterdir()
        if path.is_dir() and len(path.name) == 8 and path.name.isdigit() and (path / "results/summary.json").exists()
    )
    if not candidates:
        raise RuntimeError("outputs 下没有可用数据快照")
    return candidates[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d"))
    parser.add_argument("--skip-data", action="store_true", help="复用最新快照，仅重建和验证网页")
    parser.add_argument("--flow-days", default="1,5,10", help="资金流窗口，交易日数，逗号分隔")
    parser.add_argument("--rps-days", default="10,20", help="RPS窗口，交易日数，逗号分隔")
    parser.add_argument("--market-cap-floor", type=float, default=200.0, help="比例榜最低总市值，亿元")
    parser.add_argument("--output-root", default="outputs", help="快照输出根目录")
    args = parser.parse_args()
    output_root = Path(args.output_root)
    if not output_root.is_absolute():
        output_root = (ROOT / output_root).resolve()

    if not args.skip_data:
        run([
            sys.executable, "scripts/market_flow_dashboard.py",
            "--as-of", args.as_of,
            "--flow-days", args.flow_days,
            "--rps-days", args.rps_days,
            "--market-cap-floor", str(args.market_cap_floor),
            "--output-root", str(output_root),
        ])
    snapshot = latest_snapshot(output_root)
    run([sys.executable, "scripts/render_dashboard.py", str(snapshot)])
    run([sys.executable, "scripts/validate_market_dashboard.py", str(snapshot)])

    latest_html = output_root / "latest.html"
    shutil.copy2(snapshot / "report.html", latest_html)
    summary = json.loads((snapshot / "results/summary.json").read_text(encoding="utf-8"))
    receipt = {
        "snapshot": snapshot.name,
        "report": str((snapshot / "report.html").resolve()),
        "latest_alias": str(latest_html.resolve()),
        "independent_validation": str((snapshot / "results/independent_validation.json").resolve()),
        "flow_windows": summary.get("flow_windows"),
        "rps_windows": summary.get("rps_windows"),
        "market_cap_floor_yi": summary.get("market_cap_floor_yi"),
        "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }
    (output_root / "latest.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
