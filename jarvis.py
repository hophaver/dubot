import json
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

# Minimal base instructions when adaptive DM assistant is on (replaces global persona for that DM).
ADAPTIVE_DM_BASE_PERSONA = (
    "You are the user's private DM assistant. "
    "Follow the user-specific context and behaviour instructions supplied below. "
    "Be accurate and helpful; use command and capability details from the system prompt when relevant."
)

# Appended to the system prompt when adaptive DM assistant is enabled (single source of truth).
ADAPTIVE_DM_SYSTEM_SUFFIX = (
    "\n\nFor this direct message conversation, keep your tone natural and conversational. "
    "Avoid formal assistant phrasing, avoid stiff closers, and do not call the user 'sir' or similar titles. "
    "Be proactive, keep continuity, and match the user's style."
)

_MANUAL_CONTEXT_MAX_LEN = 12000

# Lines at the start of exported status files to strip when pasting back.
_MANUAL_PASTE_HEADER_PREFIXES = (
    "this is the dm-specific",
    "adaptive assistant is currently off",
    "the following is what would be appended",
)


class JarvisManager:
    """DM-only per-user adaptive assistant state."""

    def __init__(self, save_file: str = "data/jarvis_state.json"):
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
            }
        return self.state[key]

    def is_enabled(self, user_id: int) -> bool:
        return bool(self._get_user_state(user_id).get("enabled", False))

    def set_enabled(self, user_id: int, enabled: bool) -> None:
        user_state = self._get_user_state(user_id)
        user_state["enabled"] = bool(enabled)
        self.save()

    def get_profile(self, user_id: int) -> Dict[str, Any]:
        return self._get_user_state(user_id).get("profile", {})

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
        self.save()

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

    def update_profile_from_message(self, user_id: int, text: str) -> None:
        """Lightweight preference/tone extraction from DM user text."""
        if not text:
            return
        if self.get_profile_manual_override(user_id):
            return
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

        # Cap growth.
        profile["preferred_name"] = preferred_name
        profile["likes"] = likes[-20:]
        profile["dislikes"] = dislikes[-20:]
        profile["tone_notes"] = tone_notes[-20:]
        user_state["profile"] = profile
        self.save()

    def get_profile_prompt(self, user_id: int) -> str:
        manual = self.get_profile_manual_override(user_id)
        if manual:
            low_start = manual.lstrip().lower()
            if low_start.startswith("user-specific context"):
                return manual
            return "User-specific context (manual):\n" + manual

        profile = self.get_profile(user_id)
        if not profile:
            return ""
        lines = ["User-specific context (learned from this user):"]
        preferred_name = str(profile.get("preferred_name", "") or "").strip()
        if preferred_name:
            lines.append(f"- Preferred name: {preferred_name}")
        likes = profile.get("likes", []) or []
        if likes:
            lines.append(f"- Likes: {', '.join(likes[:8])}")
        dislikes = profile.get("dislikes", []) or []
        if dislikes:
            lines.append(f"- Dislikes: {', '.join(dislikes[:8])}")
        tone_notes = profile.get("tone_notes", []) or []
        if tone_notes:
            lines.append(f"- Tone notes: {', '.join(tone_notes[:8])}")
        if len(lines) == 1:
            return ""
        lines.append("- Match the user's style naturally without being repetitive.")
        return "\n".join(lines)

    def get_full_jarvis_system_addition(self, user_id: int) -> str:
        """Text appended to the base persona+chat system prompt when adaptive DM assistant is on (learned + fixed behaviour)."""
        parts = []
        profile = self.get_profile_prompt(user_id)
        if profile:
            parts.append(profile)
        parts.append(ADAPTIVE_DM_SYSTEM_SUFFIX.strip())
        return "\n\n".join(parts)

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
            "has_manual_override": bool(self.get_profile_manual_override(user_id)),
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
        if not text:
            return
        if self.get_profile_manual_override(user_id):
            return
        user_state = self._get_user_state(user_id)
        queue = list(user_state.get("tone_tuning_queue", []) or [])
        cleaned = str(text).strip()
        if not cleaned:
            return
        queue.append(cleaned[:240])
        user_state["tone_tuning_queue"] = queue[-40:]
        self.save()

    def should_run_tone_tuning(self, user_id: int, min_messages: int = 8, min_interval_seconds: int = 900) -> bool:
        user_state = self._get_user_state(user_id)
        queue = user_state.get("tone_tuning_queue", []) or []
        last_ts = float(user_state.get("last_tone_tuning_ts", 0.0) or 0.0)
        if len(queue) < int(min_messages):
            return False
        return (time.time() - last_ts) >= int(min_interval_seconds)

    def run_tone_tuning_now(self, user_id: int, force: bool = False) -> bool:
        """Apply queued user samples to profile. Returns True when profile updated."""
        if self.get_profile_manual_override(user_id):
            return False
        user_state = self._get_user_state(user_id)
        queue = list(user_state.get("tone_tuning_queue", []) or [])
        if not queue:
            return False
        if (not force) and (not self.should_run_tone_tuning(user_id)):
            return False
        merged = "\n".join(queue[-20:])
        self.update_profile_from_message(user_id, merged)
        user_state["tone_tuning_queue"] = []
        user_state["last_tone_tuning_ts"] = time.time()
        user_state["tone_tuning_updates"] = int(user_state.get("tone_tuning_updates", 0) or 0) + 1
        self.save()
        return True

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


jarvis_manager = JarvisManager()
