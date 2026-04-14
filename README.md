# Dubot

Discord/Telegram bot with LLM chat and various features

## Requirements

- Python 3
- Discord bot token or Telegram bot token
- Ollama

### minimum system requirements
- raspberry pi 4 2GB (LLM has a really hard time)

## Setup

1. Setup `.env`
2. `python -m venv venv`
3. `source venv/bin/activate`
4. `pip install -r requirements.txt`
5. Run: `sh start.sh`

### Multi-platform mode

- Add `TELEGRAM_BOT_TOKEN` to `.env`
- If both `DISCORD_BOT_TOKEN` and `TELEGRAM_BOT_TOKEN` are set, `start.sh` runs both bots simultaneously
- Optional: set `BOT_PLATFORM=both` to force both
- Optional: set `BOT_PLATFORM=telegram` to force Telegram only
- Optional: set `BOT_PLATFORM=discord` to force Discord only
- If only Telegram token is set, `start.sh` auto-selects `main_telegram.py`

## Config

- **`.env`** – Secrets
- **`config.json`** – Wake word, startup channel, download limit, etc.
- **`model_fallback.json`** – Model fallback
- **`system_prompts.json`** – System prompts
- **`whitelist.json`** – Permissions
- **`data/ha_mappings.json`** – HA friendly name → entity_id (from `/explain`)
- **`data/ha_entities_allowlist.json`** – Optional

### reliability defaults (recommended)
- keep `OLLAMA_URL` pointed to a reachable Ollama host
- set at least one local fallback model in `model_fallback.json`
- if using cloud models, set `OPENROUTER_API_KEY`
- use `/reliability` to inspect retry/timeout/error counters

## Commands (summary)

- **Use** `/help`

### Commands have different permission levels
- **permanent admin** hardcoded admin
- **admin** (everything)
- **himas** (HA + user)
- **user** (basic)

## Structure

- **`main.py`** – Entry point, command registration, events
- **`commands/`** – Slash commands:
  - `general/` – status, checkwake
  - `file/` – analyze, examine, interrogate, code_review, ocr, compare_files
  - `chat/` – chat, forget
  - `reminder/` – remind, reminders, cancel_reminder
  - `persona/` – persona, persona_create
  - `model/` – model, pull_model
  - `download/` – download, download_limit
  - `translate/` – translate
  - `scripts/` – scripts, run
  - `admin/` – whitelist, update, purge, restart, kill, setwake, sethome, setstatus
  - `ha/` – himas, explain, listentities, removeentity, ha_status, find_sensor
  - `help/` – help
- **`commands/shared.py`** – Shared helpers
- **`utils/llm_service.py`** – LLM calls, fallback, vision, file analysis
- **`utils/ha_integration.py`** – Home Assistant parsing and control
- **`conversations.py`** – Per-channel conversation history
- **`models.py`** – User model preferences
- **`personas.py`** – Persona definitions
- **`services/`** – Reminder service

## Reliability notes

- Discord message sends are retried automatically on transient HTTP failures.
- LLM calls retry transient errors and model fallback is used when a model is unavailable.
- DMs support compacted long history summaries to keep context while reducing token load.
- File and image flows are handled through shared LLM service code paths, so behavior is consistent between chat and file commands.
