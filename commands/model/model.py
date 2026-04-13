import requests
from typing import Optional, List
import discord
from discord import app_commands
from discord.ui import View, Select, Button
from whitelist import get_user_permission
from models import model_manager
from utils.llm_service import validate_and_set_model
from integrations import OLLAMA_URL


def _model_embed(current: str, selected: Optional[str]) -> discord.Embed:
    embed = discord.Embed(
        title="🤖 Model Configuration",
        description="Basic interactions (e.g. shitpost, Home Assistant parsing, translate) always run on the local Ollama model shown below.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Local runtime model", value=f"`{current}`", inline=True)
    if selected:
        embed.add_field(name="Selected (confirm to switch)", value=f"`{selected}`", inline=True)
    embed.set_footer(text="Select a local model, then confirm. Use /model cloud <name> for OpenRouter chat models.")
    return embed


class ModelSelectView(View):
    def __init__(self, client: discord.Client, user_id: int, current: str, models: List[str], timeout: float = 120):
        super().__init__(timeout=timeout)
        self.client = client
        self.user_id = user_id
        self.current = current
        self.models = models or []
        self.selected: Optional[str] = None
        options = [discord.SelectOption(label=m[:100], value=m, description="Current" if m == current else None) for m in self.models[:25]]
        if options:
            sel = Select(placeholder="Select a model", options=options)
            sel.callback = self._on_select
            self.add_item(sel)
        confirm = Button(label="Confirm switch", style=discord.ButtonStyle.success, custom_id="model_confirm")
        confirm.callback = self._on_confirm
        self.add_item(confirm)
        remove_btn = Button(label="Remove model", style=discord.ButtonStyle.danger, custom_id="model_remove")
        remove_btn.callback = self._on_remove
        self.add_item(remove_btn)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        self.selected = interaction.data.get("values", [None])[0]
        if not self.selected:
            await interaction.response.defer_update()
            return
        await interaction.response.edit_message(embed=_model_embed(self.current, self.selected), view=self)

    async def _on_confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        target = self.selected or self.current
        await interaction.response.defer()
        success, msg = await validate_and_set_model(interaction.user.id, "local", target)
        if success:
            self.current = target
        await interaction.message.edit(
            content=f"✅ {msg}" if success else f"❌ {msg}",
            embed=_model_embed(self.current, None),
            view=self,
        )

    async def _on_remove(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        target = self.selected or self.current
        await interaction.response.defer()
        try:
            r = requests.delete(f"{OLLAMA_URL}/api/delete", json={"name": target}, timeout=60)
            if r.status_code == 200:
                from utils.llm_service import clear_vision_model_cache
                clear_vision_model_cache()
                self.models = model_manager.list_all_models(refresh_local=True)
                if self.current == target:
                    self.current = self.models[0] if self.models else "qwen2.5:7b"
                    model_manager.set_user_model(interaction.user.id, self.current)
                self.selected = None
                await interaction.message.edit(
                    content=f"✅ Removed `{target}`.",
                    embed=_model_embed(self.current, None),
                    view=ModelSelectView(self.client, self.user_id, self.current, self.models, timeout=self.timeout),
                )
            else:
                await interaction.followup.send(f"❌ Failed: {r.text[:200] or r.status_code}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}", ephemeral=True)


def register(client: discord.Client):
    @client.tree.command(name="model", description="View or switch model (local Ollama or cloud OpenRouter)")
    @app_commands.describe(
        provider="Model provider: local (Ollama) or cloud (OpenRouter)",
        model_name="Model to switch to (required when provider is set)",
    )
    @app_commands.choices(provider=[
        app_commands.Choice(name="local (Ollama)", value="local"),
        app_commands.Choice(name="cloud (OpenRouter)", value="cloud"),
    ])
    async def model(
        interaction: discord.Interaction,
        provider: Optional[app_commands.Choice[str]] = None,
        model_name: Optional[str] = None,
    ):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=False)
        try:
            if provider:
                if not model_name or not model_name.strip():
                    await interaction.followup.send("Usage: `/model local|cloud model_name`")
                    return
                success, msg = await validate_and_set_model(
                    interaction.user.id,
                    provider.value,
                    model_name.strip(),
                )
                if success:
                    local_runtime = model_manager.get_last_local_model(interaction.user.id, refresh_local=True)
                    await interaction.followup.send(
                        f"✅ {msg}\n"
                        f"Active chat model updated.\n"
                        f"Basic interactions continue using local runtime model: `{local_runtime}`."
                    )
                else:
                    await interaction.followup.send(f"❌ {msg}")
                return

            models = model_manager.list_all_models(refresh_local=True)
            info = model_manager.get_user_model_info(interaction.user.id)
            current_provider = info.get("provider", "local")
            current = info.get("model", "qwen2.5:7b")
            if current_provider == "cloud":
                local_runtime = model_manager.get_last_local_model(interaction.user.id, refresh_local=True)
                await interaction.followup.send(
                    f"**Current chat model:** `{current}` (`cloud`)\n"
                    f"**Basic local runtime model:** `{local_runtime}` (`local`)\n\n"
                    "Use `/model cloud model_name` to switch cloud chat model or "
                    "`/model local model_name` to switch back fully to local."
                )
                return
            if not models:
                await interaction.followup.send(
                    "No local models found. Use **/pull-model local model_name** "
                    "(example: `/pull-model local llama3.2:3b`).",
                    ephemeral=False,
                )
                return
            if current not in models:
                current = models[0] if models else "qwen2.5:7b"
            embed = _model_embed(current, None)
            view = ModelSelectView(client, interaction.user.id, current, models)
            await interaction.followup.send(embed=embed, view=view)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}")
