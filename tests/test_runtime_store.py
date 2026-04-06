from __future__ import annotations

from pathlib import Path

from weld_traceability_agent.config import AgentAppConfig
from weld_traceability_agent.runtime_models import ConversationState, ProcessedMessage
from weld_traceability_agent.state_store import RuntimeStateStore


def test_runtime_store_roundtrip(tmp_path: Path):
    config = AgentAppConfig.model_validate(
        {
            "agent": {
                "runtime_db_path": str(tmp_path / "runtime.db"),
            },
            "openai": {"enabled": False},
        }
    )
    store = RuntimeStateStore(config)

    state = store.get_conversation("telegram", "1001", ttl_hours=12)
    state.selected_drawing_number = "C-52"
    store.save_conversation(state)

    loaded = store.get_conversation("telegram", "1001", ttl_hours=12)
    assert loaded.selected_drawing_number == "C-52"

    first = store.touch_processed_message(
        ProcessedMessage(channel="telegram", chat_id="1001", message_id="m-1")
    )
    second = store.touch_processed_message(
        ProcessedMessage(channel="telegram", chat_id="1001", message_id="m-1")
    )
    assert first is True
    assert second is False

