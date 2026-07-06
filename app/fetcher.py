import hashlib
import logging
from typing import Any

import httpx

from app.config import IS_VERCEL, TPEX_API_URL, TWSE_API_URL
from app.database import insert_announcements, log_sync
from app.mops_history import SYNC_GAP_MAX_DAYS, sync_missing_days
from app.notifier import notify_new_announcements
from app.utils import normalize_text, roc_time_to_time, roc_to_date

logger = logging.getLogger(__name__)


def _content_hash(
    market: str,
    stock_code: str,
    announce_date,
    announce_time,
    subject: str,
) -> str:
    raw = f"{market}|{stock_code}|{announce_date}|{announce_time}|{subject}"
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_twse_record(raw: dict) -> dict:
    subject = normalize_text(raw.get("主旨 ") or raw.get("主旨"))
    announce_date = roc_to_date(raw.get("發言日期"))
    announce_time = roc_time_to_time(raw.get("發言時間"))
    stock_code = (raw.get("公司代號") or "").strip()

    return {
        "market": "TWSE",
        "report_date": roc_to_date(raw.get("出表日期")),
        "announce_date": announce_date,
        "announce_time": announce_time,
        "stock_code": stock_code,
        "company_name": normalize_text(raw.get("公司名稱")),
        "subject": subject,
        "clause": normalize_text(raw.get("符合條款")),
        "event_date": roc_to_date(raw.get("事實發生日")),
        "description": normalize_text(raw.get("說明")),
        "content_hash": _content_hash(
            "TWSE", stock_code, announce_date, announce_time, subject
        ),
    }


def parse_otc_record(raw: dict) -> dict:
    subject = normalize_text(raw.get("主旨"))
    announce_date = roc_to_date(raw.get("發言日期"))
    announce_time = roc_time_to_time(raw.get("發言時間"))
    stock_code = (raw.get("SecuritiesCompanyCode") or "").strip()

    return {
        "market": "OTC",
        "report_date": roc_to_date(raw.get("Date")),
        "announce_date": announce_date,
        "announce_time": announce_time,
        "stock_code": stock_code,
        "company_name": normalize_text(raw.get("CompanyName")),
        "subject": subject,
        "clause": normalize_text(raw.get("符合條款")),
        "event_date": roc_to_date(raw.get("事實發生日")),
        "description": normalize_text(raw.get("說明")),
        "content_hash": _content_hash(
            "OTC", stock_code, announce_date, announce_time, subject
        ),
    }


async def _fetch_json(url: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, list):
        raise ValueError(f"API 回傳格式非預期的陣列: {url}")

    return data


async def fetch_twse() -> list[dict]:
    records = []
    for item in await _fetch_json(TWSE_API_URL):
        if not item.get("公司代號"):
            continue
        records.append(parse_twse_record(item))
    return records


async def fetch_otc() -> list[dict]:
    records = []
    for item in await _fetch_json(TPEX_API_URL):
        if not item.get("SecuritiesCompanyCode"):
            continue
        records.append(parse_otc_record(item))
    return records


async def sync_announcements() -> dict:
    errors: list[str] = []

    gap_result: dict[str, Any] = {}
    try:
        gap_result = await sync_missing_days(
            max_days=SYNC_GAP_MAX_DAYS if IS_VERCEL else None,
        )
        if gap_result.get("errors"):
            errors.extend(gap_result["errors"][:3])
    except Exception as exc:
        logger.exception("MOPS 缺口補齊失敗")
        errors.append(f"缺口補齊: {exc}")
        gap_result = {
            "status": "error",
            "days": 0,
            "fetched": 0,
            "inserted": 0,
            "twse": {"inserted": 0},
            "otc": {"inserted": 0},
        }

    twse_records: list[dict] = []
    otc_records: list[dict] = []

    try:
        twse_records = await fetch_twse()
    except Exception as exc:
        logger.exception("上市同步失敗")
        errors.append(f"上市: {exc}")

    try:
        otc_records = await fetch_otc()
    except Exception as exc:
        logger.exception("上櫃同步失敗")
        errors.append(f"上櫃: {exc}")

    all_records = twse_records + otc_records
    openapi_inserted: list[dict] = []

    if all_records:
        openapi_inserted = insert_announcements(all_records)

    openapi_twse = sum(1 for r in openapi_inserted if r["market"] == "TWSE")
    openapi_otc = sum(1 for r in openapi_inserted if r["market"] == "OTC")

    gap_inserted = gap_result.get("inserted", 0)
    gap_fetched = gap_result.get("fetched", 0)
    total_fetched = gap_fetched + len(twse_records) + len(otc_records)
    total_inserted = gap_inserted + len(openapi_inserted)

    twse_inserted = gap_result.get("twse", {}).get("inserted", 0) + openapi_twse
    otc_inserted = gap_result.get("otc", {}).get("inserted", 0) + openapi_otc

    notify_result: dict[str, Any] = {}
    if openapi_inserted:
        try:
            notify_result = await notify_new_announcements(openapi_inserted)
        except Exception as exc:
            logger.exception("通知發送失敗")
            errors.append(f"通知: {exc}")

    has_data = total_fetched > 0 or gap_result.get("status") == "skipped"
    if errors and not has_data:
        log_sync(0, 0, "error", "; ".join(errors))
        return {
            "status": "error",
            "fetched": 0,
            "inserted": 0,
            "gap": gap_result,
            "twse": {"fetched": 0, "inserted": 0},
            "otc": {"fetched": 0, "inserted": 0},
            "notified": notify_result,
            "message": "; ".join(errors),
        }

    status = "partial" if errors else "success"
    gap_days = gap_result.get("days", 0)
    if gap_days:
        message = gap_result.get("message") or f"補齊 {gap_days} 天"
    elif gap_result.get("status") == "skipped":
        message = "資料已是最新"
    else:
        message = ""
    if errors:
        message = "; ".join(filter(None, [message, "; ".join(errors)]))

    log_sync(total_fetched, total_inserted, status, message or "同步完成")

    logger.info(
        "同步完成：缺口 %d 天 +%d，OpenAPI 上市 %d/%d，上櫃 %d/%d，合計新增 %d 筆",
        gap_days,
        gap_inserted,
        len(twse_records),
        openapi_twse,
        len(otc_records),
        openapi_otc,
        total_inserted,
    )

    return {
        "status": status,
        "fetched": total_fetched,
        "inserted": total_inserted,
        "gap": gap_result,
        "twse": {
            "fetched": len(twse_records),
            "inserted": twse_inserted,
        },
        "otc": {
            "fetched": len(otc_records),
            "inserted": otc_inserted,
        },
        "notified": notify_result,
        "message": message,
    }
