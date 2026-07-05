#!/usr/bin/env python3
"""從 MOPS 回填近 N 天或指定區間的上市/上櫃重大訊息歷史資料"""

import argparse
import asyncio
import sys
from datetime import date, timedelta

from app.database import init_db
from app.mops_history import backfill_announcements


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="MOPS 重大訊息歷史回填")
    parser.add_argument("--days", type=int, default=365, help="回填天數（預設 365）")
    parser.add_argument("--from", dest="date_from", help="起始日 YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="結束日 YYYY-MM-DD")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.35,
        help="每次請求間隔秒數（預設 0.35）",
    )
    args = parser.parse_args()

    today = date.today()
    if args.date_from and args.date_to:
        date_from = _parse_date(args.date_from)
        date_to = _parse_date(args.date_to)
    elif args.date_from:
        date_from = _parse_date(args.date_from)
        date_to = today
    else:
        date_to = today
        date_from = today - timedelta(days=max(args.days, 1) - 1)

    init_db()
    print(f"開始回填：{date_from.isoformat()} ~ {date_to.isoformat()}")
    result = asyncio.run(
        backfill_announcements(
            date_from,
            date_to,
            request_delay=args.delay,
        )
    )

    print(f"狀態: {result['status']}")
    print(f"天數: {result['days']}")
    print(f"抓取: {result['fetched']} 筆")
    print(f"新增: {result['inserted']} 筆")
    if result.get("message"):
        print(f"訊息: {result['message']}")

    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
