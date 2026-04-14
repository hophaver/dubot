import json
import os
import time
from typing import Dict, Tuple


UPDATE_STATE_FILE = "data/update_state.json"


class UpdateStateManager:
    def __init__(self, save_file: str = UPDATE_STATE_FILE):
        self.save_file = save_file
        self.state = {
            "safe_commit": "",
            "rollback_commit": "",
            "previous_commit": "",
            "last_update_from": "",
            "last_update_to": "",
            "safe_marked_at": 0,
            "last_rollback_at": 0,
        }
        self._load()

    def _normalized_sha(self, sha: str) -> str:
        return str(sha or "").strip()

    def get_state(self) -> Dict[str, object]:
        return dict(self.state)

    def record_update(self, from_commit: str, to_commit: str) -> None:
        before = self._normalized_sha(from_commit)
        after = self._normalized_sha(to_commit)
        if before:
            self.state["previous_commit"] = before
            self.state["last_update_from"] = before
        if after:
            self.state["last_update_to"] = after
        self.save()

    def set_safe_commit(self, commit_sha: str) -> None:
        commit = self._normalized_sha(commit_sha)
        if not commit:
            return
        self.state["safe_commit"] = commit
        self.state["rollback_commit"] = commit
        self.state["safe_marked_at"] = int(time.time())
        self.save()

    def record_rollback_success(self, commit_sha: str) -> None:
        commit = self._normalized_sha(commit_sha)
        if not commit:
            return
        # Keep rollback target stable as last known good version.
        self.state["rollback_commit"] = commit
        self.state["last_rollback_at"] = int(time.time())
        self.save()

    def get_preferred_rollback_target(self) -> Tuple[str, str]:
        safe_commit = self._normalized_sha(self.state.get("safe_commit", ""))
        if safe_commit:
            return safe_commit, "safe"

        rollback_commit = self._normalized_sha(self.state.get("rollback_commit", ""))
        if rollback_commit:
            return rollback_commit, "last_working"

        previous_commit = self._normalized_sha(self.state.get("previous_commit", ""))
        if previous_commit:
            return previous_commit, "previous"

        return "", "none"

    def save(self) -> None:
        folder = os.path.dirname(self.save_file)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(self.save_file, "w") as f:
            json.dump({"state": self.state}, f, indent=2)

    def _load(self) -> None:
        try:
            with open(self.save_file) as f:
                data = json.load(f)
            loaded = data.get("state", {}) if isinstance(data, dict) else {}
            if isinstance(loaded, dict):
                self.state.update(loaded)
        except (FileNotFoundError, json.JSONDecodeError):
            pass


update_state_manager = UpdateStateManager()
