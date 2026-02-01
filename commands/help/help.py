from typing import Optional
import discord
from discord import app_commands
from whitelist import get_user_permission
from config import get_wake_word
from utils.llm_service import command_db


def register(client: discord.Client):
    @client.tree.command(name="help", description="Show all commands or get help for specific command")
    async def help_command(interaction: discord.Interaction, command: Optional[str] = None):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if command:
            cmd_info = command_db.get_command(command.lower())
            if cmd_info:
                embed = discord.Embed(title=f"📖 /{cmd_info['name']}", description=cmd_info["description"], color=discord.Color.blue())
                embed.set_thumbnail(url=client.user.display_avatar.url if client.user else None)
                embed.add_field(name="Category", value=cmd_info["category"], inline=True)
                if cmd_info.get("aliases"):
                    embed.add_field(name="Aliases", value=", ".join(cmd_info["aliases"]), inline=True)
                embed.set_footer(text="/help")
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.response.send_message(f"❌ Command '{command}' not found. Use `/help` to see all commands.")
        else:
            wake = get_wake_word()
            embed = discord.Embed(title="📖 Help", color=discord.Color.blue())
            embed.set_thumbnail(url=client.user.display_avatar.url if client.user else None)
            embed.add_field(name="📌 Usage", value=f"Use **`/help <command>`** for details (e.g. `/help himas`).", inline=False)
            category_icons = {"General": "💬", "File Analysis": "📄", "Reminders": "⏰", "Persona": "🎭", "Model": "🤖", "Download": "📥", "Scripts": "📜", "Admin": "🔧", "Home Assistant": "🏠"}
            for category, cmd_names in command_db.categories.items():
                if cmd_names:
                    icon = category_icons.get(category, "•")
                    embed.add_field(name=f"{icon} {category}", value=" ".join(f"`/{name}`" for name in sorted(cmd_names)), inline=True)
            embed.add_field(name="💡 Tips", value=f"Chat: **{wake}** or mention · Reply to bot to continue · **{wake} dl** or `/download` · `/translate` · `/run` · `/scripts`", inline=False)
            embed.set_footer(text="/help [command]")
            await interaction.response.send_message(embed=embed)
