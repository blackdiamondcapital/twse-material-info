import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time
from typing import Any

from app.config import DATABASE_URL, DATA_DIR, DB_PATH, EXPORT_MAX_ROWS

USE_POSTGRES = bool(DATABASE_URL)


def _content_hash(
    market: str,
    stock_code: str,
    announce_date: str | None,
    announce_time: str | None,
    subject: str | None,
) -> str:
    raw = f"{market}|{stock_code}|{announce_date}|{announce_time}|{subject or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


def init_db() -> None:
    if USE_POSTGRES:
        _init_postgres()
    else:
        _init_sqlite()


def _init_postgres() -> None:
    import psycopg2

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS announcements (
                    id SERIAL PRIMARY KEY,
                    market TEXT NOT NULL DEFAULT 'TWSE',
                    report_date TEXT,
                    announce_date TEXT NOT NULL,
                    announce_time TEXT,
                    stock_code TEXT NOT NULL,
                    company_name TEXT,
                    subject TEXT,
                    clause TEXT,
                    event_date TEXT,
                    description TEXT,
                    content_hash TEXT NOT NULL UNIQUE,
                    synced_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_logs (
                    id SERIAL PRIMARY KEY,
                    synced_at TEXT NOT NULL,
                    fetched_count INTEGER NOT NULL,
                    inserted_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ann_market ON announcements(market)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ann_stock_code ON announcements(stock_code)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ann_announce_date ON announcements(announce_date DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ann_report_date ON announcements(report_date DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ann_company_name ON announcements(company_name)"
            )


def _init_sqlite() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL DEFAULT 'TWSE',
                report_date TEXT,
                announce_date TEXT NOT NULL,
                announce_time TEXT,
                stock_code TEXT NOT NULL,
                company_name TEXT,
                subject TEXT,
                clause TEXT,
                event_date TEXT,
                description TEXT,
                content_hash TEXT NOT NULL UNIQUE,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                synced_at TEXT NOT NULL,
                fetched_count INTEGER NOT NULL,
                inserted_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                message TEXT
            );
            """
        )
        _migrate_sqlite(conn)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_ann_market ON announcements(market);
            CREATE INDEX IF NOT EXISTS idx_ann_stock_code ON announcements(stock_code);
            CREATE INDEX IF NOT EXISTS idx_ann_announce_date ON announcements(announce_date DESC);
            CREATE INDEX IF NOT EXISTS idx_ann_report_date ON announcements(report_date DESC);
            CREATE INDEX IF NOT EXISTS idx_ann_company_name ON announcements(company_name);
            """
        )


def _migrate_sqlite(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(announcements)")}
    if "market" not in columns:
        conn.execute(
            "ALTER TABLE announcements ADD COLUMN market TEXT NOT NULL DEFAULT 'TWSE'"
        )

    rows = conn.execute(
        """
        SELECT id, market, stock_code, announce_date, announce_time, subject, content_hash
        FROM announcements
        """
    ).fetchall()

    for row in rows:
        new_hash = _content_hash(
            row["market"],
            row["stock_code"],
            row["announce_date"],
            row["announce_time"],
            row["subject"],
        )
        if new_hash == row["content_hash"]:
            continue

        duplicate = conn.execute(
            "SELECT id FROM announcements WHERE content_hash = ? AND id != ?",
            (new_hash, row["id"]),
        ).fetchone()
        if duplicate:
            conn.execute("DELETE FROM announcements WHERE id = ?", (row["id"],))
        else:
            conn.execute(
                "UPDATE announcements SET content_hash = ? WHERE id = ?",
                (new_hash, row["id"]),
            )

    conn.execute(
        """
        DELETE FROM announcements
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM announcements
            GROUP BY market, stock_code, announce_date, announce_time, subject
        )
        """
    )


@contextmanager
def get_connection():
    if USE_POSTGRES:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


def _serialize_date(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _serialize_time(t: time | None) -> str | None:
    return t.isoformat() if t else None


def insert_announcements(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not records:
        return []

    now = datetime.now().isoformat()
    inserted: list[dict[str, Any]] = []

    with get_connection() as conn:
        for rec in records:
            values = (
                rec["market"],
                _serialize_date(rec["report_date"]),
                _serialize_date(rec["announce_date"]),
                _serialize_time(rec["announce_time"]),
                rec["stock_code"],
                rec["company_name"],
                rec["subject"],
                rec["clause"],
                _serialize_date(rec["event_date"]),
                rec["description"],
                rec["content_hash"],
                now,
            )

            if USE_POSTGRES:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO announcements (
                            market, report_date, announce_date, announce_time,
                            stock_code, company_name, subject, clause,
                            event_date, description, content_hash, synced_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (content_hash) DO NOTHING
                        RETURNING id
                        """,
                        values,
                    )
                    if cur.fetchone():
                        inserted.append(rec)
            else:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO announcements (
                        market, report_date, announce_date, announce_time,
                        stock_code, company_name, subject, clause,
                        event_date, description, content_hash, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                if cursor.rowcount > 0:
                    inserted.append(rec)

    return inserted


def log_sync(fetched: int, inserted: int, status: str, message: str = "") -> None:
    params = (datetime.now().isoformat(), fetched, inserted, status, message)
    with get_connection() as conn:
        if USE_POSTGRES:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sync_logs (synced_at, fetched_count, inserted_count, status, message)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    params,
                )
        else:
            conn.execute(
                """
                INSERT INTO sync_logs (synced_at, fetched_count, inserted_count, status, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                params,
            )


def get_latest_sync() -> dict[str, Any] | None:
    sql = """
        SELECT synced_at, fetched_count, inserted_count, status, message
        FROM sync_logs
        ORDER BY id DESC
        LIMIT 1
    """
    with get_connection() as conn:
        if USE_POSTGRES:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
        else:
            row = conn.execute(sql).fetchone()
    return _row_to_dict(row) if row else None


def _build_conditions(
    *,
    market: str | None = None,
    stock_code: str | None = None,
    company_name: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[str, list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    ph = "%s" if USE_POSTGRES else "?"

    if market and market.upper() != "ALL":
        conditions.append(f"market = {ph}")
        params.append(market.upper())

    if stock_code:
        conditions.append(f"stock_code = {ph}")
        params.append(stock_code.strip())

    if company_name:
        conditions.append(f"company_name LIKE {ph}")
        params.append(f"%{company_name.strip()}%")

    if keyword:
        conditions.append(f"(subject LIKE {ph} OR description LIKE {ph})")
        kw = f"%{keyword.strip()}%"
        params.extend([kw, kw])

    if date_from:
        conditions.append(f"announce_date >= {ph}")
        params.append(date_from)

    if date_to:
        conditions.append(f"announce_date <= {ph}")
        params.append(date_to)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where_clause, params


def _fetch_all(conn: Any, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [_row_to_dict(row) for row in cur.fetchall()]
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def _fetch_one(conn: Any, sql: str, params: list[Any]) -> Any:
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    return conn.execute(sql, params).fetchone()


def search_announcements(
    *,
    market: str | None = None,
    stock_code: str | None = None,
    company_name: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    where_clause, params = _build_conditions(
        market=market,
        stock_code=stock_code,
        company_name=company_name,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )
    offset = (page - 1) * page_size
    ph = "%s" if USE_POSTGRES else "?"

    with get_connection() as conn:
        count_row = _fetch_one(
            conn,
            f"SELECT COUNT(*) AS total FROM announcements {where_clause}",
            params,
        )
        total = count_row["total"] if count_row else 0

        rows = _fetch_all(
            conn,
            f"""
            SELECT *
            FROM announcements
            {where_clause}
            ORDER BY announce_date DESC, announce_time DESC, id DESC
            LIMIT {ph} OFFSET {ph}
            """,
            [*params, page_size, offset],
        )

    return rows, total


def export_announcements(
    *,
    market: str | None = None,
    stock_code: str | None = None,
    company_name: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    where_clause, params = _build_conditions(
        market=market,
        stock_code=stock_code,
        company_name=company_name,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )
    ph = "%s" if USE_POSTGRES else "?"

    with get_connection() as conn:
        return _fetch_all(
            conn,
            f"""
            SELECT *
            FROM announcements
            {where_clause}
            ORDER BY announce_date DESC, announce_time DESC, id DESC
            LIMIT {ph}
            """,
            [*params, EXPORT_MAX_ROWS],
        )


def get_announcement(announcement_id: int) -> dict[str, Any] | None:
    ph = "%s" if USE_POSTGRES else "?"
    with get_connection() as conn:
        row = _fetch_one(
            conn,
            f"SELECT * FROM announcements WHERE id = {ph}",
            [announcement_id],
        )
    return _row_to_dict(row) if row else None


def get_latest_report_date() -> date | None:
    sql = "SELECT MAX(report_date) AS v FROM announcements"
    with get_connection() as conn:
        row = _fetch_one(conn, sql, [])
    if not row or not row["v"]:
        return None
    value = row["v"]
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def get_stats() -> dict[str, Any]:
    queries = {
        "total": "SELECT COUNT(*) AS v FROM announcements",
        "companies": "SELECT COUNT(DISTINCT stock_code) AS v FROM announcements",
        "latest_date": "SELECT MAX(announce_date) AS v FROM announcements",
        "today_count": """
            SELECT COUNT(*) AS v FROM announcements
            WHERE report_date = (SELECT MAX(report_date) FROM announcements)
        """,
        "twse_count": "SELECT COUNT(*) AS v FROM announcements WHERE market = 'TWSE'",
        "otc_count": "SELECT COUNT(*) AS v FROM announcements WHERE market = 'OTC'",
    }

    results: dict[str, Any] = {}
    with get_connection() as conn:
        for key, sql in queries.items():
            row = _fetch_one(conn, sql, [])
            results[key] = row["v"] if row else 0

    return {
        "total": results["total"],
        "companies": results["companies"],
        "twse_count": results["twse_count"],
        "otc_count": results["otc_count"],
        "latest_announce_date": results["latest_date"],
        "latest_report_count": results["today_count"],
        "storage": "postgres" if USE_POSTGRES else "sqlite",
    }
