from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_DB_LOCK = threading.Lock()
_DB_CACHE: dict[Path, sqlite3.Connection] = {}


def _db_path(run_root: Path) -> Path:
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace / "chat.db"


def _get_conn(run_root: Path) -> sqlite3.Connection:
    db_path = _db_path(run_root)
    conn = _DB_CACHE.get(db_path)
    if conn is not None:
        return conn
    with _DB_LOCK:
        conn = _DB_CACHE.get(db_path)
        if conn is not None:
            return conn
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                thinking TEXT NOT NULL DEFAULT '',
                actions TEXT NOT NULL DEFAULT '[]',
                kind TEXT NOT NULL DEFAULT 'message',
                created_at TEXT NOT NULL,
                created_ts REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_run ON chat_messages(run_id, id)"
        )
        _DB_CACHE[db_path] = conn
        return conn


def save_message(
    run_root: Path,
    run_id: str,
    role: str,
    content: str,
    thinking: str = "",
    actions: list[dict[str, Any]] | None = None,
    kind: str = "message",
) -> dict[str, Any]:
    conn = _get_conn(run_root)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    ts = time.time()
    actions_json = json.dumps(actions or [], ensure_ascii=False)
    with _DB_LOCK:
        cur = conn.execute(
            "INSERT INTO chat_messages (run_id, role, content, thinking, actions, kind, created_at, created_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, role, content, thinking, actions_json, kind, now, ts),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "run_id": run_id,
            "role": role,
            "content": content,
            "thinking": thinking,
            "actions": actions or [],
            "kind": kind,
            "created_at": now,
        }


def load_messages(run_root: Path, run_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    conn = _get_conn(run_root)
    with _DB_LOCK:
        rows = conn.execute(
            "SELECT id, run_id, role, content, thinking, actions, kind, created_at "
            "FROM chat_messages WHERE run_id = ? ORDER BY id ASC LIMIT ?",
            (run_id, limit),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            actions = json.loads(row["actions"]) if row["actions"] else []
        except Exception:
            actions = []
        result.append(
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "thinking": row["thinking"],
                "actions": actions,
                "kind": row["kind"],
                "created_at": row["created_at"],
            }
        )
    return result


def clear_messages(run_root: Path, run_id: str) -> int:
    conn = _get_conn(run_root)
    with _DB_LOCK:
        cur = conn.execute("DELETE FROM chat_messages WHERE run_id = ?", (run_id,))
        conn.commit()
        return cur.rowcount
