# Weld Traceability Agent

Telegram-first agent layer for the weld traceability workflow.

This repository does not replace the original `weld-traceability-assistant` project. It adds an agent shell on top of it:

- OpenAI handles intent understanding, tool selection, and short operator-facing replies.
- The local assistant handles OCR, drawing parsing, structured validation, review queue generation, and SQLite persistence.
- The same SQLite database can still be opened from the original Streamlit-based assistant UI.

## What Runs Where

Default runtime behavior:

- OpenAI API: agent reasoning and tool orchestration
- Local `weld_assistant`: OCR, drawing parsing, review heuristics, DB writes
- Local SQLite: shared drawings, welds, photos, and review items
- Local Ollama / VLM: optional, disabled by default

Current defaults come from:

- [config/agent_config.yaml](D:\study\Weld_map_understanding_and_weld_traceability_assistant_agent\config\agent_config.yaml)
- [config/shared_assistant_config.yaml](D:\study\Weld_map_understanding_and_weld_traceability_assistant_agent\config\shared_assistant_config.yaml)

Important:

- If `openai.enabled: true` and `OPENAI_API_KEY` is set, the bot uses OpenAI for the agent layer.
- If OpenAI is unavailable, the project falls back to a local English heuristic router.
- `vlm.enabled` in the shared assistant config controls local Ollama/VLM use inside the original assistant pipeline. It is `false` by default.

## Key Behaviors

- Drawing imports always create a draft first. The user must explicitly confirm import.
- Bare image uploads are auto-routed:
  - preview OCR token count `>= 20` -> treat as a drawing
  - preview OCR token count `< 20` -> treat as a weld photo
- Telegram no longer sends an eager ack message.
  - If processing finishes quickly, only the final result is sent.
  - If processing takes longer than 5 seconds, the bot sends `Processing...` and later edits that same message into the result.
- Review listing is available with `list review`, `show review`, or `review items`.

## Repository Layout

- `src/weld_traceability_agent/`: agent runtime
- `config/agent_config.yaml`: agent-side config
- `config/shared_assistant_config.yaml`: shared assistant config used by the original parsing stack
- `config/roi_template_default.json`: default ROI template required by manual layout mode
- `tests/`: test suite

## Requirements

- Python 3.12
- Git
- A Telegram bot token from BotFather
- An OpenAI API key if you want the OpenAI agent layer enabled
- Optional: a local Ollama setup if you want local VLM support inside the original assistant

## Installation

```powershell
cd D:\study\Weld_map_understanding_and_weld_traceability_assistant_agent
python -m pip install -e .
```

## Step 1: Configure the Shared Assistant

Edit [config/shared_assistant_config.yaml](D:\study\Weld_map_understanding_and_weld_traceability_assistant_agent\config\shared_assistant_config.yaml).

Fields to check first:

- `pipeline.data_root`: intermediate working data for the original assistant pipeline
- `layout.manual_roi_config`: ROI template path, default is `config/roi_template_default.json`
- `database.path`: the shared SQLite database used by both the bot and the original assistant UI
- `export.output_dir`: exported outputs

Example:

```yaml
database:
  path: "D:/study/Weld_map_understanding_and_weld_traceability_assistant/data/db/weld_traceability.db"
```

If you want the agent and the old Streamlit UI to see the same data, point both systems to the same SQLite file.

## Step 2: Configure the Agent

Edit [config/agent_config.yaml](D:\study\Weld_map_understanding_and_weld_traceability_assistant_agent\config\agent_config.yaml).

Most important fields:

- `assistant.config_path`: usually `config/shared_assistant_config.yaml`
- `agent.runtime_db_path`: runtime state DB for the bot itself, not the business DB
- `agent.drafts_dir`: where unconfirmed drawing drafts are stored
- `agent.inbox_dir`: downloaded Telegram files
- `agent.use_local_vlm_for_parse`: whether the local assistant parse pipeline may use local VLM
- `openai.enabled`: whether the agent layer uses OpenAI
- `openai.model`: default is `gpt-5.4-mini`

## Step 3: Set Environment Variables

Minimum setup:

```powershell
$env:OPENAI_API_KEY = "your_openai_api_key"
$env:TELEGRAM_BOT_TOKEN = "your_telegram_bot_token"
$env:ALLOWED_USERS = "6863032327"
```

Optional allowlists:

```powershell
$env:ALLOWED_CHATS = "6863032327"
$env:ADMIN_CHATS = "6863032327"
```

Notes:

- `ALLOWED_USERS` is the Telegram numeric user ID, not the username
- `ALLOWED_CHATS` is the Telegram chat ID
- If both allowlists are empty, the bot accepts all users and chats
- `ADMIN_CHATS` receives review alerts when a confirmed drawing creates review items

