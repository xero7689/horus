import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from horus.models import ScrapedItem, ScrapedPage

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS items (
    id          TEXT NOT NULL,
    site_id     TEXT NOT NULL,
    url         TEXT NOT NULL,
    text        TEXT,
    author_id   TEXT,
    author_name TEXT,
    timestamp   TEXT NOT NULL,
    extra       TEXT NOT NULL DEFAULT '{}',
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (site_id, id)
);

CREATE INDEX IF NOT EXISTS idx_items_site_timestamp
    ON items(site_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_items_author_timestamp
    ON items(site_id, author_name, timestamp DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    text,
    content='items',
    content_rowid='rowid',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, text) VALUES('delete', old.rowid, old.text);
    INSERT INTO items_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;

CREATE TABLE IF NOT EXISTS crawl_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id     TEXT NOT NULL,
    url         TEXT NOT NULL,
    items_found INTEGER NOT NULL DEFAULT 0,
    items_new   INTEGER NOT NULL DEFAULT 0,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pages (
    url         TEXT PRIMARY KEY,
    site_id     TEXT NOT NULL,
    title       TEXT,
    markdown    TEXT NOT NULL,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pages_site_id ON pages(site_id);
"""


def _row_to_page(row: sqlite3.Row) -> ScrapedPage:
    return ScrapedPage(
        url=row["url"],
        site_id=row["site_id"],
        title=row["title"],
        markdown=row["markdown"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]).replace(tzinfo=UTC),
    )


def _row_to_item(row: sqlite3.Row) -> ScrapedItem:
    return ScrapedItem(
        id=row["id"],
        site_id=row["site_id"],
        url=row["url"],
        text=row["text"],
        author_id=row["author_id"],
        author_name=row["author_name"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        extra=json.loads(row["extra"]),
    )


class HorusStorage:
    def __init__(self, db_path: Path) -> None:
        db_str = str(db_path)
        if db_str != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_str, check_same_thread=True)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def upsert_items(self, items: list[ScrapedItem]) -> int:
        """Upsert items, return count of newly inserted rows."""
        new_count = 0
        for item in items:
            existing = self._conn.execute(
                "SELECT 1 FROM items WHERE site_id = ? AND id = ?",
                (item.site_id, item.id),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """UPDATE items
                    SET text = ?, url = ?, author_id = ?, author_name = ?,
                        extra = ?, fetched_at = datetime('now')
                    WHERE site_id = ? AND id = ?""",
                    (
                        item.text,
                        item.url,
                        item.author_id,
                        item.author_name,
                        json.dumps(item.extra),
                        item.site_id,
                        item.id,
                    ),
                )
            else:
                self._conn.execute(
                    """INSERT INTO items
                    (id, site_id, url, text, author_id, author_name, timestamp, extra)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.id,
                        item.site_id,
                        item.url,
                        item.text,
                        item.author_id,
                        item.author_name,
                        item.timestamp.isoformat(),
                        json.dumps(item.extra),
                    ),
                )
                new_count += 1
        self._conn.commit()
        return new_count

    def get_items(
        self,
        *,
        site_id: str | None = None,
        author_name: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScrapedItem]:
        query = "SELECT * FROM items WHERE 1=1"
        params: list[str | int] = []
        if site_id:
            query += " AND site_id = ?"
            params.append(site_id)
        if author_name:
            query += " AND author_name = ?"
            params.append(author_name)
        if since:
            query += " AND timestamp > ?"
            params.append(since.isoformat())
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_item(row) for row in rows]

    def get_latest_timestamp(
        self,
        site_id: str,
        author_name: str | None = None,
    ) -> datetime | None:
        query = "SELECT MAX(timestamp) AS ts FROM items WHERE site_id = ?"
        params: list[str] = [site_id]
        if author_name:
            query += " AND author_name = ?"
            params.append(author_name)
        row = self._conn.execute(query, params).fetchone()
        if row and row["ts"]:
            return datetime.fromisoformat(row["ts"]).replace(tzinfo=UTC)
        return None

    def search(
        self,
        query: str,
        *,
        site_id: str | None = None,
        limit: int = 50,
    ) -> list[ScrapedItem]:
        """FTS5 full-text search. Short queries (<3 chars) fallback to LIKE."""
        use_fts = len(query) >= 3
        if use_fts:
            sql = (
                "SELECT i.* FROM items i "
                "JOIN items_fts ON i.rowid = items_fts.rowid "
                "WHERE items_fts MATCH ?"
            )
            params: list[str | int] = [query]
        else:
            sql = "SELECT i.* FROM items i WHERE i.text LIKE ?"
            params = [f"%{query}%"]

        if site_id:
            sql += " AND i.site_id = ?"
            params.append(site_id)
        sql += " ORDER BY i.timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_item(row) for row in rows]

    def log_crawl(
        self,
        site_id: str,
        url: str,
        items_found: int,
        items_new: int,
        started_at: datetime,
    ) -> None:
        self._conn.execute(
            """INSERT INTO crawl_log (site_id, url, items_found, items_new, started_at)
            VALUES (?, ?, ?, ?, ?)""",
            (site_id, url, items_found, items_new, started_at.isoformat()),
        )
        self._conn.commit()

    def get_stats(self, site_id: str | None = None) -> dict[str, Any]:
        """Return stats: total items, per-site counts, latest timestamps."""
        if site_id:
            total_row = self._conn.execute(
                "SELECT COUNT(*) AS cnt FROM items WHERE site_id = ?", (site_id,)
            ).fetchone()
            total = total_row["cnt"] if total_row else 0

            latest_row = self._conn.execute(
                "SELECT MAX(timestamp) AS ts FROM items WHERE site_id = ?", (site_id,)
            ).fetchone()
            latest = latest_row["ts"] if latest_row else None

            return {
                "total": total,
                "by_site": {site_id: total},
                "latest_by_site": {site_id: latest},
            }

        total_row = self._conn.execute("SELECT COUNT(*) AS cnt FROM items").fetchone()
        total = total_row["cnt"] if total_row else 0

        by_site: dict[str, int] = {}
        latest_by_site: dict[str, str | None] = {}
        rows = self._conn.execute(
            "SELECT site_id, COUNT(*) AS cnt, MAX(timestamp) AS ts FROM items GROUP BY site_id"
        ).fetchall()
        for row in rows:
            by_site[row["site_id"]] = row["cnt"]
            latest_by_site[row["site_id"]] = row["ts"]

        return {
            "total": total,
            "by_site": by_site,
            "latest_by_site": latest_by_site,
        }

    def upsert_page(self, page: ScrapedPage) -> bool:
        """Insert or replace a page. Returns True if it was newly inserted."""
        existing = self._conn.execute(
            "SELECT 1 FROM pages WHERE url = ?", (page.url,)
        ).fetchone()
        self._conn.execute(
            """INSERT OR REPLACE INTO pages (url, site_id, title, markdown, fetched_at)
            VALUES (?, ?, ?, ?, ?)""",
            (
                page.url,
                page.site_id,
                page.title,
                page.markdown,
                page.fetched_at.isoformat(),
            ),
        )
        self._conn.commit()
        return existing is None

    def get_pages(
        self,
        *,
        site_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScrapedPage]:
        query = "SELECT * FROM pages WHERE 1=1"
        params: list[str | int] = []
        if site_id:
            query += " AND site_id = ?"
            params.append(site_id)
        query += " ORDER BY fetched_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_page(row) for row in rows]

    def get_page(self, url: str) -> ScrapedPage | None:
        row = self._conn.execute(
            "SELECT * FROM pages WHERE url = ?", (url,)
        ).fetchone()
        return _row_to_page(row) if row else None

    def close(self) -> None:
        self._conn.close()
