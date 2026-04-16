import discord
from whitelist import is_admin
from utils import home_log
from commands.admin.update_shared import (
    build_update_action_view,
    format_requirements_status,
    get_current_commit,
    pip_upgrade_dependencies,
    run_git,
    short_sha,
)
from utils.update_state import update_state_manager


def register(client: discord.Client):
    @client.tree.command(
        name="update",
        description="Update bot from git and upgrade Python dependencies (requirements.txt)",
    )
    async def update(interaction: discord.Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            try:
                from integrations import refresh_environment_location_async

                await refresh_environment_location_async()
            except Exception:
                pass

            before_commit = get_current_commit()
            result = run_git(["pull"])
            after_commit = get_current_commit()
            update_state_manager.record_update(before_commit, after_commit)

            state = update_state_manager.get_state()
            safe_commit = str(state.get("safe_commit", "") or "").strip()
            if result.returncode == 0:
                msg = f"### Git\n✅ Git pull successful:\n```\n{(result.stdout or '')[:1000]}"
                if result.stderr:
                    msg += f"\nStderr:\n{result.stderr[:500]}"
                msg += "\n```"
                msg += (
                    "\n\n### Version tracking\n"
                    f"- Before update: `{short_sha(before_commit)}`\n"
                    f"- After update: `{short_sha(after_commit)}`\n"
                    f"- Safe rollback target: `{short_sha(safe_commit)}`"
                )

                pip_res = pip_upgrade_dependencies()
                msg += "\n\n" + format_requirements_status(pip_res)
                msg += (
                    "\n\n### Next step\n"
                    "**Restart the bot to apply changes.**\n"
                    "If this update looks stable, click **Mark as safe** to make it the preferred rollback version."
                )
            else:
                msg = f"### Git\n❌ Git pull failed:\n```\n{(result.stderr or '')[:1000]}\n```"
            view = build_update_action_view()
            sent = await home_log.send_to_home(content=msg, view=view)
            if sent:
                await interaction.followup.send("✅ Update Downloaded", ephemeral=True)
            else:
                await interaction.followup.send(
                    msg[:1900] + "\n\n*(Home channel not set; use /sethome.)*",
                    view=view,
                    ephemeral=True,
                )
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}", ephemeral=True)
