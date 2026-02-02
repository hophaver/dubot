# Dubot

Discord bot with LLM chat and various features

## Requirements

- Python 3
- Discord bot token
- Ollama

### minimum system requirements
- raspberry pi 4 2GB (LLM has a really hard time)

## Setup

1. Setup `.env`
2. `python -m venv venv`
4. `source venv/bin/activate`
5. `pip install -r requirements.txt`
6. Run: `sh start.sh`

## Config

- **`.env`** – Secrets
- **`config.json`** – Wake word, startup channel, download limit, etc.
- **`model_fallback.json`** – Model fallback
- **`system_prompts.json`** – System prompts
- **`whitelist.json`** – Permissions
- **`data/ha_mappings.json`** – HA friendly name → entity_id (from `/explain`)
- **`data/ha_entities_allowlist.json`** – Optional

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
