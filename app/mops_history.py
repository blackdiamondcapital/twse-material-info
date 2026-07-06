import asyncio
import hashlib
import logging
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import IS_VERCEL, MOPS_BASE_URL, MOPS_REQUEST_DELAY
from app.database import get_latest_report_date, insert_announcements, log_sync
from app.utils import normalize_text, parse_colon_time, parse_slash_roc_date

logger = logging.getLogger(__name__)

# Vercel 單次同步最多補齊天數（避免逾時）
SYNC_GAP_MAX_DAYS = 14

MARKET_KIND_MAP = {
    "sii": "TWSE",
    "otc": "OTC",
    "rotc": "OTC",
    "pub": "OTC",
}


def _content_hash(
    market: str,
    stock_code: str,
    announce_date: date | None,
    announce_time,
    subject: str,
) -> str:
    raw = f"{market}|{stock_code}|{announce_date}|{announce_time}|{subject}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _row_to_record(row: list[Any], query_day: date) -> dict[str, Any] | None:
    if len(row) < 5:
        return None

    stock_code = str(row[2]).strip()
    if not stock_code:
        return None

    subject = normalize_text(str(row[4]))
    announce_date = parse_slash_roc_date(str(row[0]))
    announce_time = parse_colon_time(str(row[1]))
    company_name = normalize_text(str(row[3]))

    detail_obj = row[5] if len(row) > 5 else {}
    params = detail_obj.get("parameters", {}) if isinstance(detail_obj, dict) else {}
    market_kind = str(params.get("marketKind", "sii")).lower()
    market = MARKET_KIND_MAP.get(market_kind, "TWSE")

    return {
        "market": market,
        "report_date": query_day,
        "announce_date": announce_date,
        "announce_time": announce_time,
        "stock_code": stock_code,
        "company_name": company_name,
        "subject": subject,
        "clause": "",
        "event_date": None,
        "description": "",
        "content_hash": _content_hash(
            market, stock_code, announce_date, announce_time, subject
        ),
    }


def _count_markets(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "twse": sum(1 for r in records if r["market"] == "TWSE"),
        "otc": sum(1 for r in records if r["market"] == "OTC"),
    }


def compute_gap_range(today: date | None = None) -> tuple[date, date] | None:
    """依資料庫最新 report_date 計算需補齊的日期區間（含今日）。"""
    today = today or date.today()
    latest = get_latest_report_date()

    if latest is None:
        return today, today

    date_from = latest + timedelta(days=1)
    if date_from > today:
        return None

    return date_from, today


async def _ensure_mops_session(client: httpx.AsyncClient) -> None:
    await client.get(f"{MOPS_BASE_URL}/mops")


