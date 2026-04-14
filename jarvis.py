import json
import os
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional


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
                "pending_confirmation": None,
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

    def update_profile_from_message(self, user_id: int, text: str) -> None:
        """Lightweight preference/tone extraction from DM user text."""
        if not text:
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
        profile = self.get_profile(user_id)
        if not profile:
            return ""
        lines = ["Jarvis DM profile (learned from this user):"]
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

    def set_pending_confirmation(self, user_id: int, payload: Optional[Dict[str, Any]]) -> None:
        user_state = self._get_user_state(user_id)
        user_state["pending_confirmation"] = payload
        self.save()

    def get_pending_confirmation(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self._get_user_state(user_id).get("pending_confirmation")

    def clear_pending_confirmation(self, user_id: int) -> None:
        self.set_pending_confirmation(user_id, None)

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
