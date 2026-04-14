import os
import subprocess
import sys
from typing import Optional

import discord
from discord.ui import Button, View

from utils.update_state import update_state_manager
from whitelist import is_admin

# Project root: commands/admin/update_shared.py -> admin -> commands -> root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REQUIREMENTS = os.path.join(PROJECT_ROOT, "requirements.txt")


def run_git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


def get_current_commit() -> str:
    res = run_git(["rev-parse", "HEAD"])
    if res.returncode != 0:
        return ""
    return (res.stdout or "").strip()


def short_sha(sha: str) -> str:
    value = str(sha or "").strip()
    return value[:8] if value else "unknown"


def pip_upgrade_dependencies() -> Optional[subprocess.CompletedProcess]:
    """Run pip install -r requirements.txt --upgrade. Returns None if requirements.txt is missing."""
    if not os.path.isfile(REQUIREMENTS):
        return None
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS, "--upgrade"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


def format_requirements_status(pip_res: Optional[subprocess.CompletedProcess]) -> str:
    section = ["### Requirements"]
    if pip_res is None:
        section.append("○ No `requirements.txt` found.")
        return "\n".join(section)

    combined = ((pip_res.stdout or "") + "\n" + (pip_res.stderr or "")).strip()
    lower = combined.lower()

    if pip_res.returncode != 0:
        first_line = (pip_res.stderr or pip_res.stdout or "unknown pip error").strip().splitlines()[0][:220]
        section.append(f"❌ Dependency check failed: {first_line}")
        return "\n".join(section)

    if "successfully installed" in lower or "uninstalling " in lower:
        section.append("✅ Dependencies updated.")
        return "\n".join(section)

    section.append("○ No dependency updates.")
    return "\n".join(section)


def build_update_action_view() -> View:
    async def restart_callback(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.send_message("🔄 Restarting bot...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    async def safe_callback(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        current = get_current_commit()
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
