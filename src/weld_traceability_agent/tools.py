from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from weld_traceability_agent.assistant_bridge import AssistantBridge
from weld_traceability_agent.config import AgentAppConfig
from weld_traceability_agent.runtime_models import BotAttachment, DraftRecord, IncomingMessage, OutgoingMessage
from weld_traceability_agent.state_store import RuntimeStateStore


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]


class AgentToolbox:
    def __init__(self, config: AgentAppConfig, store: RuntimeStateStore, bridge: AssistantBridge):
        self.config = config
        self.store = store
        self.bridge = bridge
        self._outbox: list[OutgoingMessage] = []
        self._handlers: dict[str, Callable[[dict[str, Any], IncomingMessage], dict[str, Any]]] = {
            "parse_drawing_draft": self.parse_drawing_draft,
            "confirm_draft_import": self.confirm_draft_import,
            "search_drawings": self.search_drawings,
            "search_welds": self.search_welds,
            "register_welds": self.register_welds,
            "update_weld_status": self.update_weld_status,
            "update_inspection_status": self.update_inspection_status,
            "link_photo": self.link_photo,
            "list_review_items": self.list_review_items,
            "apply_review_action": self.apply_review_action,
        }

    def reset_outbox(self) -> None:
        self._outbox = []

    def drain_outbox(self) -> list[OutgoingMessage]:
        outbox = list(self._outbox)
        self._outbox = []
        return outbox

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="parse_drawing_draft",
                description="Run the local OCR/VLM pipeline on one uploaded drawing and create a draft that still needs user confirmation before DB import.",
                parameters={
                    "type": "object",
                    "properties": {"attachment_id": {"type": "string"}},
                    "required": ["attachment_id"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="confirm_draft_import",
                description="Import the active draft into the shared SQLite database after the user explicitly confirms.",
                parameters={
                    "type": "object",
                    "properties": {
                        "draft_id": {"type": "string"},
                        "overwrite": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="search_drawings",
                description="Search drawings by drawing number, spool name, or document id.",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="search_welds",
                description="Search welds by weld id or description, optionally within one drawing.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "drawing_number": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="register_welds",
                description="Register one or more weld ids on the selected drawing.",
                parameters={
                    "type": "object",
                    "properties": {
                        "drawing_number": {"type": "string"},
                        "weld_ids": {"type": "array", "items": {"type": "string"}},
                        "location_description": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["weld_ids"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="update_weld_status",
                description="Update weld execution status for a weld on the selected drawing.",
                parameters={
                    "type": "object",
                    "properties": {
                        "drawing_number": {"type": "string"},
                        "weld_id": {"type": "string"},
                        "to_status": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["weld_id", "to_status"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="update_inspection_status",
                description="Update inspection status for a weld on the selected drawing.",
                parameters={
                    "type": "object",
                    "properties": {
                        "drawing_number": {"type": "string"},
                        "weld_id": {"type": "string"},
                        "inspection_status": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["weld_id", "inspection_status"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="link_photo",
                description="Attach one uploaded weld photo to a weld record.",
                parameters={
                    "type": "object",
                    "properties": {
                        "drawing_number": {"type": "string"},
                        "weld_id": {"type": "string"},
                        "attachment_id": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["weld_id", "attachment_id"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="list_review_items",
                description="List review queue items for the selected drawing or for all drawings.",
                parameters={
                    "type": "object",
                    "properties": {
                        "drawing_number": {"type": "string"},
                        "unresolved_only": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="apply_review_action",
                description="Apply one review action such as register_welds, keep_review_open, mark_resolved, inspect_manually, or rerun_vlm.",
                parameters={
                    "type": "object",
                    "properties": {
                        "review_id": {"type": "string"},
                        "action": {"type": "string"},
                        "weld_ids": {"type": "array", "items": {"type": "string"}},
                        "note": {"type": "string"},
                    },
                    "required": ["review_id", "action"],
                    "additionalProperties": False,
                },
            ),
        ]

    def invoke(self, tool_name: str, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        handler = self._handlers.get(tool_name)
        if not handler:
            return self._error("unknown_tool", f"Unknown tool: {tool_name}")
        try:
            return handler(arguments, incoming)
        except FileNotFoundError as exc:
            target = exc.filename or str(exc)
            return self._error(
                "missing_local_file",
                f"Required local file is missing: {target}. Check the shared assistant config and ROI template files.",
            )
        except Exception as exc:  # pragma: no cover - defensive
            return self._error("tool_failed", str(exc))

    def parse_drawing_draft(self, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        state = self._get_state(incoming)
        if state.active_draft_id and self.store.get_active_draft(incoming.chat_id):
            return self._error(
                "draft_pending_confirmation",
                "There is still an unconfirmed drawing draft in this chat. Please confirm or handle the previous one first.",
            )

        attachment = self._attachment_by_id(incoming.attachments, arguments["attachment_id"])
        if not attachment:
            return self._error("attachment_not_found", "I could not find the attachment to parse.")

        task = self.store.create_task(
            chat_id=incoming.chat_id,
            kind="parse_drawing_draft",
            source_message_id=incoming.message_id,
            payload={"attachment_id": attachment.attachment_id},
            status="running",
        )
        state.active_task_id = task.task_id
        self.store.save_conversation(state)

        structured = self.bridge.parse_drawing(
            attachment.local_path,
            use_vlm=self.config.agent.use_local_vlm_for_parse,
        )
        draft_id = f"draft_{uuid4().hex[:12]}"
        draft_path = self.config.resolve_path(self.config.agent.drafts_dir) / f"{draft_id}.json"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        self.bridge.save_structured_draft(structured, draft_path)
        draft = DraftRecord(
            draft_id=draft_id,
            chat_id=incoming.chat_id,
            task_id=task.task_id,
            source_message_id=incoming.message_id,
            source_attachment_id=attachment.attachment_id,
            input_file_path=attachment.local_path,
            structured_json_path=str(draft_path),
            preview_text=self.bridge.build_preview_text(structured),
            drawing_number_candidate=structured.drawing.drawing_number or structured.document_id,
            review_count=len(structured.needs_review_items),
            supported=structured.drawing.drawing_type_supported,
        )
        self.store.save_draft(draft)
        task.status = "completed"
        task.draft_path = str(draft_path)
        task.updated_at = _utc_now()
        self.store.save_task(task)

        state.active_draft_id = draft.draft_id
        state.pending_action = "confirm_draft_import"
        self.store.save_conversation(state)
        return self._success(
            reply_hint=draft.preview_text,
            draft_id=draft.draft_id,
            drawing_number_candidate=draft.drawing_number_candidate,
            review_count=draft.review_count,
            supported=draft.supported,
        )

    def confirm_draft_import(self, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        state = self._get_state(incoming)
        draft_id = arguments.get("draft_id") or state.active_draft_id
        if not draft_id:
            return self._error("missing_draft", "There is no drawing draft waiting for confirmation right now.")

        draft = self.store.get_draft(draft_id)
        if not draft:
            return self._error("draft_not_found", "I could not find that draft record.")

        structured = self.bridge.load_structured_draft(draft.structured_json_path)
        drawing_number = self.bridge.import_structured_drawing(structured, overwrite=bool(arguments.get("overwrite", False)))
        self.bridge.export_structured_drawing(structured)

        draft.confirmed_at = _utc_now()
        self.store.save_draft(draft)

        state.active_draft_id = None
        state.pending_action = None
        state.selected_drawing_number = drawing_number
        state.selected_weld_id = None
        self.store.save_conversation(state)

        review_count = len(structured.needs_review_items)
        if review_count > 0 and self.config.review.notify_admins:
            for admin_chat in self.config.security.admin_chats:
                if str(admin_chat) == str(incoming.chat_id):
                    continue
                self._outbox.append(
                    OutgoingMessage(
                        chat_id=str(admin_chat),
                        text=f"Review alert: drawing {drawing_number} has {review_count} new review items.",
                    )
                )

        return self._success(
            reply_hint=(
                f"Imported drawing {drawing_number}. "
                f"Welds: {len(structured.welds)}. Review items: {review_count}. "
                'Send "list review" to inspect the open review items.'
            ),
            drawing_number=drawing_number,
            weld_count=len(structured.welds),
            review_count=review_count,
        )

    def search_drawings(self, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        query = (arguments.get("query") or "").strip()
        rows = self.bridge.repository.search_drawings(query, limit=10)
        results = [self.bridge.serialize_row(row) for row in rows]
        state = self._get_state(incoming)
        if len(results) == 1:
            state.selected_drawing_number = results[0]["drawing_number"]
            self.store.save_conversation(state)
        return self._success(
            reply_hint=_drawing_search_hint(results),
            results=results,
            selected_drawing_number=state.selected_drawing_number,
        )

    def search_welds(self, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        state = self._get_state(incoming)
        drawing_number = arguments.get("drawing_number") or state.selected_drawing_number
        query = (arguments.get("query") or "").strip()
        rows = self.bridge.repository.search_welds(query=query, drawing_number=drawing_number, limit=10)
        results = [self.bridge.serialize_row(row) for row in rows]
        if len(results) == 1:
            state.selected_drawing_number = results[0]["drawing_number"]
            state.selected_weld_id = results[0]["weld_id"]
            self.store.save_conversation(state)
        return self._success(
            reply_hint=_weld_search_hint(results, drawing_number),
            results=results,
            selected_drawing_number=state.selected_drawing_number,
            selected_weld_id=state.selected_weld_id,
        )

    def register_welds(self, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        state = self._get_state(incoming)
        drawing_number = arguments.get("drawing_number") or state.selected_drawing_number
        if not drawing_number:
            return self._error(
                "missing_drawing_context",
                "I do not have a unique drawing in context yet. Please provide the drawing number first.",
            )
        weld_ids = [str(value) for value in arguments.get("weld_ids", [])]
        result = self.bridge.progress_service.register_welds(
            drawing_number=drawing_number,
            weld_ids=weld_ids,
            location_description=arguments.get("location_description"),
            operator=incoming.user_id,
            note=arguments.get("note"),
            skip_existing=True,
        )
        created = result["created"]
        state.selected_drawing_number = drawing_number
        if len(created) == 1:
            state.selected_weld_id = created[0]
        self.store.save_conversation(state)
        return self._success(
            reply_hint=(
                f"Processed weld registration for drawing {drawing_number}. "
                f"Created: {', '.join(created) if created else 'none'}; "
                f"skipped existing: {', '.join(result['skipped_existing']) if result['skipped_existing'] else 'none'}."
            ),
            drawing_number=drawing_number,
            created=created,
            skipped_existing=result["skipped_existing"],
        )

    def update_weld_status(self, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        state = self._get_state(incoming)
        drawing_number = arguments.get("drawing_number") or state.selected_drawing_number
        weld_id = self.bridge.normalize_weld_id(arguments.get("weld_id") or state.selected_weld_id)
        if not drawing_number:
            return self._error(
                "missing_drawing_context",
                "I do not have a unique drawing in context yet. Please provide the drawing number first.",
            )
        if not weld_id:
            return self._error(
                "missing_weld_context",
                "I do not have a unique weld in context yet. Please provide the weld ID first.",
            )
        self._ensure_weld_exists(drawing_number, weld_id)
        event = self.bridge.progress_service.update_status(
            drawing_number=drawing_number,
            weld_id=weld_id,
            to_status=str(arguments["to_status"]),
            operator=incoming.user_id,
            note=arguments.get("note"),
        )
        state.selected_drawing_number = drawing_number
        state.selected_weld_id = weld_id
        self.store.save_conversation(state)
        return self._success(
            reply_hint=f"Updated weld status for {drawing_number}/{weld_id} to {event.to_status}.",
            drawing_number=drawing_number,
            weld_id=weld_id,
            to_status=event.to_status,
        )

    def update_inspection_status(self, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        state = self._get_state(incoming)
        drawing_number = arguments.get("drawing_number") or state.selected_drawing_number
        weld_id = self.bridge.normalize_weld_id(arguments.get("weld_id") or state.selected_weld_id)
        if not drawing_number:
            return self._error(
                "missing_drawing_context",
                "I do not have a unique drawing in context yet. Please provide the drawing number first.",
            )
        if not weld_id:
            return self._error(
                "missing_weld_context",
                "I do not have a unique weld in context yet. Please provide the weld ID first.",
            )
        self._ensure_weld_exists(drawing_number, weld_id)
        event = self.bridge.progress_service.update_inspection(
            drawing_number=drawing_number,
            weld_id=weld_id,
            inspection_status=str(arguments["inspection_status"]),
            operator=incoming.user_id,
            note=arguments.get("note"),
        )
        state.selected_drawing_number = drawing_number
        state.selected_weld_id = weld_id
        self.store.save_conversation(state)
        return self._success(
            reply_hint=f"Updated inspection status for {drawing_number}/{weld_id} to {event.to_status}.",
            drawing_number=drawing_number,
            weld_id=weld_id,
            inspection_status=event.to_status,
        )

    def link_photo(self, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        state = self._get_state(incoming)
        drawing_number = arguments.get("drawing_number") or state.selected_drawing_number
        weld_id = self.bridge.normalize_weld_id(arguments.get("weld_id") or state.selected_weld_id)
        attachment = self._attachment_by_id(incoming.attachments, arguments["attachment_id"])
        if not drawing_number:
            return self._error(
                "missing_drawing_context",
                "I do not have a unique drawing in context yet. Please provide the drawing number first.",
            )
        if not weld_id:
            return self._error(
                "missing_weld_context",
                "I do not have a unique weld in context yet. Please provide the weld ID first.",
            )
        if not attachment:
            return self._error("attachment_not_found", "I could not find the photo to attach.")
        self._ensure_weld_exists(drawing_number, weld_id)
        evidence = self.bridge.progress_service.link_photo(
            drawing_number=drawing_number,
            weld_id=weld_id,
            file_bytes=Path(attachment.local_path).read_bytes(),
            filename=attachment.file_name,
            linked_by=incoming.user_id,
            note=arguments.get("note"),
        )
        state.selected_drawing_number = drawing_number
        state.selected_weld_id = weld_id
        self.store.save_conversation(state)
        return self._success(
            reply_hint=f"Attached photo {evidence.photo_id} to {drawing_number}/{weld_id}.",
            drawing_number=drawing_number,
            weld_id=weld_id,
            photo_id=evidence.photo_id,
        )

    def list_review_items(self, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        state = self._get_state(incoming)
        drawing_number = arguments.get("drawing_number") or state.selected_drawing_number
        unresolved_only = bool(arguments.get("unresolved_only", True))
        rows = self.bridge.repository.list_review_queue(drawing_number, unresolved_only=unresolved_only)
        items: list[dict[str, Any]] = []
        for row in rows[:10]:
            row_dict = self.bridge.serialize_row(row)
            suggestion = self.bridge.review_service.suggest_review_item(row["review_id"], use_llm=False)
            items.append(
                {
                    "review_id": row_dict["review_id"],
                    "drawing_number": row_dict["drawing_number"],
                    "weld_id": row_dict["weld_id"],
                    "item_type": row_dict["item_type"],
                    "resolved_at": row_dict["resolved_at"],
                    "recommended_action": suggestion["heuristic"]["recommended_action"],
                    "summary": suggestion["heuristic"]["summary"],
                }
            )
        return self._success(
            reply_hint=_review_list_hint(items, drawing_number, len(rows)),
            items=items,
            total=len(rows),
        )

    def apply_review_action(self, arguments: dict[str, Any], incoming: IncomingMessage) -> dict[str, Any]:
        review_id = str(arguments["review_id"])
        action = str(arguments["action"]).strip().lower()
        review_row = self.bridge.repository.get_review_item(review_id)
        if not review_row:
            return self._error("review_not_found", f"Review item not found: {review_id}")

        if action == "mark_resolved":
            self.bridge.repository.resolve_review_item(review_id)
            return self._success(reply_hint=f"Marked review item {review_id} as resolved.", review_id=review_id)

        if action == "keep_review_open":
            return self._success(reply_hint=f"Review item {review_id} remains open.", review_id=review_id)

        if action == "inspect_manually":
            return self._success(
                reply_hint=f"Review item {review_id} still needs manual review, so no automatic DB change was made.",
                review_id=review_id,
            )

        if action == "rerun_vlm":
            result = self.bridge.review_service.suggest_review_item(review_id, use_llm=True)
            final = result["final"]
            return self._success(
                reply_hint=f"M5 suggested action: {final.get('model_recommended_action') or final.get('recommended_action')}",
                review_id=review_id,
                llm=result["llm"],
                final=final,
            )

        if action == "register_welds":
            heuristic = self.bridge.review_service.suggest_review_item(review_id, use_llm=False)["heuristic"]
            weld_ids = [str(value) for value in arguments.get("weld_ids") or heuristic["candidate_weld_ids"]]
            drawing_number = review_row["drawing_number"]
            if not drawing_number:
                return self._error(
                    "missing_drawing_context",
                    "This review item has no drawing_number, so I cannot register welds automatically.",
                )
            result = self.bridge.progress_service.register_welds(
                drawing_number=drawing_number,
                weld_ids=weld_ids,
                operator=incoming.user_id,
                note=arguments.get("note") or f"Accepted from review item {review_id}.",
                skip_existing=True,
            )
            self.bridge.repository.resolve_review_item(review_id)
            return self._success(
                reply_hint=(
                    f"Processed review item {review_id}. "
                    f"Created: {', '.join(result['created']) if result['created'] else 'none'}; "
                    f"skipped existing: {', '.join(result['skipped_existing']) if result['skipped_existing'] else 'none'}."
                ),
                review_id=review_id,
                created=result["created"],
                skipped_existing=result["skipped_existing"],
            )

        return self._error("unsupported_review_action", f"Unsupported review action: {action}")

    def _attachment_by_id(self, attachments: list[BotAttachment], attachment_id: str) -> BotAttachment | None:
        for attachment in attachments:
            if attachment.attachment_id == attachment_id:
                return attachment
        return None

    def _ensure_weld_exists(self, drawing_number: str, weld_id: str) -> None:
        row = self.bridge.repository.get_weld(drawing_number, weld_id)
        if not row:
            raise ValueError(f"Weld not found: {drawing_number}/{weld_id}")

    def _get_state(self, incoming: IncomingMessage):
        return self.store.get_conversation(incoming.channel, incoming.chat_id, self.config.agent.state_ttl_hours)

    def _success(self, reply_hint: str, **payload: Any) -> dict[str, Any]:
        return {"ok": True, "reply_hint": reply_hint, **payload}

    def _error(self, code: str, message: str) -> dict[str, Any]:
        return {"ok": False, "error_code": code, "error_message": message, "reply_hint": message}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _drawing_search_hint(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No matching drawings found."
    if len(results) == 1:
        row = results[0]
        return f"Located drawing {row['drawing_number']}."
    labels = [row["drawing_number"] for row in results[:5]]
    return "Found multiple drawings: " + ", ".join(labels)


def _weld_search_hint(results: list[dict[str, Any]], drawing_number: str | None) -> str:
    if not results:
        return "No matching welds found."
    if len(results) == 1:
        row = results[0]
        return f"Located weld {row['drawing_number']}/{row['weld_id']}."
    labels = [f"{row['drawing_number']}/{row['weld_id']}" for row in results[:5]]
    prefix = f"Within drawing {drawing_number}, " if drawing_number else ""
    return f"{prefix}found multiple welds: " + ", ".join(labels)


def _review_list_hint(items: list[dict[str, Any]], drawing_number: str | None, total: int) -> str:
    if not items:
        return "There are no open review items right now."
    scope = f"Open review items for drawing {drawing_number}" if drawing_number else "Open review items"
    lines = [f"{scope} ({total})", ""]
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. {item['summary']}")
    return "\n".join(lines)
