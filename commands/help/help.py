from typing import List, Optional
import discord
from commands.shared import make_bot_embed, require_user
from config import get_wake_word
from utils import dm_background
from utils.llm_service import command_db

_FIELD_LIMIT = 1024


def _pack(tokens: List[str], limit: int = _FIELD_LIMIT) -> List[str]:
    """Join tokens with spaces into strings no longer than ``limit`` (embed field value cap)."""
    out, cur = [], ""
    for t in tokens:
        if cur and len(cur) + 1 + len(t) > limit:
            out.append(cur)
            cur = t
        else:
            cur = f"{cur} {t}" if cur else t
    if cur:
        out.append(cur)
    return out


def register(client: discord.Client):
    @client.tree.command(name="help", description="Commands list or one command’s summary")
    async def help_command(interaction: discord.Interaction, command: Optional[str] = None):
        if not await require_user(interaction):
            return
        from services.clone_service import sync_identity

        # Identity sync may hit the network; don't hold up the interaction response for it.
        dm_background.spawn(sync_identity(client, interaction.guild))
        wake = get_wake_word()

        if command:
            cmd_info = command_db.get_command(command.lower())
            if not cmd_info:
                await interaction.response.send_message(f"Unknown command. Try `/help` for the list.")
                return
            embed = make_bot_embed(
                client, f"/{cmd_info['name']}", cmd_info["description"], color=discord.Color.blue()
            )
            embed.add_field(name="Category", value=cmd_info["category"], inline=True)
            if cmd_info.get("aliases"):
                embed.add_field(name="Aliases", value=", ".join(f"`{a}`" for a in cmd_info["aliases"]), inline=True)
            embed.set_footer(text="/help [command]")
            await interaction.response.send_message(embed=embed)
            return

        embed = make_bot_embed(
            client,
            "Commands",
            f"`/help <name>` for one command · Wake **{wake}** or mention the bot",
            color=discord.Color.blue(),
        )
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
            for i, names in enumerate(_pack([f"`/{n}`" for n in sorted(cmd_names)])):
                label = f"{icon} {category}" if i == 0 else f"{icon} {category} (cont.)"
                embed.add_field(name=label, value=names, inline=False)
        embed.add_field(
            name="Quick tips",
            value=f"Reply to the bot to continue · `{wake} dl` or `/download` · `!word` / `.word` = shitpost",
            inline=False,
        )
        embed.set_footer(text="/help [command]")
        await interaction.response.send_message(embed=embed)
