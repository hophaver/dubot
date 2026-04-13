"""Admin command to change the LLM used for news summarization."""

import discord
from discord import app_commands
from whitelist import is_admin
from services.news_service import set_news_model, get_news_model


def register(client: discord.Client):
    @client.tree.command(
        name="news-model",
        description="[Admin] Set the LLM model for news summarization",
    )
    @app_commands.describe(
        provider="Model provider: local (Ollama) or cloud",
        model_name="Model name (e.g. qwen2.5:7b, llama3.2:3b)",
    )
    @app_commands.choices(provider=[
        app_commands.Choice(name="local (Ollama)", value="local"),
        app_commands.Choice(name="cloud", value="cloud"),
    ])
    async def news_model(
        interaction: discord.Interaction,
        provider: app_commands.Choice[str],
        model_name: str,
    ):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return

        set_news_model(provider.value, model_name.strip())

        embed = discord.Embed(
            title="📰 News Model Updated",
            color=0x2ECC71,
        )
        embed.add_field(name="Provider", value=provider.name, inline=True)
        embed.add_field(name="Model", value=f"`{model_name.strip()}`", inline=True)
        embed.set_footer(text="This model will be used for all news summarization")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @client.tree.command(
        name="news-model-info",
        description="Show the current news summarization model",
    )
    async def news_model_info(interaction: discord.Interaction):
        model_type, model_name = get_news_model()
        display_model = model_name or "default (same as chat)"
        embed = discord.Embed(
            title="📰 News Model",
            color=0x5865F2,
        )
        embed.add_field(name="Provider", value=model_type, inline=True)
        embed.add_field(name="Model", value=f"`{display_model}`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
