# Weld Traceability Agent

Telegram-first agent layer for weld traceability workflows.

## What It Does

- Reuses the existing `weld_assistant` pipeline for OCR, VLM, fusion, review, and SQLite persistence.
- Adds a draft-first agent workflow: uploaded drawings are parsed into drafts, then imported only after explicit confirmation.
- Exposes coarse business tools for drawing intake, weld updates, photo linking, review listing, and review actions.
- Persists conversation state, draft state, task state, and processed message IDs in a separate runtime SQLite database.
- Includes an OpenAI Responses API adapter for tool-using turns and a heuristic fallback adapter for offline/test mode.
- Includes an async `python-telegram-bot` polling entrypoint with queue-based background workers.

## Workspace Layout

- `src/weld_traceability_agent/`: new agent package
- `config/agent_config.yaml`: agent runtime config
- `config/shared_assistant_config.yaml`: shared OCR/DB/export config for the reused assistant pipeline
- `tests/`: runtime and message-flow tests
- `source_repo/`: local checkout of the original weld assistant project

## Setup

1. The project can run in either of these modes:
   - installed dependency mode: it will install the original assistant package from the pinned Git commit in `pyproject.toml`
   - local dev mode: if a local checkout exists at `source_repo/`, bootstrap will prefer that code
2. Install the agent package:

```powershell
python -m pip install -e .
```

3. Edit `config/shared_assistant_config.yaml` so `database.path` points to the SQLite file you want to share with the original Streamlit system.
4. If you prefer using the original repo's existing config file directly, update `assistant.config_path` in `config/agent_config.yaml`.
5. Set `OPENAI_API_KEY` if you want OpenAI tool-using turns.
6. Set `TELEGRAM_BOT_TOKEN` if you want to run the Telegram bot.
7. Review `config/agent_config.yaml` and set whitelist/admin chats.

## Run

Process a local message through the agent:

```powershell
python -m weld_traceability_agent.cli process-message `
  --chat-id 1001 `
  --user-id 501 `
  --message-id msg-1 `
  --text "确认录入"
```

Run the Telegram bot:

```powershell
python -m weld_traceability_agent.cli run-telegram
```

## Tests

```powershell
python -m pytest -q
```
