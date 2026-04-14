from typing import Optional

import discord
from discord import app_commands

from conversations import conversation_manager
from whitelist import get_user_permission
from utils.llm_service import compact_dm_history_for_channel


def register(client: discord.Client):
    @client.tree.command(
        name="dm-history",
        description="DM only: view/set cutoff for history summarization and optionally summarize now",
    )
    @app_commands.describe(
        cutoff="How many recent user turns to keep before older history is summarized (4-80)",
        summarize_now="If true, summarize older messages now",
    )
    async def dm_history(
        interaction: discord.Interaction,
        cutoff: Optional[int] = None,
        summarize_now: Optional[bool] = False,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("❌ `/dm-history` works only in DMs.", ephemeral=True)
            return
        if cutoff is not None and (cutoff < 4 or cutoff > 80):
            await interaction.response.send_message("❌ Cutoff must be between 4 and 80.", ephemeral=True)
            return

        channel_id = interaction.channel.id
        if cutoff is not None:
            conversation_manager.set_dm_history_cutoff(channel_id, cutoff)
            conversation_manager.save()

        result = None
        if summarize_now:
            await interaction.response.defer(ephemeral=True)
            result = await compact_dm_history_for_channel(
                interaction.user.id,
                channel_id,
                str(interaction.user.name),
                force=True,
            )
        current_cutoff = conversation_manager.get_dm_history_cutoff(channel_id)
        summary_entries = conversation_manager.get_dm_summaries(channel_id)
        merged_total = sum(int(item.get("merged_messages", 0)) for item in summary_entries)

        msg = (
            f"DM history cutoff: **{current_cutoff}** user turns.\n"
            f"Stored summary blocks: **{len(summary_entries)}** (merged messages: **{merged_total}**)."
        )
        if cutoff is not None:
            msg = f"✅ DM history cutoff updated to **{current_cutoff}** user turns.\n" + msg
        if summarize_now:
            if result and result.get("compacted"):
                msg += (
                    f"\n✅ Summarized **{result.get('merged_messages', 0)}** old messages now."
                )
            else:
                reason = (result or {}).get("reason", "nothing to summarize")
                msg += f"\nℹ️ Summarize-now did not compact history ({reason})."

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
