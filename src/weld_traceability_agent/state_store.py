from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from weld_traceability_agent.config import AgentAppConfig
from weld_traceability_agent.runtime_models import AgentTask, ConversationState, DraftRecord, ProcessedMessage


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversation_state (
  chat_id TEXT PRIMARY KEY,
  channel TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_task (
  task_id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  source_message_id TEXT NOT NULL,
  draft_path TEXT,
  error TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_record (
  draft_id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS processed_message (
  channel TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  processed_at TEXT NOT NULL,
  PRIMARY KEY (channel, chat_id, message_id)
);
"""


class RuntimeStateStore:
    def __init__(self, config: AgentAppConfig):
        self.config = config
        self.db_path = config.resolve_path(config.agent.runtime_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA synchronous=NORMAL;")
        connection.execute("PRAGMA busy_timeout=5000;")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)

    def get_conversation(self, channel: str, chat_id: str, ttl_hours: int) -> ConversationState:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM conversation_state WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        if not row:
            state = ConversationState(channel=channel, chat_id=chat_id)
            state.refresh(ttl_hours)
            self.save_conversation(state)
            return state

        state = ConversationState.model_validate_json(row["payload_json"])
        if state.is_expired():
            state = ConversationState(channel=channel, chat_id=chat_id)
        state.refresh(ttl_hours)
        self.save_conversation(state)
        return state

    def save_conversation(self, state: ConversationState) -> None:
        payload = state.model_dump_json()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_state (chat_id, channel, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id)
                DO UPDATE SET
                  channel = excluded.channel,
                  payload_json = excluded.payload_json,
                  updated_at = excluded.updated_at
                """,
                (
                    state.chat_id,
                    state.channel,
                    payload,
                    state.last_message_at.isoformat(),
                ),
            )

    def create_task(
        self,
        chat_id: str,
        kind: str,
        source_message_id: str,
        payload: dict | None = None,
        status: str = "queued",
    ) -> AgentTask:
        task = AgentTask(
            task_id=f"task_{uuid4().hex[:12]}",
            chat_id=chat_id,
            kind=kind,
            status=status,
            source_message_id=source_message_id,
            payload=payload or {},
        )
        self.save_task(task)
        return task

    def save_task(self, task: AgentTask) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_task (
                  task_id, chat_id, kind, status, source_message_id, draft_path,
                  error, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id)
                DO UPDATE SET
                  status = excluded.status,
                  draft_path = excluded.draft_path,
                  error = excluded.error,
                  payload_json = excluded.payload_json,
                  updated_at = excluded.updated_at
                """,
                (
                    task.task_id,
                    task.chat_id,
                    task.kind,
                    task.status,
                    task.source_message_id,
                    task.draft_path,
                    task.error,
                    json.dumps(task.payload, ensure_ascii=False),
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )

    def get_task(self, task_id: str) -> AgentTask | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_task WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return AgentTask(
            task_id=row["task_id"],
            chat_id=row["chat_id"],
            kind=row["kind"],
            status=row["status"],
            source_message_id=row["source_message_id"],
            draft_path=row["draft_path"],
            error=row["error"],
            payload=json.loads(row["payload_json"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def save_draft(self, draft: DraftRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO draft_record (draft_id, chat_id, task_id, payload_json, created_at, confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id)
                DO UPDATE SET
                  payload_json = excluded.payload_json,
                  confirmed_at = excluded.confirmed_at
                """,
                (
                    draft.draft_id,
                    draft.chat_id,
                    draft.task_id,
                    draft.model_dump_json(),
                    draft.created_at.isoformat(),
                    draft.confirmed_at.isoformat() if draft.confirmed_at else None,
                ),
            )

    def get_draft(self, draft_id: str) -> DraftRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM draft_record WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
        if not row:
            return None
        return DraftRecord.model_validate_json(row["payload_json"])

    def get_active_draft(self, chat_id: str) -> DraftRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM draft_record
                WHERE chat_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (chat_id,),
            ).fetchone()
        if not row:
            return None
        draft = DraftRecord.model_validate_json(row["payload_json"])
        if draft.confirmed_at:
            return None
        return draft

    def touch_processed_message(self, processed: ProcessedMessage) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO processed_message (channel, chat_id, message_id, processed_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    processed.channel,
                    processed.chat_id,
                    processed.message_id,
                    processed.processed_at.isoformat(),
                ),
            )
        return cursor.rowcount == 1

    def has_processed_message(self, channel: str, chat_id: str, message_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM processed_message
                WHERE channel = ? AND chat_id = ? AND message_id = ?
                """,
                (channel, chat_id, message_id),
            ).fetchone()
        return row is not None


def _parse_dt(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
