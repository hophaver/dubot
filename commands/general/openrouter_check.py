import asyncio
import os
from typing import Dict, List, Tuple

import discord
import requests
from dotenv import dotenv_values

from commands.shared import _chunk_message, require_admin
from utils.http import OPENROUTER_BASE, OPENROUTER_CHAT_URL, openrouter_headers, session
from utils.paths import root_path
from models import model_manager
import integrations


def _mask_key(k: str) -> str:
    text = str(k or "")
    if len(text) <= 10:
        return "***"
    return f"{text[:6]}...{text[-4:]}"


def _parse_env() -> Dict[str, str]:
    """Raw .env values (normalized like integrations does), to spot shell-vs-file mismatches."""
    try:
        raw = dotenv_values(root_path(".env"))
    except Exception:
        return {}
    out: Dict[str, str] = {}
    for key, val in raw.items():
        v = integrations._normalize_secret(val or "")
        if v:
            out[key] = v
    return out


def _candidate_keys() -> List[Tuple[str, str]]:
    raw_env = _parse_env()
    seen = set()
    out: List[Tuple[str, str]] = []
    candidates = [
        ("integrations.OPENROUTER_API_KEY", getattr(integrations, "OPENROUTER_API_KEY", "")),
        ("env.OPENROUTER_API_KEY", os.environ.get("OPENROUTER_API_KEY", "")),
        ("env.OPENROUTER_KEY", os.environ.get("OPENROUTER_KEY", "")),
        ("env.OPENROUTER_APIKEY", os.environ.get("OPENROUTER_APIKEY", "")),
        ("dotenv.OPENROUTER_API_KEY", raw_env.get("OPENROUTER_API_KEY", "")),
        ("dotenv.OPENROUTER_KEY", raw_env.get("OPENROUTER_KEY", "")),
        ("dotenv.OPENROUTER_APIKEY", raw_env.get("OPENROUTER_APIKEY", "")),
        # Optional compatibility aliases are shown only after the primary key path.
        ("integrations.OPENROUTER_CHAT_API_KEY", getattr(integrations, "OPENROUTER_CHAT_API_KEY", "")),
        ("dotenv.OPENROUTER_CHAT_API_KEY", raw_env.get("OPENROUTER_CHAT_API_KEY", "")),
    ]
    for source, key in candidates:
        k = str(key or "").strip()
        if not k:
            continue
        if k in seen:
            continue
        seen.add(k)
        out.append((source, k))
    return out


def _extract_error_message(resp: requests.Response) -> str:
    text = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            err = body.get("error", {})
            if isinstance(err, dict) and err.get("message"):
                text = str(err.get("message", ""))
            elif body.get("message"):
                text = str(body.get("message", ""))
    except Exception:
        pass
    if not text:
        text = (resp.text or "").strip()
    return (text or f"HTTP {resp.status_code}")[:220]


def _test_credits(key: str) -> Tuple[bool, str]:
    try:
        r = session().get(f"{OPENROUTER_BASE}/credits", headers=openrouter_headers(key), timeout=20)
    except Exception as exc:
        return False, f"request error: {str(exc)[:160]}"
    if r.status_code == 200:
        return True, "OK"
    return False, f"{r.status_code}: {_extract_error_message(r)}"


def _test_chat(key: str, model_name: str) -> Tuple[bool, str]:
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Ping"}],
        "max_tokens": 12,
        "temperature": 0.0,
    }
    try:
        headers = {**openrouter_headers(key), "HTTP-Referer": "https://github.com/hophaver/dubot", "X-Title": "dubot"}
        r = session().post(OPENROUTER_CHAT_URL, headers=headers, json=payload, timeout=30)
    except Exception as exc:
        return False, f"request error: {str(exc)[:160]}"
    if r.status_code == 200:
        return True, "OK"
    # 402/404 means auth likely passed but model/credits issue.
    if r.status_code in {402, 404, 429}:
        return True, f"{r.status_code}: {_extract_error_message(r)} (auth likely OK)"
    return False, f"{r.status_code}: {_extract_error_message(r)}"


def _test_management_key_endpoint(key: str) -> Tuple[bool, str]:
    """Check whether key works against management keys API (typically management-only keys)."""
    try:
        r = session().get(f"{OPENROUTER_BASE}/keys", headers=openrouter_headers(key), timeout=20)
    except Exception as exc:
        return False, f"request error: {str(exc)[:160]}"
    if r.status_code == 200:
        return True, "OK"
    return False, f"{r.status_code}: {_extract_error_message(r)}"


def register(client):
    @client.tree.command(name="openrouter-check", description="Diagnose OpenRouter keys for chat and credits")
    async def openrouter_check(interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        model_info = model_manager.get_user_model_info(interaction.user.id)
        model_name = str(model_info.get("model", "") or "").strip()
        if not model_name or model_info.get("provider") != "cloud":
            model_name = "google/gemma-3-27b-it:free"

        keys = _candidate_keys()
        if not keys:
            await interaction.followup.send(
                "❌ No OpenRouter keys detected.\n"
                "Set `OPENROUTER_API_KEY` in `.env` (override vars are optional).",
                ephemeral=True,
            )
            return

        lines = [f"Testing {len(keys)} unique key(s). Chat probe model: `{model_name}`", ""]
        chat_ok = False
        credits_ok = False
        mgmt_api_ok = False
        async def _probe(key: str):
            return await asyncio.gather(
                asyncio.to_thread(_test_credits, key),
                asyncio.to_thread(_test_chat, key, model_name),
                asyncio.to_thread(_test_management_key_endpoint, key),
            )

        results = await asyncio.gather(*(_probe(key) for _, key in keys))
        for (source, key), ((c_ok, c_msg), (m_ok, m_msg), (g_ok, g_msg)) in zip(keys, results):
            credits_ok = credits_ok or c_ok
            chat_ok = chat_ok or m_ok
            mgmt_api_ok = mgmt_api_ok or g_ok
            lines.append(f"- `{source}` `{_mask_key(key)}`")
            lines.append(f"  credits: {'OK' if c_ok else 'FAIL'} ({c_msg})")
            lines.append(f"  chat: {'OK' if m_ok else 'FAIL'} ({m_msg})")
            lines.append(f"  management-api (/keys): {'OK' if g_ok else 'FAIL'} ({g_msg})")

        lines.append("")
        if chat_ok and credits_ok:
            lines.append("✅ Chat and credits both have at least one working key.")
        elif credits_ok and not chat_ok:
            lines.append("⚠️ Credits check works, but no key passed chat auth.")
            lines.append("Use a valid OpenRouter API key in `OPENROUTER_API_KEY`.")
            if mgmt_api_ok:
                lines.append(
                    "This key appears to be a Management API key. "
                    "Create a normal API key at https://openrouter.ai/settings/keys for chat completions."
                )
        elif chat_ok and not credits_ok:
            lines.append("⚠️ Chat works, credits endpoint failed for all keys.")
            lines.append("`/bal` may require account permissions; key itself can still be valid for chat.")
        else:
            lines.append("❌ No candidate key worked for chat or credits.")

        for chunk in _chunk_message("\n".join(lines)):
            await interaction.followup.send(chunk, ephemeral=True)
