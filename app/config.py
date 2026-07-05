import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "announcements.db"

# Vercel 部署請設定 PostgreSQL 連線（Neon / Supabase / Vercel Postgres）
DATABASE_URL = os.getenv("DATABASE_URL", "")

TWSE_API_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
TPEX_API_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"

DEFAULT_PORT = 8765
EXPORT_MAX_ROWS = 10000

# Vercel Cron 驗證（部署時必填）
CRON_SECRET = os.getenv("CRON_SECRET", "")

# Email 通知（選填）
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.getenv("EMAIL_TO", "")

# Telegram 通知
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MARKET_LABELS = {
    "TWSE": "上市",
    "OTC": "上櫃",
}

IS_VERCEL = os.getenv("VERCEL") == "1"
