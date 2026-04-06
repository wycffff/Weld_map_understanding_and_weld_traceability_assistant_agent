from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AssistantSection(BaseModel):
    repo_path: str = "source_repo"
    config_path: str = "source_repo/config/config.yaml"
    auto_bootstrap: bool = True


class AgentSection(BaseModel):
    data_root: str = "data"
    runtime_db_path: str = "data/agent_runtime.db"
    drafts_dir: str = "data/drafts"
    inbox_dir: str = "data/inbox"
    max_tool_rounds: int = 6
    state_ttl_hours: int = 24


class SecuritySection(BaseModel):
    allowed_users: list[str] = Field(default_factory=list)
    allowed_chats: list[str] = Field(default_factory=list)
    admin_chats: list[str] = Field(default_factory=list)


class ReviewSection(BaseModel):
    notify_submitter: bool = True
    notify_admins: bool = True


class OpenAISection(BaseModel):
    enabled: bool = True
    api_key_env: str = "OPENAI_API_KEY"
    model: str = "gpt-5.4-mini"
    reasoning_effort: str = "low"
    temperature: float = 0.0
    max_output_tokens: int = 700
    timeout_sec: int = 90


class TelegramSection(BaseModel):
    enabled: bool = False
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    ack_text: str = "已收到，正在处理中。"
    worker_count: int = 1


class AgentAppConfig(BaseModel):
    assistant: AssistantSection = Field(default_factory=AssistantSection)
    agent: AgentSection = Field(default_factory=AgentSection)
    security: SecuritySection = Field(default_factory=SecuritySection)
    review: ReviewSection = Field(default_factory=ReviewSection)
    openai: OpenAISection = Field(default_factory=OpenAISection)
    telegram: TelegramSection = Field(default_factory=TelegramSection)

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return Path.cwd() / path


def load_agent_config(config_path: str | Path = "config/agent_config.yaml") -> AgentAppConfig:
    path = Path(config_path)
    if not path.exists():
        return AgentAppConfig()
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return AgentAppConfig.model_validate(data)

