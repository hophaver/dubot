import os
import asyncio
import subprocess
from typing import Optional
import discord
from discord import app_commands
from whitelist import is_admin
from utils import home_log
from ._shared import list_scripts, parse_when, SCRIPTS_DIR


def register(client: discord.Client):
    @client.tree.command(name="run", description="Run a script from the scripts folder (now or at specified time)")
    @app_commands.describe(script="Script name (e.g. backup.py)", when="Optional: 'now', 'in 5 minutes', or 'at 15:30' (default: now)")
    async def run_script(interaction: discord.Interaction, script: str, when: Optional[str] = "now"):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        names = list_scripts()
        chosen = None
        base = script.lower().strip()
        for ext in (".py", ".sh", ".bash", ".exp"):
            if base.endswith(ext):
                base = base[: -len(ext)]
                break
        for n in names:
            if n == script or n == script.strip() or n.lower() == script.lower():
                chosen = n
                break
            if base and n.lower() == base + n[n.rfind(".") :]:
                chosen = n
                break
        if not chosen:
            for n in names:
                if n.lower().startswith(script.lower().strip()):
                    chosen = n
                    break
        if not chosen:
            await interaction.response.send_message(f"❌ Script `{script}` not found. Use `/scripts` to list.", ephemeral=True)
            return
        path = os.path.join(SCRIPTS_DIR, chosen)
        if not os.path.isfile(path):
            await interaction.response.send_message("❌ Script file not found.", ephemeral=True)
            return
        run_now, delay_sec, _ = parse_when(when)
        if not run_now and delay_sec is not None and delay_sec > 0:
            await interaction.response.send_message(f"⏱️ Scheduled `{chosen}` to run in {int(delay_sec)}s.")
            async def run_later():
                await asyncio.sleep(delay_sec)
                try:
                    if chosen.endswith(".py"):
                        subprocess.run([os.environ.get("PYTHON", "python3"), path], cwd=SCRIPTS_DIR, timeout=300)
                    elif chosen.endswith(".exp"):
                        subprocess.run(["expect", path], cwd=SCRIPTS_DIR, timeout=300)
                    else:
                        subprocess.run(["bash", path], cwd=SCRIPTS_DIR, timeout=300)
                except Exception as e:
                    home_log.log_sync(f"Script run error: {e}")
            asyncio.create_task(run_later())
            return
        await interaction.response.defer()
        try:
            if chosen.endswith(".py"):
                result = subprocess.run([os.environ.get("PYTHON", "python3"), path], cwd=SCRIPTS_DIR, capture_output=True, text=True, timeout=120)
            elif chosen.endswith(".exp"):
                result = subprocess.run(["expect", path], cwd=SCRIPTS_DIR, capture_output=True, text=True, timeout=120)
            else:
                result = subprocess.run(["bash", path], cwd=SCRIPTS_DIR, capture_output=True, text=True, timeout=120)
            out = (result.stdout or "").strip() or "(no output)"
            err = (result.stderr or "").strip()
            if result.returncode != 0:
                out = f"Exit code: {result.returncode}\n{out}"
                if err:
                    out += f"\n{err}"
            if len(out) > 1900:
                out = out[:1900] + "..."
            embed = discord.Embed(title=f"📜 Ran `{chosen}`", description=out, color=discord.Color.green() if result.returncode == 0 else discord.Color.orange())
            embed.set_footer(text=f"Exit code: {result.returncode}")
            await interaction.followup.send(embed=embed)
        except subprocess.TimeoutExpired:
            await interaction.followup.send(f"❌ Script `{chosen}` timed out (120s).")
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}")
