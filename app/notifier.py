import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx

from app.config import (
    EMAIL_ENABLED,
    EMAIL_FROM,
    EMAIL_TO,
    MARKET_LABELS,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ENABLED,
)

logger = logging.getLogger(__name__)


def _format_record(rec: dict) -> str:
    market = MARKET_LABELS.get(rec.get("market", ""), rec.get("market", ""))
    subject = (rec.get("subject") or "")[:120]
    desc = (rec.get("description") or "")[:200]
    announce_date = rec.get("announce_date") or "-"
    announce_time = rec.get("announce_time") or ""
    return (
        f"[{market}] {rec.get('stock_code')} {rec.get('company_name')}\n"
        f"發言：{announce_date} {announce_time}\n"
        f"主旨：{subject}\n"
        f"說明：{desc}{'...' if len(rec.get('description') or '') > 200 else ''}"
    )


def _build_message(records: list[dict]) -> tuple[str, str]:
    title = f"重大訊息新增 {len(records)} 筆"
    lines = [_format_record(rec) for rec in records[:20]]
    body = f"{title}\n\n" + "\n\n---\n\n".join(lines)
    if len(records) > 20:
        body += f"\n\n... 另有 {len(records) - 20} 筆未顯示"
    return title, body


def send_email(records: list[dict]) -> bool:
    if not EMAIL_ENABLED or not EMAIL_TO or not SMTP_USER:
        return False

    title, body = _build_message(records)
    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = title
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    logger.info("Email 通知已發送至 %s", EMAIL_TO)
    return True


async def send_telegram(records: list[dict]) -> bool:
    if not TELEGRAM_ENABLED or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    title, body = _build_message(records)
    text = f"<b>{title}</b>\n\n{body}"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text[:4000],
                "parse_mode": "HTML",
            },
        )
        response.raise_for_status()

    logger.info("Telegram 通知已發送")
    return True


async def notify_new_announcements(records: list[dict]) -> dict[str, Any]:
    if not records:
        return {"email": False, "telegram": False}

    email_sent = False
    telegram_sent = False

    if EMAIL_ENABLED:
        try:
            email_sent = send_email(records)
        except Exception:
            logger.exception("Email 發送失敗")
            raise

    if TELEGRAM_ENABLED:
        telegram_sent = await send_telegram(records)

    return {"email": email_sent, "telegram": telegram_sent}
