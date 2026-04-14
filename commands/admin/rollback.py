import discord

from commands.admin.update_shared import build_update_action_view, get_current_commit, run_git, short_sha
from utils.update_state import update_state_manager
from whitelist import is_admin


def _working_tree_dirty() -> bool:
    status = run_git(["status", "--porcelain"])
    if status.returncode != 0:
        return False
    return bool((status.stdout or "").strip())


def _commit_exists(commit_sha: str) -> bool:
    check = run_git(["cat-file", "-e", f"{commit_sha}^{{commit}}"])
    return check.returncode == 0


def register(client: discord.Client):
    @client.tree.command(
        name="rollback",
        description="Rollback bot code to last safe/working git version",
    )
    async def rollback(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        try:
            target_commit, source = update_state_manager.get_preferred_rollback_target()
            if not target_commit:
                await interaction.followup.send(
                    "❌ No rollback target stored yet. Run `/update` first, or click **Mark as safe** on a known good version.",
                    ephemeral=True,
                )
                return

            if not _commit_exists(target_commit):
                await interaction.followup.send(
                    f"❌ Stored rollback commit `{target_commit}` is not available in this git repo.",
                    ephemeral=True,
                )
                return

            if _working_tree_dirty():
                await interaction.followup.send(
                    "❌ Rollback aborted: working tree has local changes. Commit/stash them first.",
                    ephemeral=True,
                )
                return

            before = get_current_commit()
            result = run_git(["reset", "--hard", target_commit])
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "unknown git error")[:400]
                await interaction.followup.send(f"❌ Rollback failed:\n```{err}```", ephemeral=True)
                return

            after = get_current_commit()
            update_state_manager.record_rollback_success(after)
            view = build_update_action_view()

            source_label = {
                "safe": "safe version",
                "last_working": "last working version",
                "previous": "previous update commit",
            }.get(source, source)

            msg = (
                "### Rollback\n"
                f"✅ Rolled back to `{short_sha(after)}` ({source_label}).\n"
                f"- Before rollback: `{short_sha(before)}`\n"
                f"- Current commit: `{short_sha(after)}`\n\n"
                "### Next step\n"
                "**Restart the bot to apply rollback.**"
            )
            await interaction.followup.send(msg, view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}", ephemeral=True)
