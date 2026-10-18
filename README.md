# Dubot

Discord bot with LLM chat (local Ollama or OpenRouter), adaptive DMs, news digests, reminders,
Home Assistant control and assorted admin tools.

## Requirements

- Python 3.10+
- A Discord bot token
- Ollama (for local models) and/or an OpenRouter API key

Minimum hardware: a Raspberry Pi 4 (2 GB) runs it, but local LLMs struggle.

## Setup

1. `cp .env.example .env` and fill in at least `DISCORD_BOT_TOKEN`
2. `python -m venv venv && source venv/bin/activate`
3. `pip install -r requirements.txt`
4. `./start.sh` (runs in the background; `./stop.sh` stops it and saves state)

Logs go to `bot-discord.log` (rotated to `bot-discord.log.1` past ~10 MB on start).

## Configuration

| File | Purpose |
| --- | --- |
| `.env` | Secrets and endpoints — see `.env.example` for every variable |
| `config.json` | Wake word, home channel, download limit, persona, auto-conversation (created on first run) |
| `model_fallback.json` | Local models to try when the chosen one is unavailable (largest first) |
| `system_prompts.json` | Prompt templates |

Runtime state lives in `data/` (git-ignored) and is written atomically; a corrupt file is kept as
`<name>.corrupt-<timestamp>` instead of being overwritten:

- `whitelist.json`, `personas.json`, `reminders.json`, `models.json`, `conversations.json`, `adaptive_state.json`
- `news/` (subscriptions, preferences, seen articles, digest queue)
- `ha_mappings.json` (friendly name → entity id, from `/explain`), `ha_entities_allowlist.json` (optional)
- `notes_state.json`, `clone_state.json`, `profanity.json`, `remover.json`, `update_state.json`

Older installs that kept `whitelist.json`, `personas.json` or `reminders.json` in the repo root are migrated on startup.

## Permissions

- **permanent admin** — hard-coded owner (`PERMANENT_ADMIN` in `integrations.py`)
- **admin** — everything
- **himas** — Home Assistant + user commands
- **user** — basic commands

## Commands

`/help` lists every registered command; `/help <name>` shows one. The list is generated from the
registered slash commands, so it is always current.

## Structure

- `main.py` — client, events, startup/shutdown
- `commands/` — one package per area; `commands/__init__.py` (`COMMAND_GROUPS`) is the registry
  - `commands/shared.py` — message chunking, permission guards (`require_user`, `require_admin`, …), embed/view helpers
- `platforms/discord_chat.py` — message routing (wake word, DMs, adaptive command planning, coalescing)
- `utils/llm_service.py` — chat entry points (`ask_llm`, `persist_turn`, …)
- `utils/llm/` — providers (Ollama/OpenRouter), fallback, prompts, files, DM memory, command catalogue
- `services/news/` — RSS fetch, scoring, summaries, delivery loop
- `services/` — reminders, notes, clone, profanity, remover, local status server
- `utils/json_store.py`, `utils/paths.py`, `utils/http.py` — persistence, paths, shared HTTP sessions
- `conversations.py`, `models.py`, `personas.py`, `adaptive_dm.py`, `whitelist.py`, `config.py` — state managers

## Reliability notes

- Discord sends are retried on transient HTTP failures; LLM calls retry transient errors and fall back
  to other local models (an unreachable Ollama fails fast).
- Long DM histories are compacted into topic summaries to keep context small.
- `/reliability` shows retry/timeout/error counters.
- The status server (`services/status_server.py`) listens on port 3000 on all interfaces and serves
  host details from `/status`; firewall it if the host is on an untrusted network.
