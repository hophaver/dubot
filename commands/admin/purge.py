import discord
from discord import app_commands
from whitelist import is_admin


def register(client: discord.Client):
    @client.tree.command(name="purge", description="Delete messages from channel")
    @app_commands.describe(amount="Number of messages to delete (1-100)")
    async def purge(interaction: discord.Interaction, amount: int = 10):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        if amount < 1 or amount > 100:
            await interaction.response.send_message("❌ Amount must be between 1 and 100.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:100]}", ephemeral=True)
