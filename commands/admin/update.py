import subprocess
import discord
from whitelist import is_admin


def register(client: discord.Client):
    @client.tree.command(name="update", description="Update bot from git repo")
    async def update(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            result = subprocess.run(["git", "pull"], capture_output=True, text=True)
            if result.returncode == 0:
                msg = f"✅ Git pull successful:\n```\n{result.stdout[:1000]}"
                if result.stderr:
                    msg += f"\nStderr:\n{result.stderr[:500]}"
                msg += "\n```\n**Restart the bot to apply changes.**"
            else:
                msg = f"❌ Git pull failed:\n```\n{result.stderr[:1000]}\n```"
            await interaction.followup.send(msg)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}")
