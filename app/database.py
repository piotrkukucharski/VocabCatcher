import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_db_path() -> Path:
    raw_path = os.getenv("SQLITE_DB_PATH", "data/vocabcatcher.db")
    db_path = Path(raw_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    target_language TEXT NOT NULL,
                    native_language TEXT NOT NULL,
                    cefr_level TEXT NOT NULL,
                    source TEXT,
                    youtube_url TEXT,
                    file_name TEXT,
                    status TEXT NOT NULL,
                    status_detail TEXT NOT NULL,
                    error TEXT,
                    sentences_json TEXT NOT NULL DEFAULT '[]',
                    raw_extracted_words_json TEXT NOT NULL DEFAULT '[]',
                    final_items_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
    finally:
        conn.close()


def create_session(username: str, ttl_days: int = 7) -> str:
    import secrets
    import time

    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + (ttl_days * 86400)
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (token, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, username, now, expires_at),
            )
        return token
    finally:
        conn.close()


def get_session(token: str) -> Optional[Dict[str, Any]]:
    import time

    if not token:
        return None
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT token, username, created_at, expires_at FROM sessions WHERE token = ?",
            (token,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        if data["expires_at"] < time.time():
            # Session expired, delete it
            with conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None
        return data
    finally:
        conn.close()


def delete_session(token: str):
    if not token:
        return
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    finally:
        conn.close()


def clean_expired_sessions() -> int:
    import time

    now = time.time()
    conn = get_connection()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            return cursor.rowcount
    finally:
        conn.close()


def save_operation(
    op_id: str,
    target_language: str,
    native_language: str,
    cefr_level: str,
    status: str,
    status_detail: str,
    created_at: float,
    youtube_url: Optional[str] = None,
    file_name: Optional[str] = None,
    source: Optional[str] = None,
    error: Optional[str] = None,
    sentences: Optional[List[str]] = None,
    raw_extracted_words: Optional[List[str]] = None,
    final_items: Optional[List[Dict[str, Any]]] = None,
):
    if source is None:
        source = youtube_url if youtube_url else (file_name or "Uploaded Text")

    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO operations (
                    id, target_language, native_language, cefr_level,
                    source, youtube_url, file_name, status, status_detail,
                    error, sentences_json, raw_extracted_words_json,
                    final_items_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    status_detail = excluded.status_detail,
                    error = excluded.error,
                    sentences_json = excluded.sentences_json,
                    raw_extracted_words_json = excluded.raw_extracted_words_json,
                    final_items_json = excluded.final_items_json
                """,
                (
                    op_id,
                    target_language,
                    native_language,
                    cefr_level,
                    source,
                    youtube_url,
                    file_name,
                    status,
                    status_detail,
                    error,
                    json.dumps(sentences or [], ensure_ascii=False),
                    json.dumps(raw_extracted_words or [], ensure_ascii=False),
                    json.dumps(final_items or [], ensure_ascii=False),
                    created_at,
                ),
            )
    finally:
        conn.close()


def update_operation_status(
    op_id: str,
    status: str,
    status_detail: str,
    error: Optional[str] = None,
    sentences: Optional[List[str]] = None,
    raw_extracted_words: Optional[List[str]] = None,
    final_items: Optional[List[Dict[str, Any]]] = None,
):
    conn = get_connection()
    try:
        with conn:
            updates = ["status = ?", "status_detail = ?"]
            params: List[Any] = [status, status_detail]

            if error is not None:
                updates.append("error = ?")
                params.append(error)

            if sentences is not None:
                updates.append("sentences_json = ?")
                params.append(json.dumps(sentences, ensure_ascii=False))

            if raw_extracted_words is not None:
                updates.append("raw_extracted_words_json = ?")
                params.append(json.dumps(raw_extracted_words, ensure_ascii=False))

            if final_items is not None:
                updates.append("final_items_json = ?")
                params.append(json.dumps(final_items, ensure_ascii=False))

            params.append(op_id)
            query = f"UPDATE operations SET {', '.join(updates)} WHERE id = ?"
            conn.execute(query, params)
    finally:
        conn.close()


def get_operation(op_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM operations WHERE id = ?", (op_id,))
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        data["sentences"] = json.loads(data.get("sentences_json") or "[]")
        data["raw_extracted_words"] = json.loads(data.get("raw_extracted_words_json") or "[]")
        data["final_items"] = json.loads(data.get("final_items_json") or "[]")
        return data
    finally:
        conn.close()


def list_operations_db() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM operations ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
        result = []
        for r in rows:
            data = dict(r)
            items = json.loads(data.get("final_items_json") or "[]")
            result.append({
                "id": data["id"],
                "status": data["status"],
                "status_detail": data["status_detail"],
                "target_language": data["target_language"],
                "native_language": data["native_language"],
                "cefr_level": data["cefr_level"],
                "source": data["source"] or data["youtube_url"] or data["file_name"] or "Uploaded Text",
                "items_count": len(items),
                "error": data["error"],
                "created_at": data["created_at"],
            })
        return result
    finally:
        conn.close()


def clear_operations_db():
    init_db()
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM operations")
    finally:
        conn.close()

