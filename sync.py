#!/usr/bin/env python3
"""手動同步上市/上櫃重大訊息資料"""

import asyncio
import sys

from app.database import init_db
from app.fetcher import sync_announcements


async def main():
    init_db()
    result = await sync_announcements()
    print(f"狀態: {result['status']}")
    print(f"上市: 抓取 {result['twse']['fetched']} 筆，新增 {result['twse']['inserted']} 筆")
    print(f"上櫃: 抓取 {result['otc']['fetched']} 筆，新增 {result['otc']['inserted']} 筆")
    print(f"合計: 抓取 {result['fetched']} 筆，新增 {result['inserted']} 筆")
    if result.get("notified"):
        print(f"通知: {result['notified']}")
    if result.get("message"):
        print(f"訊息: {result['message']}")
    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
