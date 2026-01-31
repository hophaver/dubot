# Dubot

Discord bot with LLM chat

## Requirements

- Python 3
- Discord bot token
- Ollama

## Setup

1. Setup `.env`
2. `pip install -r requirements.txt`
3. Run: `./start.sh`

## Config

- **`.env`** – Secrets
- **`config.json`** – Wake word, startup channel, download limit, etc.
- **`model_fallback.json`** – Model fallback
- **`system_prompts.json`** – System prompts
- **`whitelist.json`** – Permissions

## Commands (summary)

- **Use** `/help`

### Commands have different permission levels
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
