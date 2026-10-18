from commands.shared import require_himas
import discord
from ._shared import load_ha_mappings

_FIELDS_PER_EMBED = 25  # Discord limit
_EMBEDS_PER_MESSAGE = 10  # Discord limit


def register(client: discord.Client):
    @client.tree.command(name="listentities", description="List all entity mappings")
    async def listentities(interaction: discord.Interaction):
        if not await require_himas(interaction):
            return
        items = list(load_ha_mappings().items())
        if not items:
            await interaction.response.send_message("📭 No entity mappings found.")
            return
        embeds = []
        for start in range(0, len(items), _FIELDS_PER_EMBED):
            title = "🏠 Entity Mappings" if start == 0 else "🏠 Entity Mappings (cont.)"
            embed = discord.Embed(title=title, color=discord.Color.green())
            for friendly_name, entity_id in items[start : start + _FIELDS_PER_EMBED]:
                embed.add_field(name=friendly_name[:256], value=str(entity_id)[:1024], inline=True)
            embeds.append(embed)
        shown = embeds[:_EMBEDS_PER_MESSAGE]
        if len(embeds) > _EMBEDS_PER_MESSAGE:
            shown[-1].set_footer(text=f"Showing {_EMBEDS_PER_MESSAGE * _FIELDS_PER_EMBED} of {len(items)} mappings")
        await interaction.response.send_message(embeds=shown)
