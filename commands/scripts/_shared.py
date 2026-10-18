import asyncio
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from utils.paths import root_path

SCRIPTS_DIR = root_path("scripts")
ALLOWED_EXTENSIONS = (".py", ".sh", ".bash")


def list_scripts():
    """Return runnable script names from scripts/ (always reads from disk, no cache)."""
    if not os.path.isdir(SCRIPTS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(SCRIPTS_DIR)):
        if name.startswith("."):
            continue
        path = os.path.join(SCRIPTS_DIR, name)
        if os.path.isfile(path) and name.lower().endswith(ALLOWED_EXTENSIONS):
            out.append(name)
    return out


def resolve_script(query: str) -> Optional[str]:
    """Exact (case-insensitive) match on the file name, with or without its extension."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for name in list_scripts():
        low = name.lower()
        if low == q or os.path.splitext(low)[0] == q:
            return name
    return None


def parse_when(when: Optional[str]) -> Optional[float]:
    """Delay in seconds for 'now' / 'in N minutes|hours' / 'at HH:MM'. Returns None if unparseable."""
    s = (when or "").strip().lower()
    if s in ("", "now"):
        return 0.0
    m = re.match(r"in\s+(\d+)\s*(minute|min|hour|hr)s?\s*$", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return float(n * 60 if "min" in unit else n * 3600)
    m = re.match(r"at\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            return None
        now = datetime.now()
        target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()
    return None


@dataclass
class ScriptResult:
    returncode: Optional[int]  # None = timed out
    output: str


async def run_script_file(name: str, timeout: float) -> ScriptResult:
    """Run scripts/<name> without blocking the event loop; kills it on timeout."""
    path = os.path.join(SCRIPTS_DIR, name)
    python = os.environ.get("PYTHON") or sys.executable or "python3"
    argv = [python, path] if name.endswith(".py") else ["bash", path]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=SCRIPTS_DIR,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ScriptResult(None, f"Timed out after {int(timeout)}s.")
    out = (stdout or b"").decode("utf-8", "replace").strip() or "(no output)"
    err = (stderr or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        out = f"Exit code: {proc.returncode}\n{out}"
        if err:
            out += f"\n{err}"
    return ScriptResult(proc.returncode, out)
