import os
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
ALLOWED_EXTENSIONS = (".py", ".sh", ".bash")


def list_scripts():
    """Return script names from scripts/ (always reads from disk, no cache)."""
    if not os.path.isdir(SCRIPTS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(SCRIPTS_DIR)):
        if name.startswith("."):
            continue
        path = os.path.join(SCRIPTS_DIR, name)
        if os.path.isfile(path) and any(name.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
            out.append(name)
    return out


def list_scripts_dir_contents():
    """Return all contents of scripts/ (files and dirs, no extension filter). Skips dotfiles."""
    if not os.path.isdir(SCRIPTS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(SCRIPTS_DIR)):
        if name.startswith("."):
            continue
        out.append(name)
    return out


def recheck_scripts():
    """Re-scan scripts/ directory and return all contents. Use when /scripts is run."""
    return list_scripts_dir_contents()


def parse_when(when: Optional[str]) -> Tuple[bool, Optional[float], None]:
    if not when or not when.strip():
        return True, None, None
    s = when.strip().lower()
    if s in ("now", ""):
        return True, None, None
    m = re.match(r"in\s+(\d+)\s*(minute|min|hour|hr)s?\s*$", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        sec = n * 60 if "min" in unit else n * 3600
        return False, float(sec), None
    m = re.match(r"at\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        now = datetime.now()
        target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return False, (target - now).total_seconds(), None
    return True, None, None
