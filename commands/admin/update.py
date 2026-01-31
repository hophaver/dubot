import os
import sys
import subprocess
import discord
from discord.ui import View, Button
from whitelist import is_admin
from utils import home_log


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
    @client.tree.command(name="update", description="Update bot from git repo")
    async def update(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = subprocess.run(["git", "pull"], capture_output=True, text=True)
            if result.returncode == 0:
                msg = f"✅ Git pull successful:\n```\n{(result.stdout or '')[:1000]}"
                if result.stderr:
                    msg += f"\nStderr:\n{result.stderr[:500]}"
                msg += "\n```\n**Restart the bot to apply changes.**"
            else:
                msg = f"❌ Git pull failed:\n```\n{(result.stderr or '')[:1000]}\n```"
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
