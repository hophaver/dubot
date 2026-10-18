import discord
from commands.shared import make_bot_embed, require_admin
from ._shared import list_scripts


def register(client: discord.Client):
    @client.tree.command(name="scripts", description="List scripts in the local scripts folder")
    async def scripts_list(interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        names = list_scripts()  # re-scanned on every call so the list is always current
        embed = make_bot_embed(client, "📜 Scripts", color=discord.Color.blue())
        if not names:
            embed.description = "*No runnable scripts (.py/.sh/.bash) in `scripts/`.*"
        else:
            value = ", ".join(f"`{n}`" for n in names[:25])
            if len(names) > 25:
                value += f" *+{len(names) - 25} more*"
            embed.add_field(name="Scripts", value=value, inline=False)
            embed.set_footer(text="/run <script> [now|in N min|at HH:MM]")
        await interaction.response.send_message(embed=embed)
