from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from weld_traceability_agent.bootstrap import bootstrap_source_repo
from weld_traceability_agent.config import AgentAppConfig

bootstrap_source_repo()

from weld_assistant.config import load_config as load_assistant_config
from weld_assistant.contracts import StructuredDrawing
from weld_assistant.db.repository import SQLiteRepository
from weld_assistant.services.exporter import FileExporter
from weld_assistant.services.pipeline import PipelineService
from weld_assistant.services.progress import ProgressService, normalize_manual_weld_id
from weld_assistant.services.review import ReviewService


def normalize_lookup_key(value: str | None) -> str:
    if not value:
        return ""
    return "".join(char for char in value.upper() if char.isalnum())


class SafeSQLiteRepository(SQLiteRepository):
    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA synchronous=NORMAL;")
        connection.execute("PRAGMA busy_timeout=5000;")
        connection.execute("PRAGMA foreign_keys=ON;")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def search_welds(self, query: str = "", drawing_number: str | None = None, limit: int = 10) -> list[sqlite3.Row]:
        normalized_query = normalize_lookup_key(query)
        with self.connect() as connection:
            if drawing_number:
                rows = connection.execute(
                    "SELECT * FROM weld WHERE drawing_number = ? ORDER BY weld_id",
                    (drawing_number,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM weld ORDER BY drawing_number, weld_id",
                ).fetchall()

        if not normalized_query:
            return list(rows[:limit])

        matches: list[sqlite3.Row] = []
        for row in rows:
            blobs = [
                normalize_lookup_key(row["drawing_number"]),
                normalize_lookup_key(row["weld_id"]),
                normalize_lookup_key(row["location_description"] or ""),
                normalize_lookup_key(row["remarks"] or ""),
            ]
            if any(normalized_query in blob for blob in blobs if blob):
                matches.append(row)
        return matches[:limit]


class AssistantBridge:
    def __init__(self, config: AgentAppConfig):
        self.agent_config = config
        assistant_config_path = config.resolve_path(config.assistant.config_path)
        self.config = load_assistant_config(assistant_config_path)
        if not self.config.export.csv_fields:
            self.config.export.csv_fields = [
                "drawing_number",
                "weld_id",
                "status",
                "completed_by",
                "completed_at",
                "inspection_status",
                "last_photo_id",
                "last_photo_path",
            ]
        self.pipeline = PipelineService(self.config)
        self.repository = SafeSQLiteRepository(self.config)
        self.repository.init_db()
        self.progress_service = ProgressService(self.repository)
        self.review_service = ReviewService(self.repository, self.pipeline.vlm)
        self.exporter = FileExporter(self.config)

    def parse_drawing(self, input_path: str | Path, use_vlm: bool = True) -> StructuredDrawing:
        return self.pipeline.process_file(input_path, persist=False, use_vlm=use_vlm)

    def save_structured_draft(self, structured: StructuredDrawing, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(structured.to_jsonable(), ensure_ascii=False, indent=2), encoding="utf-8")

    def load_structured_draft(self, path: str | Path) -> StructuredDrawing:
        raw = Path(path).read_text(encoding="utf-8")
        return StructuredDrawing.model_validate_json(raw)

    def import_structured_drawing(self, structured: StructuredDrawing, overwrite: bool = False) -> str:
        self.repository.import_structured_drawing(structured, overwrite=overwrite)
        return structured.drawing.drawing_number or structured.document_id

    def export_structured_drawing(self, structured: StructuredDrawing) -> tuple[str, str]:
        return self.exporter.export_structured_drawing(structured)

    def build_preview_text(self, structured: StructuredDrawing) -> str:
        drawing_number = structured.drawing.drawing_number or structured.document_id
        drawing_type = structured.drawing.drawing_type or "unknown"
        support_label = "auto-supported" if structured.drawing.drawing_type_supported else "manual-review"
        return (
            f"Drawing candidate: {drawing_number}\n"
            f"Drawing type: {drawing_type} ({support_label})\n"
            f"Weld count: {len(structured.welds)}\n"
            f"BOM count: {len(structured.bom)}\n"
            f"Review items: {len(structured.needs_review_items)}"
        )

    def normalize_weld_id(self, value: str | None) -> str | None:
        return normalize_manual_weld_id(value)

    def serialize_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)
