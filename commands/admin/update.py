import os
import subprocess
import sys
from typing import Optional

import discord
from discord.ui import View, Button
from whitelist import is_admin
from utils import home_log

# Project root: commands/admin/update.py -> admin -> commands -> root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REQUIREMENTS = os.path.join(_PROJECT_ROOT, "requirements.txt")


def _format_requirements_status(pip_res: Optional[subprocess.CompletedProcess]) -> str:
    """Create a clean requirements section for update output."""
    section = ["### Requirements"]
    if pip_res is None:
        section.append("○ No `requirements.txt` found, skipped dependency upgrade.")
        return "\n".join(section)

    if pip_res.returncode == 0:
        combined = ((pip_res.stdout or "") + "\n" + (pip_res.stderr or "")).strip()
        if not combined:
            combined = "(pip finished with no output)"
        section.append("✅ Dependency upgrade finished.")
        section.append("```")
        section.append(combined[:1200])
        section.append("```")
        return "\n".join(section)

    err_text = (pip_res.stderr or pip_res.stdout or "").strip()
    if not err_text:
        err_text = "(no error output)"
    section.append(f"⚠️ Dependency upgrade failed (exit code {pip_res.returncode}).")
    section.append("```")
    section.append(err_text[:1000])
    section.append("```")
    return "\n".join(section)


def _pip_upgrade_dependencies() -> Optional[subprocess.CompletedProcess]:
    """Run pip install -r requirements.txt --upgrade. Returns None if requirements.txt is missing."""
    if not os.path.isfile(_REQUIREMENTS):
        return None
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", _REQUIREMENTS, "--upgrade"],
        capture_output=True,
        text=True,
        cwd=_PROJECT_ROOT,
    )


def _restart_button():
    """Return a View with a Restart button (admin only)."""
    async def restart_callback(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.send_message("🔄 Restarting bot...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    view = View(timeout=None)
    btn = Button(label="Restart bot", style=discord.ButtonStyle.primary, custom_id="update_restart")
    btn.callback = restart_callback
    view.add_item(btn)
    return view


def register(client: discord.Client):
    @client.tree.command(
        name="update",
        description="Update bot from git and upgrade Python dependencies (requirements.txt)",
    )
    async def update(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = subprocess.run(
                ["git", "pull"],
                capture_output=True,
                text=True,
                cwd=_PROJECT_ROOT,
            )
            if result.returncode == 0:
                msg = f"### Git\n✅ Git pull successful:\n```\n{(result.stdout or '')[:1000]}"
                if result.stderr:
                    msg += f"\nStderr:\n{result.stderr[:500]}"
                msg += "\n```"

                pip_res = _pip_upgrade_dependencies()
                msg += "\n\n" + _format_requirements_status(pip_res)
                msg += "\n\n### Next step\n**Restart the bot to apply changes.**"
            else:
                msg = f"### Git\n❌ Git pull failed:\n```\n{(result.stderr or '')[:1000]}\n```"
            view = _restart_button()
            sent = await home_log.send_to_home(content=msg, view=view)
            if sent:
                await interaction.followup.send("✅ Update Downloaded", ephemeral=True)
            else:
                await interaction.followup.send(
                    msg[:1900] + "\n\n*(Home channel not set; use /sethome.)*",
                    view=view,
                    ephemeral=True,
                )
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}", ephemeral=True)
