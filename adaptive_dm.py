import json
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

# Minimal base instructions when adaptive DM assistant is on (replaces global persona for that DM).
ADAPTIVE_DM_BASE_PERSONA = (
    "You are the user's private DM assistant. "
    "Follow the user-specific context and behaviour instructions supplied below. "
    "Be accurate and helpful; use command and capability details from the system prompt when relevant. "
    "Sound like a sharp human in DMs—direct, specific, not generic—without sounding like customer support or a tutorial."
)

# Appended to the system prompt when adaptive DM assistant is enabled (single source of truth).
ADAPTIVE_DM_SYSTEM_SUFFIX = (
    "\n\nFor this direct message conversation, keep your tone natural and conversational. "
    "Avoid formal assistant phrasing, stiff closers, and titles like sir/ma'am. "
    "Be proactive, keep continuity, and match the user's style. "
    "Use normal punctuation even when the user omits it. "
    "Avoid AI tells: over-explaining, numbered lists unless asked, 'happy to help', 'let me know if you need anything else'. "
    "Formatting: use only Discord markdown that renders in chat—**bold**, *italic*, __underline__, ~~strike~~, "
    "`inline code`, triple-backtick fenced code blocks, bullets with -, links as [label](https://example.com), optional ## headings. "
    "No HTML, no tables, no LaTeX or dollar-math; use Unicode for symbols and plain words for units."
)

# Aggressive tuning: batch queue + frequent structured nudges from single messages.
TUNE_MIN_MESSAGES = 3
TUNE_MIN_INTERVAL_SECONDS = 120
TUNE_QUEUE_ENTRY_MAX = 400
TUNE_QUEUE_MAX_ITEMS = 60

_MANUAL_CONTEXT_MAX_LEN = 12000
_CONTEXT_OVERRIDE_MAX_LEN = 48000


# Lines at the start of exported status files to strip when pasting back.
_MANUAL_PASTE_HEADER_PREFIXES = (
    "this is the dm-specific",
    "adaptive assistant is currently off",
    "the following is what would be appended",
)

# Strip URLs / angle-wrapped links so tuning ignores link-only noise.
_TUNING_URL_RE = re.compile(
    r"<https?://[^>\s]+>|https?://[^\s>]+|discord(?:app)?\.com/channels/\d+/\d+(?:/\d+)?",
    re.IGNORECASE,
)


def text_for_adaptive_tuning(raw: Optional[str]) -> Optional[str]:
    """Normalize user text for adaptive tuning: drop URLs, collapse whitespace, min length."""
    if not raw or not str(raw).strip():
        return None
    t = str(raw).strip()
    t = _TUNING_URL_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < 3:
        return None
    return t


def text_for_adaptive_tuning_batch(raw: Optional[str]) -> Optional[str]:
    """Like text_for_adaptive_tuning but keeps newlines (only strips URLs and normalizes spaces per line)."""
    if not raw or not str(raw).strip():
        return None
    t = str(raw).strip()
    t = _TUNING_URL_RE.sub("", t)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    lines_out: List[str] = []
    for line in t.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        lines_out.append(line)
    t = "\n".join(lines_out).strip()
    while "\n\n\n" in t:
        t = t.replace("\n\n\n", "\n\n")
    if len(t) < 3:
        return None
    return t


