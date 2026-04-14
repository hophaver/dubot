import os
import re
from typing import Dict, List, Tuple

import discord
from discord import app_commands
import requests

from whitelist import get_user_permission
from models import model_manager
import integrations


def _mask_key(k: str) -> str:
    text = str(k or "")
    if len(text) <= 10:
        return "***"
    return f"{text[:6]}...{text[-4:]}"


def _parse_env(path: str = ".env") -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not os.path.isfile(path):
        return values
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
                if not m:
                    continue
                key, val = m.group(1), m.group(2).strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                else:
                    if " #" in val:
                        val = val.split(" #", 1)[0].rstrip()
                val = val.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
                val = "".join(ch for ch in val if ch.isprintable())
                val = re.sub(r"\s+", "", val)
                if val:
                    values[key] = val
    except Exception:
        return {}
    return values


def _candidate_keys() -> List[Tuple[str, str]]:
    raw_env = _parse_env(".env")
    seen = set()
    out: List[Tuple[str, str]] = []
    candidates = [
        ("integrations.OPENROUTER_CHAT_API_KEY", getattr(integrations, "OPENROUTER_CHAT_API_KEY", "")),
        ("integrations.OPENROUTER_API_KEY", getattr(integrations, "OPENROUTER_API_KEY", "")),
        ("integrations.OPENROUTER_LEGACY_API_KEY", getattr(integrations, "OPENROUTER_LEGACY_API_KEY", "")),
        ("env.OPENROUTER_CHAT_API_KEY", os.environ.get("OPENROUTER_CHAT_API_KEY", "")),
        ("env.OPENROUTER_API_KEY", os.environ.get("OPENROUTER_API_KEY", "")),
        ("env.OPENROUTER_KEY", os.environ.get("OPENROUTER_KEY", "")),
        ("env.OPENROUTER_APIKEY", os.environ.get("OPENROUTER_APIKEY", "")),
        ("dotenv.OPENROUTER_CHAT_API_KEY", raw_env.get("OPENROUTER_CHAT_API_KEY", "")),
        ("dotenv.OPENROUTER_API_KEY", raw_env.get("OPENROUTER_API_KEY", "")),
        ("dotenv.OPENROUTER_KEY", raw_env.get("OPENROUTER_KEY", "")),
        ("dotenv.OPENROUTER_APIKEY", raw_env.get("OPENROUTER_APIKEY", "")),
        ("integrations.OPENROUTER_MANAGEMENT_API_KEY", getattr(integrations, "OPENROUTER_MANAGEMENT_API_KEY", "")),
        ("env.OPENROUTER_MANAGEMENT_API_KEY", os.environ.get("OPENROUTER_MANAGEMENT_API_KEY", "")),
        ("dotenv.OPENROUTER_MANAGEMENT_API_KEY", raw_env.get("OPENROUTER_MANAGEMENT_API_KEY", "")),
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
        r = requests.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20,
        )
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
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/hophaver/dubot",
                "X-Title": "dubot",
            },
            json=payload,
            timeout=30,
        )
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
        r = requests.get(
            "https://openrouter.ai/api/v1/keys",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=20,
        )
    except Exception as exc:
        return False, f"request error: {str(exc)[:160]}"
    if r.status_code == 200:
        return True, "OK"
    return False, f"{r.status_code}: {_extract_error_message(r)}"


def register(client):
    @client.tree.command(name="openrouter-check", description="Diagnose OpenRouter keys for chat and credits")
    async def openrouter_check(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
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
        for source, key in keys:
            c_ok, c_msg = _test_credits(key)
            m_ok, m_msg = _test_chat(key, model_name)
            g_ok, g_msg = _test_management_key_endpoint(key)
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

        message = "\n".join(lines)
        if len(message) > 1900:
            message = message[:1900] + "\n..."
        await interaction.followup.send(message, ephemeral=True)
