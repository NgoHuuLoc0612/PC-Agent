"""
SQLite database service for PC Agent — audit logs, schedules, settings.
"""

import asyncio
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.config import Config
from utils.logger import setup_logger

logger = setup_logger("database")


class Database:
    def __init__(self, db_path: str = Config.DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_id     TEXT NOT NULL,
                    username    TEXT NOT NULL,
                    guild_id    TEXT,
                    command     TEXT NOT NULL,
                    args        TEXT,
                    success     INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    run_at      TIMESTAMP NOT NULL,
                    repeat_secs INTEGER DEFAULT 0,
                    command     TEXT NOT NULL,
                    args        TEXT,
                    channel_id  TEXT NOT NULL,
                    enabled     INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS bot_settings (
                    key         TEXT PRIMARY KEY,
                    value       TEXT NOT NULL,
                    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS command_stats (
                    command     TEXT PRIMARY KEY,
                    invocations INTEGER DEFAULT 0,
                    errors      INTEGER DEFAULT 0,
                    last_used   TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    alert_type  TEXT NOT NULL,
                    message     TEXT NOT NULL,
                    channel_id  TEXT,
                    resolved    INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
                CREATE INDEX IF NOT EXISTS idx_audit_cmd  ON audit_log(command);
                CREATE INDEX IF NOT EXISTS idx_schedule_run ON scheduled_tasks(run_at);
            """)
        logger.info("Database initialized.")

    # ─── Audit ────────────────────────────────────────────────────────────────

    def log_command(
        self,
        user_id: int,
        username: str,
        guild_id: Optional[int],
        command: str,
        args: str = "",
        success: bool = True,
    ):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (user_id, username, guild_id, command, args, success) "
                "VALUES (?,?,?,?,?,?)",
                (str(user_id), username, str(guild_id) if guild_id else None,
                 command, args, int(success)),
            )
            conn.execute(
                "INSERT INTO command_stats (command, invocations, last_used) VALUES (?,1,CURRENT_TIMESTAMP) "
                "ON CONFLICT(command) DO UPDATE SET invocations=invocations+1, last_used=CURRENT_TIMESTAMP",
                (command,),
            )

    def get_audit_log(self, limit: int = 50) -> List[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()

    # ─── Scheduled Tasks ──────────────────────────────────────────────────────

    def add_task(self, run_at: datetime, command: str, channel_id: int,
                 args: str = "", repeat_secs: int = 0) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO scheduled_tasks (run_at, command, args, channel_id, repeat_secs) "
                "VALUES (?,?,?,?,?)",
                (run_at, command, args, str(channel_id), repeat_secs),
            )
            return cur.lastrowid

    def get_pending_tasks(self) -> List[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM scheduled_tasks WHERE enabled=1 AND run_at <= CURRENT_TIMESTAMP"
            ).fetchall()

    def get_all_tasks(self) -> List[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM scheduled_tasks WHERE enabled=1 ORDER BY run_at"
            ).fetchall()

    def remove_task(self, task_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE scheduled_tasks SET enabled=0 WHERE id=?", (task_id,))

    def reschedule_task(self, task_id: int, next_run: datetime):
        with self._conn() as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET run_at=? WHERE id=?", (next_run, task_id)
            )

    # ─── Settings ─────────────────────────────────────────────────────────────

    def set_setting(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO bot_settings (key, value, updated_at) VALUES (?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                (key, value),
            )

    def get_setting(self, key: str, default: str = None) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM bot_settings WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    # ─── Stats ────────────────────────────────────────────────────────────────

    def get_command_stats(self, limit: int = 20) -> List[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM command_stats ORDER BY invocations DESC LIMIT ?", (limit,)
            ).fetchall()

    # ─── Alerts ───────────────────────────────────────────────────────────────

    def log_alert(self, alert_type: str, message: str, channel_id: Optional[int] = None):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO alerts (alert_type, message, channel_id) VALUES (?,?,?)",
                (alert_type, message, str(channel_id) if channel_id else None),
            )

    def get_recent_alerts(self, limit: int = 20) -> List[sqlite3.Row]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()


# Global singleton
db = Database()