class AdaptiveDmManager:
    """DM-only per-user adaptive assistant state."""

    def __init__(self, save_file: str = "data/adaptive_state.json"):
        legacy = "data/jarvis_state.json"
        if save_file == "data/adaptive_state.json" and os.path.isfile(legacy) and not os.path.isfile(save_file):
            try:
                os.replace(legacy, save_file)
            except OSError:
                pass
        self.save_file = save_file
        self.state: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._load()

    @staticmethod
    def _key(user_id: int) -> str:
        return str(user_id)

    def _get_user_state(self, user_id: int) -> Dict[str, Any]:
        key = self._key(user_id)
        if key not in self.state:
            self.state[key] = {
                "enabled": False,
                "profile": {
                    "preferred_name": "",
                    "likes": [],
                    "dislikes": [],
                    "tone_notes": [],
                },
                "trusted_commands_no_confirm": [],
                "pending_confirmation": None,
                "tone_tuning_queue": [],
                "last_tone_tuning_ts": 0.0,
                "tone_tuning_updates": 0,
                "profile_manual_override": "",
                "status_reply_anchor": None,
                "adaptive_sync_display_name": "",
                "last_exported_persona_key": "",
                "tune_guild_channel_id": None,
                "tune_guild_channel_enabled": False,
                "pending_manual_merge": None,
                "context_manual_prefix": "",
                "context_override_body": "",
            }
        return self.state[key]

    def touch_adaptive_sync_display_name(self, user_id: int, display_name: str) -> None:
        """Remember Discord display/global name for the \"<name> adaptive\" persona export."""
        name = (display_name or "").strip()
        if not name:
            return
        st = self._get_user_state(user_id)
        if st.get("adaptive_sync_display_name") == name:
            return
        st["adaptive_sync_display_name"] = name
        self.save()

    def adaptive_export_persona_key(self, user_id: int, used_keys: Set[str]) -> str:
        """
        Personas.json key shown in /llm-settings persona list: \"<display_name> adaptive\" (e.g. \".dubyu adaptive\").
        If that label is already taken, uses \"<display_name> adaptive (<user_id>)\".
        """
        raw = str(self._get_user_state(user_id).get("adaptive_sync_display_name", "") or "").strip() or "user"
        raw = raw.replace("\n", " ").replace("\r", "").strip()
        if len(raw) > 72:
            raw = raw[:72].rstrip()
        primary = f"{raw} adaptive"
        if primary not in used_keys:
            used_keys.add(primary)
            return primary
        fallback = f"{raw} adaptive ({self._key(user_id)})"
        used_keys.add(fallback)
        return fallback

    def is_enabled(self, user_id: int) -> bool:
        return bool(self._get_user_state(user_id).get("enabled", False))

    def set_enabled(self, user_id: int, enabled: bool) -> None:
        user_state = self._get_user_state(user_id)
        user_state["enabled"] = bool(enabled)
        self.save()

    def get_profile(self, user_id: int) -> Dict[str, Any]:
        return self._get_user_state(user_id).get("profile", {})

    def get_profile_data_copy(self, user_id: int) -> Dict[str, Any]:
        """Deep copy of profile dict for snapshots."""
        p = self.get_profile(user_id)
        if not isinstance(p, dict):
            return {
                "preferred_name": "",
                "likes": [],
                "dislikes": [],
                "tone_notes": [],
            }
        return {
            "preferred_name": str(p.get("preferred_name", "") or ""),
            "likes": list(p.get("likes", []) or []),
            "dislikes": list(p.get("dislikes", []) or []),
            "tone_notes": list(p.get("tone_notes", []) or []),
        }

    def replace_profile_data(self, user_id: int, profile: Dict[str, Any]) -> None:
        """Replace adaptive profile dict (preferred_name, likes, dislikes, tone_notes) and save."""
        st = self._get_user_state(user_id)
        pn = str((profile or {}).get("preferred_name", "") or "").strip()
        likes = [str(x).strip() for x in ((profile or {}).get("likes") or []) if str(x).strip()][-24:]
        dislikes = [str(x).strip() for x in ((profile or {}).get("dislikes") or []) if str(x).strip()][-24:]
        tones = [str(x).strip() for x in ((profile or {}).get("tone_notes") or []) if str(x).strip()][-30:]
        st["profile"] = {
            "preferred_name": pn,
            "likes": likes,
            "dislikes": dislikes,
            "tone_notes": tones,
        }
        self.save()

    def set_pending_manual_merge(self, user_id: int, payload: Optional[Dict[str, Any]]) -> None:
        st = self._get_user_state(user_id)
        st["pending_manual_merge"] = payload
        self.save()

    def get_pending_manual_merge(self, user_id: int) -> Optional[Dict[str, Any]]:
        raw = self._get_user_state(user_id).get("pending_manual_merge")
        return raw if isinstance(raw, dict) else None

    def clear_pending_manual_merge(self, user_id: int) -> None:
        self.set_pending_manual_merge(user_id, None)

    def set_status_reply_anchor(self, user_id: int, channel_id: int, message_id: int) -> None:
        user_state = self._get_user_state(user_id)
        user_state["status_reply_anchor"] = {"channel_id": int(channel_id), "message_id": int(message_id)}
        self.save()

    def get_status_reply_anchor(self, user_id: int) -> Optional[Dict[str, int]]:
        raw = self._get_user_state(user_id).get("status_reply_anchor")
        if not raw or not isinstance(raw, dict):
            return None
        try:
            cid = int(raw.get("channel_id", 0))
            mid = int(raw.get("message_id", 0))
        except (TypeError, ValueError):
            return None
        if cid <= 0 or mid <= 0:
            return None
        return {"channel_id": cid, "message_id": mid}

    def get_profile_manual_override(self, user_id: int) -> str:
        return str(self._get_user_state(user_id).get("profile_manual_override", "") or "").strip()

    def set_profile_manual_override(self, user_id: int, text: str) -> None:
        user_state = self._get_user_state(user_id)
        cleaned = (text or "").strip()
        if len(cleaned) > _MANUAL_CONTEXT_MAX_LEN:
            cleaned = cleaned[:_MANUAL_CONTEXT_MAX_LEN]
        user_state["profile_manual_override"] = cleaned
        self.save()

    def clear_profile_manual_override(self, user_id: int) -> None:
        user_state = self._get_user_state(user_id)
        user_state["profile_manual_override"] = ""
        user_state["context_manual_prefix"] = ""
        user_state["context_override_body"] = ""
        user_state["pending_manual_merge"] = None
        self.save()

    def get_context_manual_prefix(self, user_id: int) -> str:
        return str(self._get_user_state(user_id).get("context_manual_prefix", "") or "").strip()

    def set_context_manual_prefix(self, user_id: int, text: str) -> None:
        """Fixed manual layer before live auto-learned block; not updated by message tuning."""
        st = self._get_user_state(user_id)
        cleaned = (text or "").strip()
        if len(cleaned) > _MANUAL_CONTEXT_MAX_LEN:
            cleaned = cleaned[:_MANUAL_CONTEXT_MAX_LEN]
        st["context_manual_prefix"] = cleaned
        self.save()

    def clear_context_manual_prefix(self, user_id: int) -> None:
        st = self._get_user_state(user_id)
        st["context_manual_prefix"] = ""
        self.save()

    def get_context_override_body(self, user_id: int) -> str:
        return str(self._get_user_state(user_id).get("context_override_body", "") or "").strip()

    def set_context_override_body(self, user_id: int, text: str) -> None:
        """Full adaptive-dm-context body without final suffix duplicate; merged with live auto on read."""
        st = self._get_user_state(user_id)
        cleaned = (text or "").strip()
        if len(cleaned) > _CONTEXT_OVERRIDE_MAX_LEN:
            cleaned = cleaned[:_CONTEXT_OVERRIDE_MAX_LEN]
        st["context_override_body"] = cleaned
        self.save()

    def clear_context_override_body(self, user_id: int) -> None:
        st = self._get_user_state(user_id)
        st["context_override_body"] = ""
        self.save()

    def apply_context_file_replace(
        self,
        user_id: int,
        body_core: str,
        *,
        reset_profile: bool = True,
    ) -> None:
        """Store full context body (no trailing suffix duplicate); clear legacy manual + prefix; optionally reset profile."""
        st = self._get_user_state(user_id)
        st["context_manual_prefix"] = ""
        st["profile_manual_override"] = ""
        if reset_profile:
            st["profile"] = {
                "preferred_name": "",
                "likes": [],
                "dislikes": [],
                "tone_notes": [],
            }
        cleaned = (body_core or "").strip()
        if len(cleaned) > _CONTEXT_OVERRIDE_MAX_LEN:
            cleaned = cleaned[:_CONTEXT_OVERRIDE_MAX_LEN]
        st["context_override_body"] = cleaned
        self.save()

    def restore_context_file_replace_state(
        self,
        user_id: int,
        *,
        profile: Dict[str, Any],
        override_body: str,
        manual_prefix: str,
        legacy_manual: str,
    ) -> None:
        st = self._get_user_state(user_id)
        if isinstance(profile, dict):
            self.replace_profile_data(user_id, profile)
        st = self._get_user_state(user_id)
        st["context_override_body"] = str(override_body or "")[:_CONTEXT_OVERRIDE_MAX_LEN]
        st["context_manual_prefix"] = str(manual_prefix or "")[:_MANUAL_CONTEXT_MAX_LEN]
        lm = str(legacy_manual or "").strip()
        st["profile_manual_override"] = lm[:_MANUAL_CONTEXT_MAX_LEN] if lm else ""
        self.save()

    @staticmethod
    def strip_status_export_file_headers(raw: str) -> str:
        """Remove adaptive-status file preamble lines only (keep body through fixed suffix)."""
        t = (raw or "").replace("\r\n", "\n").strip()
        lines = t.split("\n")
        while lines:
            low0 = lines[0].lower().strip()
            if not low0:
                lines.pop(0)
                continue
            if any(low0.startswith(p) for p in _MANUAL_PASTE_HEADER_PREFIXES):
                lines.pop(0)
                continue
            break
        return "\n".join(lines).strip()

    @staticmethod
    def normalize_pasted_manual_context(text: str) -> str:
        """Strip export headers and fixed behaviour suffix from a pasted status attachment."""
        t = (text or "").replace("\r\n", "\n").strip()
        low = t.lower()
        marker = "user-specific context"
        idx = low.find(marker)
        if idx != -1:
            t = t[idx:].lstrip()
        else:
            lines = t.split("\n")
            while lines:
                low0 = lines[0].lower().strip()
                if not low0:
                    lines.pop(0)
                    continue
                if any(low0.startswith(p) for p in _MANUAL_PASTE_HEADER_PREFIXES):
                    lines.pop(0)
                    continue
                break
            t = "\n".join(lines).strip()
        suffix = ADAPTIVE_DM_SYSTEM_SUFFIX.strip()
        if suffix and t.endswith(suffix):
            t = t[: -len(suffix)].rstrip()
        return t.strip()

    def validate_status_export_and_extract_manual(self, user_id: int, pasted: str) -> Tuple[bool, str, str]:
        """
        Require a full adaptive-dm-context body (profile block + fixed suffix), with auto-learned text unchanged.
        Returns (ok, error_code, manual_inner). error_code empty on success; manual_inner for set_profile_manual_override.
        """
        suffix = ADAPTIVE_DM_SYSTEM_SUFFIX.strip()
        body = self.strip_status_export_file_headers(pasted)
        if not body:
            return False, "empty", ""
        if not suffix or suffix not in body:
            return False, "missing_suffix", ""
        if not body.endswith(suffix):
            return False, "bad_suffix", ""
        core = body[: -len(suffix)].rstrip()
        auto_expected = (self._structured_profile_prompt(user_id) or "").strip()
        if auto_expected:
            if not core.endswith(auto_expected):
                return False, "auto_mismatch", ""
            rest = core[: -len(auto_expected)].rstrip()
            if rest.endswith("\n\n"):
                manual_outer = rest[:-2].rstrip()
            elif rest == "":
                manual_outer = ""
            else:
                return False, "auto_mismatch", ""
        else:
            manual_outer = core.strip()

        manual_inner = self.normalize_pasted_manual_context(manual_outer).strip()
        return True, "", manual_inner

    def parse_manual_merge_reply(self, user_id: int, pasted: str) -> Tuple[bool, str, str]:
        """
        Manual-only reply: user sends notes to merge into auto-learned profile.
        If a full export is pasted instead, extract manual when validation passes.
        Returns (ok, error_code, guidance_text).
        """
        body = self.strip_status_export_file_headers(pasted).strip()
        if not body:
            return False, "empty", ""
        ok_full, err_full, manual_from_full = self.validate_status_export_and_extract_manual(user_id, pasted)
        if ok_full:
            return True, "", manual_from_full
        if err_full in ("missing_suffix", "bad_suffix") and (
            "user-specific context" in body.lower() or "for this direct message" in body.lower()
        ):
            return False, "full_file_incomplete", ""
        if err_full == "auto_mismatch":
            return False, "auto_mismatch", ""
        guidance = self.normalize_pasted_manual_context(body).strip()
        if len(guidance) < 3:
            return False, "too_short", ""
        return True, "", guidance

    def set_guild_tune_channel(
        self,
        user_id: int,
        *,
        enabled: bool,
        channel_id: Optional[int] = None,
        clear_channel_id: bool = False,
    ) -> None:
        """Guild channel messages from this user may tune the same adaptive profile when enabled."""
        st = self._get_user_state(user_id)
        st["tune_guild_channel_enabled"] = bool(enabled)
        if clear_channel_id and not enabled:
            st["tune_guild_channel_id"] = None
        elif channel_id is not None:
            st["tune_guild_channel_id"] = int(channel_id)
        self.save()

    def get_guild_tune_channel_config(self, user_id: int) -> Dict[str, Any]:
        st = self._get_user_state(user_id)
        return {
            "channel_id": st.get("tune_guild_channel_id"),
            "enabled": bool(st.get("tune_guild_channel_enabled", False)),
        }

    def maybe_tune_from_guild_channel_message(
        self,
        channel_id: int,
        author_id: int,
        content: Optional[str],
    ) -> None:
        """If this user enabled guild-channel tuning and this is that channel, ingest (URLs ignored)."""
        if not self.is_enabled(author_id):
            return
        st = self._get_user_state(author_id)
        if not st.get("tune_guild_channel_enabled"):
            return
        tid = st.get("tune_guild_channel_id")
        if tid is None or int(tid) != int(channel_id):
            return
        self.apply_live_message_tune(author_id, content or "")

    def _update_profile_from_cleaned_text(self, user_id: int, text: str) -> None:
        """Apply heuristics to already-normalized text (single-line or multiline)."""
        user_state = self._get_user_state(user_id)
        profile = user_state.setdefault("profile", {})
        likes: List[str] = list(profile.get("likes", []))
        dislikes: List[str] = list(profile.get("dislikes", []))
        tone_notes: List[str] = list(profile.get("tone_notes", []))
        preferred_name = str(profile.get("preferred_name", "") or "")

        lower = text.lower()

        # Name preference (very simple extraction).
        m_name = re.search(r"\bcall me ([a-zA-Z0-9_\- ]{2,30})", text, flags=re.IGNORECASE)
        if m_name:
            preferred_name = m_name.group(1).strip()

        # Likes/dislikes extraction.
        for m in re.finditer(r"\b(?:i like|i love|my favorite is)\s+([^\n\.\!\?]{2,60})", lower):
            val = m.group(1).strip(" .,!?:;")
            if val and val not in likes:
                likes.append(val)

        for m in re.finditer(r"\b(?:i dislike|i hate|i don't like|i dont like)\s+([^\n\.\!\?]{2,60})", lower):
            val = m.group(1).strip(" .,!?:;")
            if val and val not in dislikes:
                dislikes.append(val)

        # Tone markers.
        if any(ch in text for ch in [":)", ":D", "xd", "XD", "😂", "🔥", "🙏", "😅", "🙂"]):
            if "often uses emojis or expressive markers" not in tone_notes:
                tone_notes.append("often uses emojis or expressive markers")
        if text == lower and len(text) > 10:
            if "prefers lowercase casual style" not in tone_notes:
                tone_notes.append("prefers lowercase casual style")
        if len(text.split()) <= 6:
            if "often writes short direct messages" not in tone_notes:
                tone_notes.append("often writes short direct messages")
        if len(text) > 12 and not re.search(r"[.!?]", text):
            if "often skips sentence-ending punctuation" not in tone_notes:
                tone_notes.append("often skips sentence-ending punctuation")
        slang_hits = (
            " ngl ",
            " tbh ",
            " imo ",
            " idk ",
            " kinda ",
            " sorta ",
            " nvm ",
            " fr ",
            " lowkey ",
        )
        if any(s in f" {lower} " for s in slang_hits) or lower.startswith(
            ("ngl ", "tbh ", "imo ", "lol", "lmao", "idk ")
        ):
            if "uses informal internet shorthand" not in tone_notes:
                tone_notes.append("uses informal internet shorthand")
        if "..." in text or re.search(r"\.{2,}", text):
            if "uses ellipses or multi-dot pauses" not in tone_notes:
                tone_notes.append("uses ellipses or multi-dot pauses")
        if len(text) > 80:
            if "sometimes sends longer messages" not in tone_notes:
                tone_notes.append("sometimes sends longer messages")

        # Cap growth.
        profile["preferred_name"] = preferred_name
        profile["likes"] = likes[-24:]
        profile["dislikes"] = dislikes[-24:]
        profile["tone_notes"] = tone_notes[-30:]
        user_state["profile"] = profile
        self.save()

    def update_profile_from_message(self, user_id: int, text: str) -> None:
        """Lightweight preference/tone extraction from DM user text (runs even when manual context is set)."""
        cleaned = text_for_adaptive_tuning(text)
        if not cleaned:
            return
        self._update_profile_from_cleaned_text(user_id, cleaned)
        self._refresh_auto_in_context_override(user_id)

    def apply_batch_tuning_text(self, user_id: int, raw: str) -> Tuple[bool, str]:
        """Ingest multiline / pasted corpus into the adaptive profile. Returns (ok, reason_code)."""
        if not self.is_enabled(user_id):
            return False, "adaptive_off"
        cleaned = text_for_adaptive_tuning_batch(raw)
        if not cleaned:
            return False, "empty"
        self._update_profile_from_cleaned_text(user_id, cleaned)
        st = self._get_user_state(user_id)
        st["tone_tuning_updates"] = int(st.get("tone_tuning_updates", 0) or 0) + 1
        st["last_tone_tuning_ts"] = time.time()
        self.save()
        self._refresh_auto_in_context_override(user_id)
        return True, "ok"

    def _format_manual_block(self, manual: str) -> str:
        manual = (manual or "").strip()
        if not manual:
            return ""
        low_start = manual.lstrip().lower()
        if low_start.startswith("user-specific context"):
            return manual
        return "User-specific context (manual):\n" + manual

    @staticmethod
    def _structured_profile_prompt_from_dict(profile: Optional[Dict[str, Any]]) -> str:
        if not profile or not isinstance(profile, dict):
            return ""
        lines = ["User-specific context (auto, learned from your messages):"]
        preferred_name = str(profile.get("preferred_name", "") or "").strip()
        if preferred_name:
            lines.append(f"- Preferred name: {preferred_name}")
        likes = profile.get("likes", []) or []
        if likes:
            lines.append(f"- Likes: {', '.join(str(x) for x in likes[:12])}")
        dislikes = profile.get("dislikes", []) or []
        if dislikes:
            lines.append(f"- Dislikes: {', '.join(str(x) for x in dislikes[:12])}")
        tone_notes = profile.get("tone_notes", []) or []
        if tone_notes:
            lines.append(f"- Tone notes: {', '.join(str(x) for x in tone_notes[:14])}")
        if len(lines) == 1:
            return ""
        lines.append("- Match the user's style naturally without being repetitive.")
        return "\n".join(lines)

    def _structured_profile_prompt(self, user_id: int) -> str:
        return self._structured_profile_prompt_from_dict(self.get_profile(user_id))

    def get_auto_profile_prompt_text(self, user_id: int) -> str:
        """Auto-learned block only (empty string if no structured profile yet)."""
        return (self._structured_profile_prompt(user_id) or "").strip()

    def build_full_addition_for_profile_dict(self, user_id: int, profile_dict: Dict[str, Any]) -> str:
        """Full adaptive addition (auto block + fixed suffix) for a hypothetical profile; ignores manual override."""
        parts = []
        auto = self._structured_profile_prompt_from_dict(profile_dict)
        if auto:
            parts.append(auto)
        parts.append(ADAPTIVE_DM_SYSTEM_SUFFIX.strip())
        return "\n\n".join(parts)

    def get_profile_prompt(self, user_id: int) -> str:
        auto = self._structured_profile_prompt(user_id)
        override = self.get_context_override_body(user_id)
        if override:
            if auto:
                return f"{override}\n\n{auto}"
            return override
        parts: List[str] = []
        fixed_prefix = self.get_context_manual_prefix(user_id)
        if fixed_prefix:
            parts.append(fixed_prefix)
        manual_raw = self.get_profile_manual_override(user_id)
        if manual_raw:
            manual = self._format_manual_block(manual_raw)
            parts.append(manual)
        if auto:
            parts.append(auto)
        if not parts:
            return ""
        return "\n\n".join(parts)

    @staticmethod
    def validate_full_context_attachment(filename: str, pasted: str) -> Tuple[bool, str, str]:
        """
        Strict full-file replace: exact filename adaptive-dm-context.txt, body ends with fixed suffix,
        and contains the auto-learned section header (user may edit that section).
        Returns (ok, err_code, body_without_suffix) for storage in context_override_body.
        """
        fn = (filename or "").strip().lower()
        if fn != "adaptive-dm-context.txt":
            return False, "bad_filename", ""
        suffix = ADAPTIVE_DM_SYSTEM_SUFFIX.strip()
        body = AdaptiveDmManager.strip_status_export_file_headers(pasted).strip()
        if not body:
            return False, "empty", ""
        if not suffix or not body.endswith(suffix):
            return False, "bad_suffix", ""
        core = body[: -len(suffix)].rstrip()
        low = core.lower()
        if "user-specific context (auto, learned from your messages)" not in low:
            return False, "missing_auto_header", ""
        return True, "", core

    def _refresh_auto_in_context_override(self, user_id: int) -> None:
        """Re-embed live auto-learned block into stored full-context override after tuning."""
        ov = self.get_context_override_body(user_id)
        if not ov:
            return
        low = ov.lower()
        marker = "user-specific context (auto, learned from your messages):"
        idx = low.find(marker)
        if idx == -1:
            return
        prefix = ov[:idx].rstrip()
        auto = (self._structured_profile_prompt(user_id) or "").strip()
        if auto:
            new_ov = f"{prefix}\n\n{auto}".strip() if prefix else auto
        else:
            new_ov = prefix
        self.set_context_override_body(user_id, new_ov)

    def apply_live_message_tune(self, user_id: int, text: str) -> None:
        """Per-message nudge plus queue sample (aggressive adaptivity). URLs stripped inside."""
        if not self.is_enabled(user_id):
            return
        self.update_profile_from_message(user_id, text)
        self.queue_user_message_for_tuning(user_id, text)

    def apply_batch_message_tune(self, user_id: int, batch_text: str) -> Dict[str, int]:
        """
        Ingest newline-separated samples like separate user messages (blank lines skipped; URLs stripped per line).
        Returns counts: messages (non-empty lines), applied (lines that contributed after normalization).
        """
        if not self.is_enabled(user_id):
            return {"messages": 0, "applied": 0}
        raw = (batch_text or "").replace("\r\n", "\n")
        messages = 0
        applied = 0
        for line in raw.split("\n"):
            segment = line.strip()
            if not segment:
                continue
            messages += 1
            if text_for_adaptive_tuning(segment) is None:
                continue
            self.apply_live_message_tune(user_id, segment)
            applied += 1
        return {"messages": messages, "applied": applied}

    def get_full_adaptive_system_addition(self, user_id: int) -> str:
        """Text appended to the base persona+chat system prompt when adaptive DM is on (learned + fixed behaviour)."""
        profile = self.get_profile_prompt(user_id)
        if not profile:
            return ADAPTIVE_DM_SYSTEM_SUFFIX.strip()
        suf = ADAPTIVE_DM_SYSTEM_SUFFIX.strip()
        if profile.rstrip().endswith(suf):
            return profile.strip()
        return f"{profile.strip()}\n\n{suf}"

    def get_status_snapshot(self, user_id: int) -> Dict[str, Any]:
        """Read-only snapshot for the DM assistant status command."""
        st = self._get_user_state(user_id)
        return {
            "enabled": bool(st.get("enabled", False)),
            "profile": dict(st.get("profile", {}) or {}),
            "trusted_commands": list(st.get("trusted_commands_no_confirm", []) or []),
            "pending_confirmation": st.get("pending_confirmation"),
            "tone_queue_len": len(st.get("tone_tuning_queue", []) or []),
            "tone_tuning_updates": int(st.get("tone_tuning_updates", 0) or 0),
            "last_tone_tuning_ts": float(st.get("last_tone_tuning_ts", 0.0) or 0.0),
            "has_manual_override": bool(self.get_profile_manual_override(user_id))
            or bool(self.get_context_manual_prefix(user_id))
            or bool(self.get_context_override_body(user_id)),
            "guild_tune_channel_id": self._get_user_state(user_id).get("tune_guild_channel_id"),
            "guild_tune_channel_enabled": bool(
                self._get_user_state(user_id).get("tune_guild_channel_enabled", False)
            ),
        }

    def set_pending_confirmation(self, user_id: int, payload: Optional[Dict[str, Any]]) -> None:
        user_state = self._get_user_state(user_id)
        user_state["pending_confirmation"] = payload
        self.save()

    def get_pending_confirmation(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self._get_user_state(user_id).get("pending_confirmation")

    def clear_pending_confirmation(self, user_id: int) -> None:
        self.set_pending_confirmation(user_id, None)

    def add_trusted_command(self, user_id: int, command_name: str) -> None:
        if not command_name:
            return
        user_state = self._get_user_state(user_id)
        trusted = list(user_state.get("trusted_commands_no_confirm", []) or [])
        name = str(command_name).strip().lower()
        if name not in trusted:
            trusted.append(name)
        user_state["trusted_commands_no_confirm"] = trusted[-40:]
        self.save()

    def remove_trusted_command(self, user_id: int, command_name: str) -> None:
        if not command_name:
            return
        user_state = self._get_user_state(user_id)
        name = str(command_name).strip().lower()
        trusted = [c for c in (user_state.get("trusted_commands_no_confirm", []) or []) if str(c).lower() != name]
        user_state["trusted_commands_no_confirm"] = trusted
        self.save()

    def is_trusted_no_confirm(self, user_id: int, command_name: str) -> bool:
        if not command_name:
            return False
        trusted = self._get_user_state(user_id).get("trusted_commands_no_confirm", []) or []
        name = str(command_name).strip().lower()
        return name in {str(c).strip().lower() for c in trusted}

    def queue_user_message_for_tuning(self, user_id: int, text: str) -> None:
        """Queue user-only samples for periodic tone tuning."""
        cleaned = text_for_adaptive_tuning(text)
        if not cleaned:
            return
        user_state = self._get_user_state(user_id)
        queue = list(user_state.get("tone_tuning_queue", []) or [])
        queue.append(cleaned[:TUNE_QUEUE_ENTRY_MAX])
        user_state["tone_tuning_queue"] = queue[-TUNE_QUEUE_MAX_ITEMS:]
        self.save()

    def should_run_tone_tuning(
        self,
        user_id: int,
        min_messages: Optional[int] = None,
        min_interval_seconds: Optional[int] = None,
    ) -> bool:
        min_messages = TUNE_MIN_MESSAGES if min_messages is None else int(min_messages)
        min_interval_seconds = (
            TUNE_MIN_INTERVAL_SECONDS if min_interval_seconds is None else int(min_interval_seconds)
        )
        user_state = self._get_user_state(user_id)
        queue = user_state.get("tone_tuning_queue", []) or []
        last_ts = float(user_state.get("last_tone_tuning_ts", 0.0) or 0.0)
        if len(queue) < int(min_messages):
            return False
        return (time.time() - last_ts) >= int(min_interval_seconds)

    def run_tone_tuning_now(self, user_id: int, force: bool = False) -> bool:
        """Apply queued user samples to profile. Returns True when profile updated."""
        user_state = self._get_user_state(user_id)
        queue = list(user_state.get("tone_tuning_queue", []) or [])
        if not queue:
            return False
        if (not force) and (not self.should_run_tone_tuning(user_id)):
            return False
        merged = "\n".join(queue[-24:])
        self.update_profile_from_message(user_id, merged)
        user_state["tone_tuning_queue"] = []
        user_state["last_tone_tuning_ts"] = time.time()
        user_state["tone_tuning_updates"] = int(user_state.get("tone_tuning_updates", 0) or 0) + 1
        self.save()
        return True

    def has_exportable_adaptive(self, user_id: int) -> bool:
        """True if this user has adaptive DM state worth syncing into personas.json."""
        st = self._get_user_state(user_id)
        if st.get("enabled"):
            return True
        if str(st.get("profile_manual_override", "") or "").strip():
            return True
        if str(st.get("context_manual_prefix", "") or "").strip():
            return True
        if str(st.get("context_override_body", "") or "").strip():
            return True
        p = st.get("profile") or {}
        if str(p.get("preferred_name", "") or "").strip():
            return True
        if p.get("likes") or p.get("dislikes") or p.get("tone_notes"):
            return True
        return False

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.save_file), exist_ok=True)
        with open(self.save_file, "w") as f:
            json.dump({"state": dict(self.state)}, f, indent=2)

    def _load(self) -> None:
        try:
            with open(self.save_file) as f:
                data = json.load(f)
            self.state = defaultdict(dict, data.get("state", {}))
        except (FileNotFoundError, json.JSONDecodeError):
            pass


