"""用户库（SQLite）。

只存账号、会话和审计流水，不存任何地理数据。数据本身在文件系统的
各个数据空间里（见 :mod:`gisds.core.tenancy`）。

SQLite 连接按需开关，开启 WAL 让读写不互相阻塞。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    status        TEXT NOT NULL DEFAULT 'active',
    display_name  TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    last_login_at REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  TEXT,
    username TEXT,
    action   TEXT NOT NULL,
    target   TEXT,
    at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(at);
"""

ROLE_ADMIN = "admin"
ROLE_USER = "user"
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"


class Database:
    """极薄的 SQLite 封装。"""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(_SCHEMA)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ---------------------------------------------------------------- 用户

    def create_user(
        self,
        user_id: str,
        username: str,
        password_hash: str,
        role: str = ROLE_USER,
        display_name: str = "",
    ) -> dict[str, Any]:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, status, display_name, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (user_id, username, password_hash, role, STATUS_ACTIVE, display_name or username, now),
            )
        return self.get_user(user_id)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_name(self, username: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def count_users(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def update_user(self, user_id: str, **fields: Any) -> bool:
        allowed = {"username", "password_hash", "role", "status", "display_name", "last_login_at"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        clause = ", ".join(f"{k}=?" for k in sets)
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE users SET {clause} WHERE id=?", (*sets.values(), user_id)
            )
        return cur.rowcount > 0

    def delete_user(self, user_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return cur.rowcount > 0

    # ---------------------------------------------------------------- 会话

    def create_session(self, token_hash: str, user_id: str, expires_at: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                (token_hash, user_id, time.time(), expires_at),
            )

    def get_session(self, token_hash: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE token_hash=? AND expires_at > ?",
                (token_hash, time.time()),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
        return cur.rowcount > 0

    def delete_sessions_for_user(self, user_id: str) -> int:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return cur.rowcount

    def purge_sessions(self) -> int:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
        return cur.rowcount

    # ---------------------------------------------------------------- 审计

    def log(self, user_id: str | None, username: str | None, action: str, target: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit (user_id, username, action, target, at) VALUES (?,?,?,?,?)",
                (user_id, username, action, target, time.time()),
            )

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit ORDER BY at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    def all_users(self) -> Iterable[dict[str, Any]]:
        return self.list_users()
