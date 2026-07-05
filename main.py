import logging
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.config import (
    BASE_DIR,
    CRON_SECRET,
    EMAIL_ENABLED,
    IS_VERCEL,
    TELEGRAM_ENABLED,
)
from app.database import (
    export_announcements,
    get_announcement,
    get_latest_sync,
    get_stats,
    init_db,
    search_announcements,
)
from app.export import build_excel
from app.fetcher import sync_announcements

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not IS_VERCEL:
        result = await sync_announcements()
        logger.info("本機啟動同步：%s", result)
    yield


app = FastAPI(
    title="台股重大訊息查詢系統",
    description="上市/上櫃公司每日重大訊息查詢、通知與匯出",
    version="3.0.0",
    lifespan=lifespan,
)

public_dir = BASE_DIR / "public"


@app.get("/")
async def index():
    index_file = public_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(index_file)


@app.get("/style.css")
async def style():
    css_file = public_dir / "style.css"
    if not css_file.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(css_file, media_type="text/css")


@app.get("/app.js")
async def script():
    js_file = public_dir / "app.js"
    if not js_file.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(js_file, media_type="application/javascript")


def _verify_cron(authorization: str | None) -> None:
    if not CRON_SECRET:
        return
    if authorization != f"Bearer {CRON_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/api/announcements")
async def list_announcements(
    market: str | None = Query(None, description="TWSE / OTC / ALL"),
    stock_code: str | None = Query(None, description="公司代號"),
    company_name: str | None = Query(None, description="公司名稱"),
    keyword: str | None = Query(None, description="主旨/說明關鍵字"),
    date_from: str | None = Query(None, description="發言日期起 (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="發言日期迄 (YYYY-MM-DD)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    items, total = search_announcements(
        market=market,
        stock_code=stock_code,
        company_name=company_name,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@app.get("/api/announcements/export")
async def export_to_excel(
    market: str | None = Query(None),
    stock_code: str | None = Query(None),
    company_name: str | None = Query(None),
    keyword: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
):
    records = export_announcements(
        market=market,
        stock_code=stock_code,
        company_name=company_name,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )
    if not records:
        raise HTTPException(status_code=404, detail="查無可匯出資料")

    content = build_excel(records)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ascii_name = f"material_info_{ts}.xlsx"
    utf8_name = quote(f"重大訊息_{ts}.xlsx")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'
            )
        },
    )


@app.get("/api/announcements/{announcement_id}")
async def get_announcement_detail(announcement_id: int):
    item = get_announcement(announcement_id)
    if not item:
        raise HTTPException(status_code=404, detail="找不到該筆重大訊息")
    return item


@app.get("/api/sync")
async def cron_sync(authorization: str | None = Header(None)):
    """Vercel Cron 每小時觸發"""
    _verify_cron(authorization)
    return await sync_announcements()


@app.post("/api/sync")
async def trigger_sync():
    """手動同步"""
    return await sync_announcements()


@app.get("/api/stats")
async def stats():
    return {
        **get_stats(),
        "last_sync": get_latest_sync(),
        "notifications": {
            "email_enabled": EMAIL_ENABLED,
            "telegram_enabled": TELEGRAM_ENABLED,
        },
        "platform": "vercel" if IS_VERCEL else "local",
    }