async def _fetch_day_list(
    client: httpx.AsyncClient,
    query_day: date,
) -> list[dict[str, Any]]:
    roc_year = query_day.year - 1911
    payload = {"year": roc_year, "month": query_day.month, "day": query_day.day}

    for attempt in range(3):
        try:
            response = await client.post(
                f"{MOPS_BASE_URL}/mops/api/t05st02",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 200:
                raise ValueError(body.get("message", "MOPS 查詢失敗"))

            rows = body.get("result", {}).get("data", [])
            records: list[dict[str, Any]] = []
            for row in rows:
                record = _row_to_record(row, query_day)
                if record and record["announce_date"]:
                    records.append(record)
            return records
        except Exception as exc:
            if attempt == 2:
                raise
            logger.warning("MOPS 列表重試 %s (%s): %s", query_day, attempt + 1, exc)
            await asyncio.sleep(1.5 * (attempt + 1))

    return []


async def _fetch_day_range(
    date_from: date,
    date_to: date,
    *,
    request_delay: float = MOPS_REQUEST_DELAY,
) -> dict[str, Any]:
    total_days = (date_to - date_from).days + 1
    fetched = 0
    inserted_total = 0
    inserted_records: list[dict[str, Any]] = []
    errors: list[str] = []
    pending_records: list[dict[str, Any]] = []

    headers = {
        "User-Agent": "QuantGems-Pulse/1.0",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers=headers,
    ) as client:
        await _ensure_mops_session(client)

        current = date_from
        day_index = 0
        while current <= date_to:
            day_index += 1
            try:
                day_records = await _fetch_day_list(client, current)
                fetched += len(day_records)
                pending_records.extend(day_records)

                if len(pending_records) >= 1000:
                    batch = insert_announcements(pending_records)
                    inserted_total += len(batch)
                    inserted_records.extend(batch)
                    pending_records.clear()

                logger.info(
                    "MOPS 補齊 %d/%d 天 (%s)，抓取 %d 筆",
                    day_index,
                    total_days,
                    current.isoformat(),
                    len(day_records),
                )
            except Exception as exc:
                logger.exception("MOPS 補齊失敗：%s", current)
                errors.append(f"{current.isoformat()}: {exc}")

            current += timedelta(days=1)
            if current <= date_to:
                await asyncio.sleep(request_delay)

    if pending_records:
        batch = insert_announcements(pending_records)
        inserted_total += len(batch)
        inserted_records.extend(batch)

    markets = _count_markets(inserted_records)
    status = "error" if errors and inserted_total == 0 else ("partial" if errors else "success")

    return {
        "status": status,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": total_days,
        "fetched": fetched,
        "inserted": inserted_total,
        "twse": {"inserted": markets["twse"]},
        "otc": {"inserted": markets["otc"]},
        "message": "; ".join(errors[:5]),
        "errors": errors,
    }


async def sync_missing_days(
    *,
    max_days: int | None = None,
    request_delay: float = MOPS_REQUEST_DELAY,
) -> dict[str, Any]:
    """補齊缺口日期，並一律刷新今日／昨日（盤中可能持續有新重訊）。"""
    today = date.today()
    yesterday = today - timedelta(days=1)
    dates_to_fetch: set[date] = {today, yesterday}

    gap = compute_gap_range()
    capped = False
    total_gap_days = 0

    if gap:
        date_from, date_to = gap
        total_gap_days = (date_to - date_from).days + 1
        limit = max_days if max_days is not None else (SYNC_GAP_MAX_DAYS if IS_VERCEL else None)

        if limit is not None and total_gap_days > limit:
            date_from = date_to - timedelta(days=limit - 1)
            capped = True

        current = date_from
        while current <= date_to:
            dates_to_fetch.add(current)
            current += timedelta(days=1)

    sorted_dates = sorted(dates_to_fetch)
    fetched = 0
    inserted_total = 0
    inserted_records: list[dict[str, Any]] = []
    errors: list[str] = []
    pending_records: list[dict[str, Any]] = []

    headers = {
        "User-Agent": "QuantGems-Pulse/1.0",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers=headers,
    ) as client:
        await _ensure_mops_session(client)

        for day_index, query_day in enumerate(sorted_dates, start=1):
            try:
                day_records = await _fetch_day_list(client, query_day)
                fetched += len(day_records)
                pending_records.extend(day_records)

                if len(pending_records) >= 1000:
                    batch = insert_announcements(pending_records)
                    inserted_total += len(batch)
                    inserted_records.extend(batch)
                    pending_records.clear()

                logger.info(
                    "MOPS 同步 %d/%d 天 (%s)，抓取 %d 筆",
                    day_index,
                    len(sorted_dates),
                    query_day.isoformat(),
                    len(day_records),
                )
            except Exception as exc:
                logger.exception("MOPS 同步失敗：%s", query_day)
                errors.append(f"{query_day.isoformat()}: {exc}")

            if day_index < len(sorted_dates):
                await asyncio.sleep(request_delay)

    if pending_records:
        batch = insert_announcements(pending_records)
        inserted_total += len(batch)
        inserted_records.extend(batch)

    markets = _count_markets(inserted_records)
    status = "error" if errors and inserted_total == 0 else ("partial" if errors else "success")

    gap_days = total_gap_days
    if inserted_total > 0:
        message = f"更新 {len(sorted_dates)} 天，新增 {inserted_total} 筆"
    elif gap_days == 0:
        message = "已刷新今日資料，無新重訊"
    else:
        message = f"補齊 {gap_days} 天，無新重訊"

    if capped:
        message += f"（尚有 {total_gap_days - gap_days} 天缺口，請再同步）"

    return {
        "status": status,
        "skipped": False,
        "date_from": sorted_dates[0].isoformat(),
        "date_to": sorted_dates[-1].isoformat(),
        "days": len(sorted_dates),
        "gap_days": gap_days,
        "fetched": fetched,
        "inserted": inserted_total,
        "twse": {"inserted": markets["twse"]},
        "otc": {"inserted": markets["otc"]},
        "capped": capped,
        "total_gap_days": total_gap_days,
        "message": message,
        "errors": errors,
    }


async def backfill_announcements(
    date_from: date,
    date_to: date,
    *,
    request_delay: float = MOPS_REQUEST_DELAY,
) -> dict[str, Any]:
    if date_from > date_to:
        raise ValueError("date_from 不可晚於 date_to")

    result = await _fetch_day_range(date_from, date_to, request_delay=request_delay)
    message = result.get("message") or "MOPS 歷史回填"
    log_sync(result["fetched"], result["inserted"], result["status"], message)

    return {
        "status": result["status"],
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": result["days"],
        "fetched": result["fetched"],
        "inserted": result["inserted"],
        "message": message,
    }
