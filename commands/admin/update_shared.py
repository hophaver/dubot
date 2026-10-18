import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Optional

import discord
from discord.ui import Button, View

from utils.paths import ROOT as PROJECT_ROOT
from utils.update_state import update_state_manager
from commands.shared import require_admin

REQUIREMENTS = os.path.join(PROJECT_ROOT, "requirements.txt")
GIT_TIMEOUT = 120
PIP_TIMEOUT = 900


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


async def _run(argv: list[str], timeout: float) -> ProcResult:
    """Run a subprocess in PROJECT_ROOT without blocking the event loop."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=PROJECT_ROOT,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ProcResult(124, "", f"timed out after {int(timeout)}s")
    return ProcResult(proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


async def run_git(args: list[str]) -> ProcResult:
    return await _run(["git", *args], GIT_TIMEOUT)


async def get_current_commit() -> str:
    res = await run_git(["rev-parse", "HEAD"])
    if res.returncode != 0:
        return ""
    return (res.stdout or "").strip()


def short_sha(sha: str) -> str:
    value = str(sha or "").strip()
    return value[:8] if value else "unknown"


async def pip_upgrade_dependencies() -> Optional[ProcResult]:
    """Run pip install -r requirements.txt --upgrade. Returns None if requirements.txt is missing."""
    if not os.path.isfile(REQUIREMENTS):
        return None
    return await _run([sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS, "--upgrade"], PIP_TIMEOUT)


def format_requirements_status(pip_res: Optional[ProcResult]) -> str:
    section = ["### Requirements"]
    if pip_res is None:
        section.append("○ No `requirements.txt` found.")
        return "\n".join(section)

    combined = ((pip_res.stdout or "") + "\n" + (pip_res.stderr or "")).strip()
    lower = combined.lower()

    if pip_res.returncode != 0:
        lines = (pip_res.stderr or pip_res.stdout or "unknown pip error").strip().splitlines()
        first_line = (lines[0] if lines else "unknown pip error")[:220]
        section.append(f"❌ Dependency check failed: {first_line}")
        return "\n".join(section)

    if "successfully installed" in lower or "uninstalling " in lower:
        section.append("✅ Dependencies updated.")
        return "\n".join(section)

    section.append("○ No dependency updates.")
    return "\n".join(section)


async def restart_bot(client: discord.Client) -> None:
    """Persist state, then replace this process with a fresh one."""
    persist = getattr(client, "persist_state", None)
    if persist is not None:
        try:
            await persist()
        except Exception as e:
            print(f"⚠️ Failed to persist state before restart: {e}", flush=True)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def build_update_action_view() -> View:
    """Persistent view (static custom_ids): register once with client.add_view() so it survives restarts."""

    async def restart_callback(interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.send_message("🔄 Restarting bot...")
        await restart_bot(interaction.client)

    async def safe_callback(interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        current = await get_current_commit()
        if not current:
            await interaction.response.send_message("❌ Could not read current git commit.", ephemeral=True)
            return
        update_state_manager.set_safe_commit(current)
        await interaction.response.send_message(
            f"✅ Marked `{short_sha(current)}` as safe rollback version.",
            ephemeral=True,
        )

    view = View(timeout=None)
    restart_btn = Button(label="Restart bot", style=discord.ButtonStyle.primary, custom_id="update_restart")
    restart_btn.callback = restart_callback
    view.add_item(restart_btn)

    safe_btn = Button(label="Mark as safe", style=discord.ButtonStyle.success, custom_id="update_mark_safe")
    safe_btn.callback = safe_callback
    view.add_item(safe_btn)
    return view
