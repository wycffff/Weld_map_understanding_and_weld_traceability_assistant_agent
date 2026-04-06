from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from weld_traceability_agent.bootstrap import bootstrap_source_repo
from weld_traceability_agent.config import AgentAppConfig

bootstrap_source_repo()

from weld_assistant.config import AppConfig, load_config as load_assistant_config
from weld_assistant.contracts import StructuredDrawing
from weld_assistant.db.repository import SQLiteRepository
from weld_assistant.services.exporter import FileExporter
from weld_assistant.services.pipeline import PipelineService
from weld_assistant.services.progress import ProgressService, normalize_manual_weld_id
from weld_assistant.services.review import ReviewService


DRAWING_PREVIEW_TOKEN_THRESHOLD = 20


def normalize_lookup_key(value: str | None) -> str:
    if not value:
        return ""
    return "".join(char for char in value.upper() if char.isalnum())


@dataclass(frozen=True)
class AttachmentRoutingDecision:
    kind: str
    ocr_token_count: int


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
        _resolve_assistant_paths(self.config, assistant_config_path)
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

    def classify_attachment_for_routing(self, input_path: str | Path) -> AttachmentRoutingDecision:
        path = Path(input_path)
        input_doc = self.pipeline.loader.load(
            path.read_bytes(),
            {"original_filename": path.name},
        )
        preprocessed = self.pipeline.preprocessor.process(input_doc)
        ocr_engine = self.pipeline.build_ocr_engine()
        preview_layout = self.pipeline.region_planner.build_preview_plan(preprocessed)
        ocr_result = ocr_engine.extract_layout(preprocessed, preview_layout)
        token_count = len(ocr_result.tokens)
        return AttachmentRoutingDecision(
            kind=classify_attachment_kind_from_token_count(token_count),
            ocr_token_count=token_count,
        )

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
        drawing_number = structured.drawing.drawing_number
        if drawing_number and drawing_number != structured.document_id:
            drawing_line = drawing_number
            source_line = "recognized from drawing"
        else:
            drawing_line = f"not recognized confidently (currently using {structured.document_id})"
            source_line = "fallback document ID"
        return (
            "Draft created. Please confirm.\n\n"
            f"Drawing number: {drawing_line}\n"
            f"Source: {source_line}\n"
            f"Welds: {len(structured.welds)}\n"
            f"Review items: {len(structured.needs_review_items)}\n\n"
            'Reply "confirm import" to continue, or send the correct drawing number.'
        )

    def normalize_weld_id(self, value: str | None) -> str | None:
        return normalize_manual_weld_id(value)

    def serialize_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)


def _resolve_assistant_paths(config: AppConfig, config_path: Path) -> None:
    project_root = _assistant_project_root(config_path)
    config.pipeline.data_root = str(_resolve_assistant_path(project_root, config.pipeline.data_root))
    config.layout.manual_roi_config = str(_resolve_assistant_path(project_root, config.layout.manual_roi_config))
    config.database.path = str(_resolve_assistant_path(project_root, config.database.path))
    config.export.output_dir = str(_resolve_assistant_path(project_root, config.export.output_dir))


def _assistant_project_root(config_path: Path) -> Path:
    config_dir = config_path.parent
    if config_dir.name.lower() == "config":
        return config_dir.parent
    return config_dir


def _resolve_assistant_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return project_root / path


def classify_attachment_kind_from_token_count(token_count: int) -> str:
    return "drawing" if token_count >= DRAWING_PREVIEW_TOKEN_THRESHOLD else "weld_photo"
