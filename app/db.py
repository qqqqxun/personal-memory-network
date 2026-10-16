from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, ensure_data_dir
from app.crypto import Cipher
from app.ids import new_id, now_iso

SCHEMA_VERSION = 1

ENCRYPTED_FIELDS = {
    "threads": {"title", "goal", "constraints", "summary"},
    "attempts": {"plan", "process", "result", "reason"},
    "candidates": {"plan", "origin_note", "reason", "source_url"},
    "solutions": {"plan", "evidence", "supersede_reason"},
    "open_questions": {"question", "answer"},
    "entities": {"name", "aliases_json"},
    "facts": {"content"},
    "insights": {"content"},
    "profile": {"content"},
    "sources": {"title", "raw_text"},
    "review_items": {"payload_json", "reviewer_note"},
    "egress_log": {"emitted", "original_preview", "redactions_json"},
    "relations": {"rel_type"},
}

JSON_FIELDS = {
    "source_ids",
    "aliases_json",
    "thread_ids",
    "entity_ids",
    "payload_json",
    "redactions_json",
    "stats_json",
    "vector_json",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    title TEXT,
    goal TEXT,
    constraints TEXT,
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    parent_id TEXT,
    current_solution_id TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0,
    weight REAL NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    plan TEXT,
    process TEXT,
    result TEXT,
    outcome TEXT,
    reason TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    plan TEXT,
    origin TEXT,
    origin_note TEXT,
    reason TEXT,
    source_url TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    estimated_cost TEXT,
    estimated_benefit TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS solutions (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    plan TEXT,
    evidence TEXT,
    effective_from TEXT,
    superseded_by TEXT,
    supersede_reason TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS open_questions (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    question TEXT,
    answer TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    name TEXT,
    type TEXT NOT NULL DEFAULT 'other',
    aliases_json TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    subject_entity_id TEXT,
    content TEXT,
    superseded_by TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY,
    from_id TEXT NOT NULL,
    from_type TEXT NOT NULL,
    to_id TEXT NOT NULL,
    to_type TEXT NOT NULL,
    rel_type TEXT,
    weight REAL NOT NULL DEFAULT 1.0,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS insights (
    id TEXT PRIMARY KEY,
    content TEXT,
    thread_ids TEXT,
    entity_ids TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS profile (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    content TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    conversation_id TEXT,
    title TEXT,
    occurred_at TEXT,
    raw_text TEXT,
    process_status TEXT NOT NULL DEFAULT 'pending',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS egress_log (
    id TEXT PRIMARY KEY,
    destination TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    blocked_reason TEXT,
    emitted TEXT,
    original_preview TEXT,
    redactions_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS review_items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    impact TEXT NOT NULL,
    thread_id TEXT,
    payload_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reviewed_at TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    source_ids TEXT,
    confidence REAL NOT NULL DEFAULT 0.4,
    record_status TEXT NOT NULL DEFAULT 'active',
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ingest_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    stats_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    id TEXT PRIMARY KEY,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_thread ON attempts(thread_id);
CREATE INDEX IF NOT EXISTS idx_candidates_thread ON candidates(thread_id);
CREATE INDEX IF NOT EXISTS idx_solutions_thread ON solutions(thread_id);
CREATE INDEX IF NOT EXISTS idx_questions_thread ON open_questions(thread_id);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status, impact);
CREATE INDEX IF NOT EXISTS idx_sources_conv ON sources(platform, conversation_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_owner ON embeddings(owner_type, owner_id);
"""


def db_path() -> Path:
    ensure_data_dir()
    return DATA_DIR / "memory.db"


def _encode(value: Any, key: str) -> Any:
    if key in JSON_FIELDS and not isinstance(value, str):
        return json.dumps(value or ([] if key.endswith("ids") or key.endswith("json") else value), ensure_ascii=False)
    return value


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key, value in list(out.items()):
        if key in JSON_FIELDS and isinstance(value, str) and value:
            try:
                out[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    if "source_ids" in out and out["source_ids"] is None:
        out["source_ids"] = []
    return out


class Store:
    def __init__(self, path: Path | None = None, cipher: Cipher | None = None) -> None:
        self.path = path or db_path()
        self.cipher = cipher or Cipher()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _encrypt_row(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        fields = ENCRYPTED_FIELDS.get(table, set())
        stored = {}
        for key, value in row.items():
            encoded = _encode(value, key)
            if key in fields and isinstance(encoded, str):
                stored[key] = self.cipher.encrypt(encoded)
            else:
                stored[key] = encoded
        return stored

    def _decrypt_row(self, table: str, row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        fields = ENCRYPTED_FIELDS.get(table, set())
        for key in fields:
            if key in data and isinstance(data[key], str):
                data[key] = self.cipher.decrypt(data[key])
        return _decode(data)

    def upsert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data.setdefault("id", new_id())
        now = now_iso()
        data.setdefault("created_at", now)
        data["updated_at"] = now
        stored = self._encrypt_row(table, data)
        columns = list(stored.keys())
        placeholders = ", ".join("?" for _ in columns)
        col_sql = ", ".join(columns)
        updates = ", ".join(f"{col}=excluded.{col}" for col in columns if col != "id")
        sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}"
        self.conn.execute(sql, [stored[col] for col in columns])
        self.conn.commit()
        return self.get(table, data["id"]) or data

    def get(self, table: str, item_id: str) -> dict[str, Any] | None:
        cur = self.conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,))
        return self._decrypt_row(table, cur.fetchone())

    def delete(self, table: str, item_id: str) -> None:
        self.conn.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
        self.conn.commit()

    def list(
        self,
        table: str,
        where: str = "",
        params: tuple[Any, ...] = (),
        order: str = "updated_at DESC",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        if order:
            sql += f" ORDER BY {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        cur = self.conn.execute(sql, params)
        rows = [self._decrypt_row(table, item) for item in cur.fetchall()]
        return [row for row in rows if row is not None]

    def count(self, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
        sql = f"SELECT COUNT(*) AS n FROM {table}"
        if where:
            sql += f" WHERE {where}"
        cur = self.conn.execute(sql, params)
        return int(cur.fetchone()["n"])

    def find_source(self, platform: str, conversation_id: str) -> dict[str, Any] | None:
        cur = self.conn.execute(
            "SELECT * FROM sources WHERE platform=? AND conversation_id=?",
            (platform, conversation_id),
        )
        return self._decrypt_row("sources", cur.fetchone())

    def put_embedding(self, owner_type: str, owner_id: str, vector: list[float]) -> None:
        self.conn.execute("DELETE FROM embeddings WHERE owner_type=? AND owner_id=?", (owner_type, owner_id))
        self.conn.execute(
            "INSERT INTO embeddings(id, owner_type, owner_id, vector_json, created_at) VALUES(?,?,?,?,?)",
            (new_id(), owner_type, owner_id, json.dumps(vector), now_iso()),
        )
        self.conn.commit()

    def embeddings(self, owner_type: str | None = None) -> list[dict[str, Any]]:
        if owner_type:
            cur = self.conn.execute("SELECT * FROM embeddings WHERE owner_type=?", (owner_type,))
        else:
            cur = self.conn.execute("SELECT * FROM embeddings")
        rows = []
        for item in cur.fetchall():
            row = dict(item)
            row["vector_json"] = json.loads(row["vector_json"])
            rows.append(row)
        return rows

    def stats(self) -> dict[str, Any]:
        return {
            "threads": self.count("threads", "record_status!='archived'"),
            "active_threads": self.count("threads", "status='active' AND record_status!='archived'"),
            "attempts": self.count("attempts"),
            "candidates": self.count("candidates", "record_status='active'"),
            "sources": self.count("sources"),
            "review_pending": self.count("review_items", "status='pending'"),
            "review_must": self.count("review_items", "status='pending' AND impact='must'"),
            "facts": self.count("facts", "record_status='active'"),
            "entities": self.count("entities", "record_status='active'"),
        }