## Step 4: Local CLI Smoke Test

Before running Telegram, verify the end-to-end path locally.

Import a real drawing image:

```powershell
python -m weld_traceability_agent.cli process-message `
  --chat-id 1001 `
  --user-id 6863032327 `
  --text "Please import this drawing." `
  --attachment "D:\path\to\real\drawing.jpg"
```

Then confirm the draft:

```powershell
python -m weld_traceability_agent.cli process-message `
  --chat-id 1001 `
  --user-id 6863032327 `
  --text "confirm import"
```

Useful details:

- `--message-id` is optional. If omitted, the CLI generates one automatically.
- The runtime de-duplicates messages by `(channel, chat_id, message_id)`.
- If you re-use the same `message_id`, the command may intentionally produce no work.
- Runtime state lives in `data/agent_runtime.db` by default.

## Step 5: Run the Telegram Bot

```powershell
python -m weld_traceability_agent.cli run-telegram
```

Once running, the bot will poll Telegram and process incoming messages in the background worker.

## Telegram Usage Examples

### Import a drawing

Send a drawing image with one of:

- `Please import this drawing.`
- `Parse this drawing.`
- no text at all, if the image clearly looks like a drawing

The bot will respond with a draft summary like:

```text
Draft created. Please confirm.

Drawing number: C-52
Source: recognized from drawing
Welds: 11
Review items: 3

Reply "confirm import" to continue, or send the correct drawing number.
```

If the drawing number was not confidently recognized, the message will explicitly say it is currently using the fallback document ID.

### Confirm import

```text
confirm import
```

### Update weld execution status

```text
W03 completed
W03 blocked
W03 in progress
```

### Update inspection status

```text
W03 inspection completed
W03 inspection rejected
```

### Attach a weld photo

Best practice is to send the weld photo with clear context, for example:

```text
This is a weld photo for C-52 W03
```

If the drawing and weld are already in the current chat context, sending a photo plus a short caption like `This is a weld photo` is enough.

If the bot classifies an uploaded image as a weld photo but does not have enough context, it will ask for the drawing number and weld ID.

### List review items

```text
list review
show review
review items
```

Example response:

```text
Open review items for drawing C-52 (3)

1. Review item rv_123 for C-52 needs manual confirmation.
2. Review item rv_456 for C-52 needs manual confirmation.
3. Review item rv_789 for C-52 needs manual confirmation.
```

## Image Auto-Routing

For ambiguous image uploads, the bot runs a lightweight preview OCR pass before choosing the path:

1. load image
2. preprocess image
3. build preview layout
4. extract OCR tokens
5. route the image

Routing rule:

- OCR token count `>= 20` -> drawing
- OCR token count `< 20` -> weld photo

This is intentionally a simple v1 rule. It avoids asking the user to label every image manually.

## Optional: Enable Local Ollama / VLM

If you want the original assistant pipeline to use a local VLM:

1. start Ollama
2. ensure the configured model is available
3. update [config/shared_assistant_config.yaml](D:\study\Weld_map_understanding_and_weld_traceability_assistant_agent\config\shared_assistant_config.yaml)

Example:

```yaml
vlm:
  enabled: true
  model: "qwen3.5:0.8b"
  mode: "review_only"
```

By default this is off. The project still works without local Ollama.

## Troubleshooting

### `run-telegram` exits immediately

Check the Telegram bot token:

```powershell
echo $env:TELEGRAM_BOT_TOKEN
```

If it is empty, the bot cannot start.

### CLI returns no messages

Most common reason: duplicate message de-duplication.

Use a fresh `--message-id`, or omit it and let the CLI generate one.

### Missing ROI template file

This project expects [config/roi_template_default.json](D:\study\Weld_map_understanding_and_weld_traceability_assistant_agent\config\roi_template_default.json) to exist because the shared assistant runs in manual layout mode by default.

### OpenAI is configured but the bot still behaves like a rule-based assistant

Check:

- `openai.enabled: true` in [config/agent_config.yaml](D:\study\Weld_map_understanding_and_weld_traceability_assistant_agent\config\agent_config.yaml)
- `OPENAI_API_KEY` is set

If OpenAI initialization fails, the agent intentionally falls back to the local heuristic adapter instead of stopping completely.

### Which model path is active right now?

Current behavior is mixed:

- Agent reasoning: OpenAI, when enabled and available
- OCR and drawing parsing: local original assistant
- Review queue and persistence: local original assistant
- Local VLM: only when enabled in the shared assistant config

## Tests

Run the full suite:

```powershell
python -m pytest -q
```

Latest local result during development:

```text
53 passed
```
