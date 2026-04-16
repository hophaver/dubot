from typing import Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from commands.shared import bot_embed_thumbnail_url
from config import get_wake_word
from utils.llm_service import command_db


def register(client: discord.Client):
    @client.tree.command(name="help", description="Commands list or one command’s summary")
    async def help_command(interaction: discord.Interaction, command: Optional[str] = None):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        from services.clone_service import sync_identity

        await sync_identity(client, interaction.guild)
        wake = get_wake_word()
        _thumb = bot_embed_thumbnail_url(client.user)

        if command:
            cmd_info = command_db.get_command(command.lower())
            if not cmd_info:
                await interaction.response.send_message(f"Unknown command. Try `/help` for the list.")
                return
            embed = discord.Embed(
                title=f"/{cmd_info['name']}",
                description=cmd_info["description"],
                color=discord.Color.blue(),
            )
            if _thumb:
                embed.set_thumbnail(url=_thumb)
            embed.add_field(name="Category", value=cmd_info["category"], inline=True)
            if cmd_info.get("aliases"):
                embed.add_field(name="Aliases", value=", ".join(f"`{a}`" for a in cmd_info["aliases"]), inline=True)
            embed.set_footer(text="/help [command]")
            await interaction.response.send_message(embed=embed)
            return

        embed = discord.Embed(
            title="Commands",
            description=f"`/help <name>` for one command · Wake **{wake}** or mention the bot",
            color=discord.Color.blue(),
        )
        if _thumb:
            embed.set_thumbnail(url=_thumb)
        category_icons = {
            "General": "💬",
            "File Analysis": "📄",
            "Reminders": "⏰",
            "Calendar": "📅",
            "Persona": "🎭",
            "Model": "🤖",
            "Download": "📥",
            "Scripts": "📜",
            "Admin": "🔧",
            "Shitpost": "🎲",
            "Home Assistant": "🏠",
            "News": "📰",
        }
        for category, cmd_names in sorted(command_db.categories.items()):
            if not cmd_names:
                continue
            icon = category_icons.get(category, "•")
            names = " ".join(f"`/{n}`" for n in sorted(cmd_names))
            embed.add_field(name=f"{icon} {category}", value=names, inline=False)
        embed.add_field(
            name="Quick tips",
            value=f"Reply to the bot to continue · `{wake} dl` or `/download` · `!word` / `.word` = shitpost",
            inline=False,
        )
        embed.set_footer(text="/help [command]")
        await interaction.response.send_message(embed=embed)
