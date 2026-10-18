"""Crash-safe JSON persistence shared by all state files.

- Writes go to a temp file in the same directory, are fsynced, then atomically replace the target.
- A corrupt file is preserved as ``<name>.corrupt-<ts>`` instead of being silently overwritten.
- ``CachedJsonFile`` re-reads only when the file's mtime changes (for hot-path reads like config).
"""
import copy
import json
import os
import shutil
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Optional

_locks: Dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def path_lock(path: str) -> threading.RLock:
    key = os.path.abspath(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


def _default_value(default: Any) -> Any:
    if callable(default):
        return default()
    return copy.deepcopy(default)


def _quarantine(path: str, err: Exception) -> None:
    dest = f"{path}.corrupt-{int(time.time())}"
    try:
        shutil.copy2(path, dest)
        print(f"⚠️  {os.path.basename(path)} is corrupt ({err}); saved a copy to {os.path.basename(dest)}")
    except OSError as e:
        print(f"⚠️  {os.path.basename(path)} is corrupt ({err}) and could not be backed up: {e}")


def load_json(path: str, default: Any = None) -> Any:
    """Load JSON from ``path``. Missing or corrupt -> ``default`` (corrupt file is backed up first)."""
    if default is None:
        default = {}
    with path_lock(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return _default_value(default)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            _quarantine(path, e)
            return _default_value(default)


def save_json(path: str, data: Any, *, indent: Optional[int] = 2) -> None:
    """Atomically write ``data`` as JSON to ``path`` (creates parent dirs)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with path_lock(path):
        fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, default=str, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class CachedJsonFile:
    """mtime-invalidated cache around load_json/save_json. ``get()`` returns a deep copy."""

    def __init__(self, path: str, default: Any = None, normalize: Optional[Callable[[Any], Any]] = None):
        self.path = path
        self.default = {} if default is None else default
        self.normalize = normalize
        self._data: Any = None
        self._mtime: Optional[float] = None
        self._lock = threading.Lock()

    def _current_mtime(self) -> Optional[float]:
        try:
            return os.stat(self.path).st_mtime_ns
        except FileNotFoundError:
            return None

    def get(self) -> Any:
        with self._lock:
            mtime = self._current_mtime()
            if self._data is None or mtime != self._mtime:
                data = load_json(self.path, self.default)
                if self.normalize is not None:
                    data = self.normalize(data)
                self._data = data
                self._mtime = mtime
            return copy.deepcopy(self._data)

    def save(self, data: Any, *, indent: Optional[int] = 4) -> None:
        with self._lock:
            save_json(self.path, data, indent=indent)
            self._data = self.normalize(copy.deepcopy(data)) if self.normalize else copy.deepcopy(data)
            self._mtime = self._current_mtime()
