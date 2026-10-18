import discord
from config import set_bot_awake
from commands.shared import require_user


def register(client: discord.Client):
    @client.tree.command(name="sleep", description="Put bot offline until /wake")
    async def sleep(interaction: discord.Interaction):
        if not await require_user(interaction):
            return
        set_bot_awake(False)
        await interaction.response.send_message("😴 Going offline. I will ignore everything except `/wake`.")

    @client.tree.command(name="wake", description="Bring bot back online")
    async def wake(interaction: discord.Interaction):
        if not await require_user(interaction):
            return
        set_bot_awake(True)
        await interaction.response.send_message("✅ Awake and back online.")
