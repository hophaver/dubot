import os
import discord
from whitelist import is_admin
from ._shared import recheck_scripts


def register(client: discord.Client):
    @client.tree.command(name="scripts", description="List scripts in the local scripts folder")
    async def scripts_list(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        # Recheck scripts/ on every /scripts run so the list is always current
        names = recheck_scripts()
        embed = discord.Embed(title="📜 Scripts", color=discord.Color.blue())
        embed.set_thumbnail(url=client.user.display_avatar.url if client.user else None)
        if not names:
            embed.description = "*No contents in `scripts/` folder.*"
            embed.set_footer(text="Scanned scripts/ · Add files or folders to list")
        else:
            value = ", ".join(f"`{n}`" for n in names[:25])
            if len(names) > 25:
                value += f" *+{len(names) - 25} more*"
            embed.add_field(name="Contents", value=value, inline=False)
            embed.set_footer(text="Scanned scripts/ · /run <script> for .py or .sh [now|in N min|at HH:MM]")
        await interaction.response.send_message(embed=embed)
