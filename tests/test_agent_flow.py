from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from weld_assistant.contracts import DrawingData, ProcessingLog, ReviewItem, StructuredDrawing, WeldItem
from weld_traceability_agent.config import load_agent_config
from weld_traceability_agent.orchestrator import AgentOrchestrator
from weld_traceability_agent.runtime_models import BotAttachment, IncomingMessage


def test_end_to_end_message_flow_with_fake_parse(tmp_path: Path):
    agent_config_path = _write_test_configs(tmp_path)
    orchestrator = AgentOrchestrator(load_agent_config(agent_config_path))

    fake_bridge = FakeBridge(orchestrator.bridge, _build_structured_drawing())
    orchestrator.bridge = fake_bridge
    orchestrator.toolbox.bridge = fake_bridge

    drawing_file = tmp_path / "drawing.jpg"
    drawing_file.write_bytes(b"fake-drawing")
    first_plan = orchestrator.process_message(
        IncomingMessage(
            channel="telegram",
            chat_id="1001",
            user_id="501",
            message_id="msg-1",
            text="这张图帮我录入",
            attachments=[
                BotAttachment(
                    attachment_id="att-1",
                    kind="image",
                    file_name=drawing_file.name,
                    local_path=str(drawing_file),
                )
            ],
        )
    )
    assert any("确认录入" in message.text for message in first_plan.messages)

    second_plan = orchestrator.process_message(
        IncomingMessage(
            channel="telegram",
            chat_id="1001",
            user_id="501",
            message_id="msg-2",
            text="确认录入",
        )
    )
    assert any("已录入图纸 C-52" in message.text for message in second_plan.messages)
    assert any(message.chat_id == "9001" for message in second_plan.messages)
    assert orchestrator.bridge.repository.get_drawing("C-52") is not None

    third_plan = orchestrator.process_message(
        IncomingMessage(
            channel="telegram",
            chat_id="1001",
            user_id="501",
            message_id="msg-3",
            text="W03 检验完成了",
        )
    )
    assert any("检验状态已更新为 accepted" in message.text for message in third_plan.messages)
    weld_row = orchestrator.bridge.repository.get_weld("C-52", "W03")
    assert weld_row is not None
    assert weld_row["inspection_status"] == "accepted"

    photo_file = tmp_path / "photo.jpg"
    photo_file.write_bytes(b"fake-photo")
    fourth_plan = orchestrator.process_message(
        IncomingMessage(
            channel="telegram",
            chat_id="1001",
            user_id="501",
            message_id="msg-4",
            text="这是焊口照片",
            attachments=[
                BotAttachment(
                    attachment_id="att-2",
                    kind="photo",
                    file_name=photo_file.name,
                    local_path=str(photo_file),
                )
            ],
        )
    )
    assert any("已把照片" in message.text for message in fourth_plan.messages)
    assert len(orchestrator.bridge.repository.list_photo_evidence("C-52", "W03")) == 1

    duplicate_plan = orchestrator.process_message(
        IncomingMessage(
            channel="telegram",
            chat_id="1001",
            user_id="501",
            message_id="msg-4",
            text="这是焊口照片",
        )
    )
    assert duplicate_plan.messages == []


def test_whitelist_blocks_unknown_chat(tmp_path: Path):
    agent_config_path = _write_test_configs(tmp_path, allowed_chats=["1001"])
    orchestrator = AgentOrchestrator(load_agent_config(agent_config_path))
    plan = orchestrator.process_message(
        IncomingMessage(
            channel="telegram",
            chat_id="2002",
            user_id="501",
            message_id="msg-x",
            text="hello",
        )
    )
    assert plan.messages == []


class FakeBridge:
    def __init__(self, real_bridge, structured: StructuredDrawing):
        self._real_bridge = real_bridge
        self._structured = structured
        self.repository = real_bridge.repository
        self.progress_service = real_bridge.progress_service
        self.review_service = real_bridge.review_service
        self.exporter = real_bridge.exporter

    def __getattr__(self, item):
        return getattr(self._real_bridge, item)

    def parse_drawing(self, input_path, use_vlm: bool = True) -> StructuredDrawing:
        return StructuredDrawing.model_validate(self._structured.model_dump(mode="json"))


def _write_test_configs(tmp_path: Path, allowed_chats: list[str] | None = None) -> Path:
    assistant_data = tmp_path / "assistant_data"
    assistant_data.mkdir(parents=True, exist_ok=True)
    assistant_config_path = tmp_path / "assistant_config.yaml"
    assistant_config = {
        "pipeline": {"version": "0.1.0", "data_root": str(assistant_data)},
        "ocr": {"engine": "null"},
        "vlm": {"enabled": False},
        "database": {"path": str(assistant_data / "shared.db")},
        "export": {"output_dir": str(assistant_data / "exports")},
    }
    assistant_config_path.write_text(yaml.safe_dump(assistant_config), encoding="utf-8")

    agent_config_path = tmp_path / "agent_config.yaml"
    agent_config = {
        "assistant": {"config_path": str(assistant_config_path)},
        "agent": {
            "runtime_db_path": str(tmp_path / "runtime.db"),
            "drafts_dir": str(tmp_path / "drafts"),
            "inbox_dir": str(tmp_path / "inbox"),
        },
        "security": {
            "allowed_chats": allowed_chats or ["1001"],
            "admin_chats": ["9001"],
        },
        "openai": {"enabled": False},
    }
    agent_config_path.write_text(yaml.safe_dump(agent_config), encoding="utf-8")
    return agent_config_path


def _build_structured_drawing() -> StructuredDrawing:
    return StructuredDrawing(
        document_id="doc-c52",
        drawing=DrawingData(
            drawing_number="C-52",
            spool_name="C-52",
            drawing_type="simple_spool",
            drawing_type_supported=True,
        ),
        welds=[
            WeldItem(weld_id="W03", status="not_started", inspection_status="not_checked", confidence=0.95),
        ],
        needs_review_items=[
            ReviewItem(
                item_type="weld_ids_from_vlm",
                field="weld_id",
                message="Additional weld identifiers were supplied by VLM.",
            )
        ],
        processing_log=ProcessingLog(
            pipeline_version="0.1.0",
            processed_at=datetime.now(timezone.utc),
            ocr_engine="null",
            drawing_type="simple_spool",
            supported=True,
        ),
    )
