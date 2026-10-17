import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone


def uid() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS topics (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                route TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('global','topic')),
                topic_id TEXT REFERENCES topics(id) ON DELETE RESTRICT, key TEXT NOT NULL, content TEXT NOT NULL,
                source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
                updated_at TEXT NOT NULL,
                CHECK((scope='global' AND topic_id IS NULL) OR (scope='topic' AND topic_id IS NOT NULL))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS memory_key ON memories(scope,COALESCE(topic_id,''),key);
            CREATE TABLE IF NOT EXISTS mobile_sessions (
                token_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_events (
                id TEXT PRIMARY KEY, action TEXT NOT NULL, memory_id TEXT, before_value TEXT,
                after_value TEXT, source_message_id TEXT, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS message_conversation ON messages(conversation_id,created_at);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def all(self, sql: str, args=()) -> list[dict]:
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def one(self, sql: str, args=()) -> dict | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql: str, args=()):
        with self.connect() as c:
            c.execute(sql, args)

    def topic(self, name: str, description: str) -> dict:
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO topics VALUES(?,?,?,?)", (uid(), name, description, now()))
            return dict(c.execute("SELECT * FROM topics WHERE name=?", (name,)).fetchone())

    def save_memory(self, scope, topic_id, key, content, source):
        if scope == "global":
            topic_id = None
        with self.connect() as c:
            # A deferred job must not undo the owner's edits/deletions by replaying its old source.
            if (
                source
                and c.execute(
                    "SELECT id FROM memory_events WHERE action IN ('delete','manual_edit') "
                    "AND json_extract(before_value,'$.source_message_id')=? "
                    "AND json_extract(before_value,'$.scope')=? LIMIT 1",
                    (source, scope),
                ).fetchone()
            ):
                return
            old = c.execute(
                "SELECT * FROM memories WHERE scope=? AND topic_id IS ? AND key=?", (scope, topic_id, key)
            ).fetchone()
            if old and old["content"] == content:
                return
            memory_id = old["id"] if old else uid()
            if old:
                c.execute(
                    "UPDATE memories SET content=?,source_message_id=?,updated_at=? WHERE id=?",
                    (content, source, now(), memory_id),
                )
            else:
                c.execute(
                    "INSERT INTO memories VALUES(?,?,?,?,?,?,?)",
                    (memory_id, scope, topic_id, key, content, source, now()),
                )
            c.execute(
                "INSERT INTO memory_events VALUES(?,?,?,?,?,?,?)",
                (
                    uid(),
                    "update" if old else "create",
                    memory_id,
                    json.dumps(dict(old), ensure_ascii=False) if old else None,
                    content,
                    source,
                    now(),
                ),
            )

    def set_route(self, message_id: str, route: dict):
        self.execute(
            "UPDATE messages SET route=? WHERE id=?", (json.dumps(route, ensure_ascii=False), message_id)
        )

    def assign_message(self, message: dict, topic_id: str):
        """Persist an explicit assignment atomically, without model availability."""
        mid = message["id"]
        route = {
            "status": "matched",
            "topic_ids": [topic_id],
            "confidence": 1,
            "remember": True,
            "notice": "手动归类",
            "manual_assignment": True,
        }
        timestamp = now()
        with self.connect() as c:
            previous = json.loads(c.execute("SELECT route FROM messages WHERE id=?", (mid,)).fetchone()[0])
            if previous.get("manual_assignment") and previous.get("topic_ids") == [topic_id]:
                return
            # Keep full source text, bounded to the editor's content limit per record.
            chunks = [message["content"][i : i + 2000] for i in range(0, len(message["content"]), 2000)]
            for index, content in enumerate(chunks, 1):
                title = " ".join(message["content"].split())[:40]
                base_key = f"手动归类：{title}"
                if len(chunks) > 1:
                    base_key += f"（第{index}段）"
                key, suffix = base_key, 2
                while c.execute(
                    "SELECT id FROM memories WHERE scope='topic' AND topic_id=? AND key=?", (topic_id, key)
                ).fetchone():
                    key = f"{base_key}（{suffix}）"
                    suffix += 1
                memory_id = uid()
                c.execute(
                    "INSERT INTO memories VALUES(?,?,?,?,?,?,?)",
                    (memory_id, "topic", topic_id, key, content, mid, timestamp),
                )
                c.execute(
                    "INSERT INTO memory_events VALUES(?,?,?,?,?,?,?)",
                    (uid(), "manual_assign", memory_id, None, content, mid, timestamp),
                )
            c.execute("UPDATE messages SET route=? WHERE id=?", (json.dumps(route, ensure_ascii=False), mid))
            c.execute(
                "INSERT INTO jobs(message_id,status,updated_at) VALUES(?,'done',?) "
                "ON CONFLICT(message_id) DO UPDATE SET status='done',error=NULL,updated_at=excluded.updated_at",
                (mid, timestamp),
            )

    def enqueue(self, message_id: str):
        self.execute("INSERT OR IGNORE INTO jobs(message_id,updated_at) VALUES(?,?)", (message_id, now()))

    def recover(self):
        self.execute("UPDATE messages SET status='interrupted' WHERE status='streaming'")
        self.execute("UPDATE jobs SET status='queued' WHERE status='running'")
