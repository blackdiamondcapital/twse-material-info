import asyncio
import hashlib
import logging
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import MOPS_BASE_URL, MOPS_REQUEST_DELAY
from app.database import insert_announcements, log_sync
from app.utils import normalize_text, parse_colon_time, parse_slash_roc_date

logger = logging.getLogger(__name__)

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


async def backfill_announcements(
    date_from: date,
    date_to: date,
    *,
    request_delay: float = MOPS_REQUEST_DELAY,
) -> dict[str, Any]:
    if date_from > date_to:
        raise ValueError("date_from 不可晚於 date_to")

    total_days = (date_to - date_from).days + 1
    fetched = 0
    inserted_total = 0
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
                    inserted = insert_announcements(pending_records)
                    inserted_total += len(inserted)
                    pending_records.clear()

                if day_index % 10 == 0 or day_index == total_days:
                    logger.info(
                        "回填進度 %d/%d 天 (%s)，累計抓取 %d 筆",
                        day_index,
                        total_days,
                        current.isoformat(),
                        fetched,
                    )
            except Exception as exc:
                logger.exception("回填失敗：%s", current)
                errors.append(f"{current.isoformat()}: {exc}")

            current += timedelta(days=1)
            if current <= date_to:
                await asyncio.sleep(request_delay)

    if pending_records:
        inserted = insert_announcements(pending_records)
        inserted_total += len(inserted)

    status = "error" if errors and inserted_total == 0 else ("partial" if errors else "success")
    message = "; ".join(errors[:5])
    if len(errors) > 5:
        message += f" ...共 {len(errors)} 個錯誤"

    log_sync(fetched, inserted_total, status, message or "MOPS 歷史回填")

    return {
        "status": status,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": total_days,
        "fetched": fetched,
        "inserted": inserted_total,
        "message": message,
    }