adaptive_dm_manager = AdaptiveDmManager()


def export_adaptive_to_personas(persona_manager) -> None:
    """
    Upsert adaptive DM bundles into personas.json as \"<Discord display name> adaptive\".
    Drops legacy adaptive_dm_* keys and the previous export key if the display name changed.
    """
    try:
        legacy_prefix = "adaptive_dm_"
        used_keys: Set[str] = set()
        keys_written: Dict[str, str] = {}
        removed = False
        state_dirty = False

        for uid_str in list(adaptive_dm_manager.state.keys()):
            try:
                uid = int(uid_str)
            except (TypeError, ValueError):
                continue
            st = adaptive_dm_manager._get_user_state(uid)
            old_export = str(st.get("last_exported_persona_key", "") or "")

            if not adaptive_dm_manager.has_exportable_adaptive(uid):
                if old_export and old_export in persona_manager.personas:
                    del persona_manager.personas[old_export]
                    removed = True
                if old_export:
                    st["last_exported_persona_key"] = ""
                    state_dirty = True
                continue

            text = adaptive_dm_manager.get_full_adaptive_system_addition(uid).strip()
            if not text:
                if old_export and old_export in persona_manager.personas:
                    del persona_manager.personas[old_export]
                    removed = True
                if old_export:
                    st["last_exported_persona_key"] = ""
                    state_dirty = True
                continue

            new_key = adaptive_dm_manager.adaptive_export_persona_key(uid, used_keys)
            if old_export and old_export != new_key and old_export in persona_manager.personas:
                del persona_manager.personas[old_export]
                removed = True
            keys_written[new_key] = text
            if st.get("last_exported_persona_key") != new_key:
                st["last_exported_persona_key"] = new_key
                state_dirty = True

        for k in list(persona_manager.personas.keys()):
            if k.startswith(legacy_prefix):
                del persona_manager.personas[k]
                removed = True

        for k, v in keys_written.items():
            persona_manager.personas[k] = v
        if state_dirty:
            adaptive_dm_manager.save()
        if keys_written or removed:
            persona_manager.save_personas()
    except OSError:
        pass
