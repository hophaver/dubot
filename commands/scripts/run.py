import asyncio
from typing import Optional
import discord
from discord import app_commands
from commands.shared import require_admin
from utils import dm_background, home_log
from ._shared import parse_when, resolve_script, run_script_file

# Scripts that wait for remote output (e.g. tail -f until sentinel): need longer timeout and background run
LONG_RUNNING_SCRIPTS = {"site-update-1.py", "site-start-1.py"}
LONG_RUNNING_TIMEOUT = 600  # seconds
DEFAULT_TIMEOUT = 120
SCHEDULED_TIMEOUT = 300


def _result_embed(name: str, result, scheduled: bool = False) -> discord.Embed:
    out = result.output
    if len(out) > 4000:
        out = out[:4000] + "..."
    ok = result.returncode == 0
    embed = discord.Embed(
        title=f"📜 Ran `{name}`" + (" (scheduled)" if scheduled else ""),
        description=out,
        color=discord.Color.green() if ok else discord.Color.orange(),
    )
    embed.set_footer(text="Timed out" if result.returncode is None else f"Exit code: {result.returncode}")
    return embed


async def _run_to_home(name: str, timeout: float, scheduled: bool = False, delay: float = 0.0) -> None:
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        result = await run_script_file(name, timeout)
        await home_log.send_to_home(embed=_result_embed(name, result, scheduled))
    except Exception as e:
        await home_log.send_to_home(content=f"❌ Script `{name}` error: {str(e)[:500]}")


def register(client: discord.Client):
    @client.tree.command(name="run", description="Run a script from the scripts folder (now or at specified time)")
    @app_commands.describe(script="Script name (e.g. backup.py)", when="Optional: 'now', 'in 5 minutes', or 'at 15:30' (default: now)")
    async def run_script(interaction: discord.Interaction, script: str, when: Optional[str] = "now"):
        if not await require_admin(interaction):
            return
        chosen = resolve_script(script)
        if not chosen:
            await interaction.response.send_message(f"❌ Script `{script}` not found. Use `/scripts` to list.", ephemeral=True)
            return
        delay = parse_when(when)
        if delay is None:
            await interaction.response.send_message(
                f"❌ Couldn't parse `when: {when}`. Use `now`, `in 5 minutes`, `in 2 hours`, or `at 15:30`.",
                ephemeral=True,
            )
            return
        if delay > 0:
            await interaction.response.send_message(f"⏱️ Scheduled `{chosen}` to run in {int(delay)}s.")
            dm_background.spawn(_run_to_home(chosen, SCHEDULED_TIMEOUT, scheduled=True, delay=delay))
            return
        if chosen in LONG_RUNNING_SCRIPTS:
            await interaction.response.send_message(
                f"⏳ Running `{chosen}` (may take a few minutes). Output will be sent to home when done.",
                ephemeral=True,
            )
            dm_background.spawn(_run_to_home(chosen, LONG_RUNNING_TIMEOUT))
            return
        await interaction.response.defer()
        result = await run_script_file(chosen, DEFAULT_TIMEOUT)
        embed = _result_embed(chosen, result)
        if await home_log.send_to_home(embed=embed):
            await interaction.followup.send("✅ Output sent to home channel.", ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, content="*(Home channel not set; use /sethome.)*")
