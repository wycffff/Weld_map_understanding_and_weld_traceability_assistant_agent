from __future__ import annotations

from pathlib import Path

import yaml

from weld_assistant.config import AppConfig
from weld_traceability_agent.assistant_bridge import _resolve_assistant_paths, classify_attachment_kind_from_token_count
from weld_traceability_agent.config import load_agent_config


def test_env_whitelist_overrides(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "agent_config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "security": {
                    "allowed_users": [],
                    "allowed_chats": [],
                    "admin_chats": [],
                },
                "openai": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ALLOWED_USERS", "6863032327, 7001")
    monkeypatch.setenv("ALLOWED_CHATS", "1001, 1002")
    monkeypatch.setenv("ADMIN_CHATS", "9001")

    config = load_agent_config(config_path)

    assert config.security.allowed_users == ["6863032327", "7001"]
    assert config.security.allowed_chats == ["1001", "1002"]
    assert config.security.admin_chats == ["9001"]


def test_assistant_relative_paths_resolve_from_project_root(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    assistant_config_path = config_dir / "config.yaml"

    config = AppConfig.model_validate(
        {
            "pipeline": {"data_root": "data/shared_assistant"},
            "layout": {"manual_roi_config": "config/roi_template_default.json"},
            "database": {"path": "data/shared/weld_traceability.db"},
            "export": {"output_dir": "data/shared_exports"},
        }
    )

    _resolve_assistant_paths(config, assistant_config_path)

    assert config.pipeline.data_root == str(tmp_path / "data" / "shared_assistant")
    assert config.layout.manual_roi_config == str(tmp_path / "config" / "roi_template_default.json")
    assert config.database.path == str(tmp_path / "data" / "shared" / "weld_traceability.db")
    assert config.export.output_dir == str(tmp_path / "data" / "shared_exports")


def test_attachment_classification_threshold_is_inclusive():
    assert classify_attachment_kind_from_token_count(19) == "weld_photo"
    assert classify_attachment_kind_from_token_count(20) == "drawing"
    assert classify_attachment_kind_from_token_count(21) == "drawing"
