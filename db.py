"""SQLite 存储封装。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List

from config import CONFIG
from models import VideoMetrics


class SQLiteStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or CONFIG.db_path
        self.init_db()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS video_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bvid TEXT NOT NULL,
                    title TEXT,
                    author TEXT,
                    view INTEGER,
                    like_count INTEGER,
                    coin INTEGER,
                    favorite INTEGER,
                    reply INTEGER,
                    share INTEGER,
                    duration INTEGER,
                    avg_view_duration REAL,
                    like_rate REAL,
                    completion_rate REAL,
                    competition_score REAL,
                    source TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

    def save_video_metrics(self, metrics: VideoMetrics) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO video_metrics (
                    bvid, title, author, view, like_count, coin, favorite, reply, share,
                    duration, avg_view_duration, like_rate, completion_rate,
                    competition_score, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metrics.bvid,
                    metrics.title,
                    metrics.author,
                    metrics.view,
                    metrics.like,
                    metrics.coin,
                    metrics.favorite,
                    metrics.reply,
                    metrics.share,
                    metrics.duration,
                    metrics.avg_view_duration,
                    metrics.like_rate,
                    metrics.completion_rate,
                    metrics.competition_score,
                    metrics.source,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def get_history(self, bvid: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM video_metrics
                WHERE bvid = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (bvid, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_snapshot(self, bvid: str) -> Dict[str, Any] | None:
        history = self.get_history(bvid, limit=1)
        return history[0] if history else None
