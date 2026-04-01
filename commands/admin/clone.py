from typing import Optional

import discord
from discord import app_commands

from integrations import PERMANENT_ADMIN
from services import clone_service


def register(client: discord.Client):
    @client.tree.command(
        name="clone",
        description="Mirror a member's avatar, nickname, and messages (permanent admin only)",
    )
    @app_commands.describe(
        user="Server member to mirror (required for on/replace)",
        mode="on: copy messages · replace: copy and delete originals · off: stop and revert",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
            app_commands.Choice(name="replace", value="replace"),
        ],
    )
    async def clone_cmd(
        interaction: discord.Interaction,
        user: Optional[discord.Member],
        mode: str,
    ):
        if interaction.user.id != PERMANENT_ADMIN:
            await interaction.response.send_message("❌ Permanent admin only.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("❌ Use this command in a server.", ephemeral=True)
            return

        if mode == "off":
            await interaction.response.defer(ephemeral=True)
            try:
                await clone_service.stop_clone(client)
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ Could not revert: {e}", ephemeral=True)
                return
            await interaction.followup.send("✅ Clone off — bot avatar and nickname restored.", ephemeral=True)
            return

        if user is None:
            await interaction.response.send_message("❌ Pick a member for on/replace.", ephemeral=True)
            return
        if user.id == client.user.id:
            await interaction.response.send_message("❌ Pick someone other than the bot.", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("❌ Cannot clone a bot user.", ephemeral=True)
            return

        delete_original = mode == "replace"
        await interaction.response.defer(ephemeral=True)
        try:
            await clone_service.start_clone(client, user, delete_original=delete_original)
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ Discord rejected the change (permissions or rate limit): {e}",
                ephemeral=True,
            )
            return
        except RuntimeError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        mode_desc = "mirroring (originals kept)" if mode == "on" else "mirroring (deleting originals)"
        await interaction.followup.send(
            f"✅ Clone **{mode_desc}** as {user.mention}. Use `/clone` mode **off** to revert.",
            ephemeral=True,
        )
