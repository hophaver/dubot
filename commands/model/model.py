import requests
from typing import Optional, List, Tuple
import discord
from discord import app_commands
from discord.ui import View, Select, Button
from whitelist import get_user_permission
from models import model_manager
from utils.llm_service import validate_and_set_model
from integrations import OLLAMA_URL

PAGE_SIZE = 25


def _model_embed(
    current_provider: str,
    current_model: str,
    local_runtime: str,
    selected: Optional[Tuple[str, str]],
    page: int,
    total_items: int,
) -> discord.Embed:
    embed = discord.Embed(
        title="🤖 Model Configuration",
        description="Pick a model from the menu, then confirm to switch.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Current chat model", value=f"`{current_model}` (`{current_provider}`)", inline=False)
    embed.add_field(name="Local runtime model", value=f"`{local_runtime}` (`local`)", inline=False)
    if selected:
        selected_provider, selected_model = selected
        embed.add_field(
            name="Selected (confirm to switch)",
            value=f"`{selected_model}` (`{selected_provider}`)",
            inline=False,
        )
    total_pages = max(1, ((total_items - 1) // PAGE_SIZE) + 1)
    embed.set_footer(text=f"Page {page + 1}/{total_pages} • Menu includes all local and previously used cloud models.")
    return embed


class ModelSelectView(View):
    def __init__(
        self,
        client: discord.Client,
        user_id: int,
        current_provider: str,
        current_model: str,
        local_models: List[str],
        cloud_models: List[str],
        timeout: float = 120,
    ):
        super().__init__(timeout=timeout)
        self.client = client
        self.user_id = user_id
        self.current_provider = current_provider
        self.current_model = current_model
        self.local_models = sorted(local_models or [])
        self.cloud_models = list(dict.fromkeys([m for m in (cloud_models or []) if m]))
        if self.current_provider == "cloud" and self.current_model and self.current_model not in self.cloud_models:
            self.cloud_models.insert(0, self.current_model)
        self.options: List[Tuple[str, str]] = [("local", m) for m in self.local_models]
        self.options.extend([("cloud", m) for m in self.cloud_models])
        self.page = 0
        self.selected: Optional[Tuple[str, str]] = None
        self._rebuild_components()

    def _total_pages(self) -> int:
        return max(1, ((len(self.options) - 1) // PAGE_SIZE) + 1)

    def _page_slice(self) -> List[Tuple[int, Tuple[str, str]]]:
        start = self.page * PAGE_SIZE
        end = start + PAGE_SIZE
        return list(enumerate(self.options[start:end], start=start))

    def _rebuild_components(self) -> None:
        self.clear_items()
        page_items = self._page_slice()
        options: List[discord.SelectOption] = []
        for idx, (provider, model_name) in page_items:
            is_current = provider == self.current_provider and model_name == self.current_model
            label = f"[{provider}] {model_name}"[:100]
            description = "Current chat model" if is_current else None
            options.append(discord.SelectOption(label=label, value=str(idx), description=description))
        if options:
            sel = Select(placeholder="Select a model from this page", options=options)
            sel.callback = self._on_select
            self.add_item(sel)
        prev_btn = Button(label="Previous", style=discord.ButtonStyle.secondary, custom_id="model_prev")
        prev_btn.disabled = self.page <= 0
        prev_btn.callback = self._on_prev
        self.add_item(prev_btn)
        next_btn = Button(label="Next", style=discord.ButtonStyle.secondary, custom_id="model_next")
        next_btn.disabled = self.page >= (self._total_pages() - 1)
        next_btn.callback = self._on_next
        self.add_item(next_btn)
        confirm = Button(label="Confirm switch", style=discord.ButtonStyle.success, custom_id="model_confirm")
        confirm.callback = self._on_confirm
        self.add_item(confirm)
        remove_btn = Button(label="Remove local model", style=discord.ButtonStyle.danger, custom_id="model_remove")
        remove_btn.callback = self._on_remove
        self.add_item(remove_btn)

    def _current_embed(self) -> discord.Embed:
        local_runtime = model_manager.get_last_local_model(self.user_id, refresh_local=True)
        return _model_embed(
            current_provider=self.current_provider,
            current_model=self.current_model,
            local_runtime=local_runtime,
            selected=self.selected,
            page=self.page,
            total_items=len(self.options),
        )

    def _selected_target(self) -> Tuple[str, str]:
        return self.selected or (self.current_provider, self.current_model)

    async def _refresh_message(self, interaction: discord.Interaction):
        self._rebuild_components()
        await interaction.response.edit_message(embed=self._current_embed(), view=self)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        selected_value = interaction.data.get("values", [None])[0]
        if not selected_value:
            await interaction.response.defer_update()
            return
        try:
            selected_idx = int(selected_value)
        except (TypeError, ValueError):
            await interaction.response.defer_update()
            return
        if selected_idx < 0 or selected_idx >= len(self.options):
            await interaction.response.defer_update()
            return
        self.selected = self.options[selected_idx]
        await self._refresh_message(interaction)

    async def _on_prev(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        if self.page > 0:
            self.page -= 1
        await self._refresh_message(interaction)

    async def _on_next(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        if self.page < self._total_pages() - 1:
            self.page += 1
        await self._refresh_message(interaction)

    async def _on_confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        target_provider, target_model = self._selected_target()
        await interaction.response.defer()
        success, msg = await validate_and_set_model(interaction.user.id, target_provider, target_model)
        if success:
            self.current_provider = target_provider
            self.current_model = target_model
            info = model_manager.get_user_model_info(interaction.user.id)
            self.cloud_models = info.get("cloud_history", [])
            self.local_models = model_manager.list_all_models(refresh_local=True)
            self.options = [("local", m) for m in self.local_models]
            self.options.extend([("cloud", m) for m in self.cloud_models])
            self.page = min(self.page, max(0, self._total_pages() - 1))
            self.selected = None
        self._rebuild_components()
        local_runtime = model_manager.get_last_local_model(interaction.user.id, refresh_local=True)
        await interaction.message.edit(
            content=f"✅ {msg}" if success else f"❌ {msg}",
            embed=_model_embed(
                current_provider=self.current_provider,
                current_model=self.current_model,
                local_runtime=local_runtime,
                selected=None,
                page=self.page,
                total_items=len(self.options),
            ),
            view=self,
        )

    async def _on_remove(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        target_provider, target_model = self._selected_target()
        if target_provider != "local":
            await interaction.response.send_message("Only local Ollama models can be removed.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            r = requests.delete(f"{OLLAMA_URL}/api/delete", json={"name": target_model}, timeout=60)
            if r.status_code == 200:
                from utils.llm_service import clear_vision_model_cache
                clear_vision_model_cache()
                self.local_models = model_manager.list_all_models(refresh_local=True)
                if self.current_provider == "local" and self.current_model == target_model:
                    if self.local_models:
                        self.current_model = self.local_models[0]
                        model_manager.set_user_model(interaction.user.id, self.current_model, provider="local")
                    else:
                        self.current_model = "qwen2.5:7b"
                info = model_manager.get_user_model_info(interaction.user.id)
                self.cloud_models = info.get("cloud_history", [])
                self.options = [("local", m) for m in self.local_models]
                self.options.extend([("cloud", m) for m in self.cloud_models])
                self.page = min(self.page, max(0, self._total_pages() - 1))
                self.selected = None
                self._rebuild_components()
                await interaction.message.edit(
                    content=f"✅ Removed `{target_model}`.",
                    embed=self._current_embed(),
                    view=self,
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
                        "Active chat model updated.\n"
                        f"Basic interactions continue using local runtime model: `{local_runtime}`."
                    )
                else:
                    await interaction.followup.send(f"❌ {msg}")
                return

            info = model_manager.get_user_model_info(interaction.user.id)
            current_provider = info.get("provider", "local")
            current_model = info.get("model", "qwen2.5:7b")
            local_models = model_manager.list_all_models(refresh_local=True)
            cloud_models = model_manager.get_recent_cloud_models(interaction.user.id)
            if not local_models and not cloud_models:
                await interaction.followup.send(
                    "No models available in the menu yet.\n"
                    "Install a local model with **/pull-model local model_name** "
                    "(example: `/pull-model local llama3.2:3b`) or set a cloud model with "
                    "**/model cloud model_name**.",
                    ephemeral=False,
                )
                return
            if current_provider == "local" and current_model not in local_models and local_models:
                current_model = local_models[0]
            local_runtime = model_manager.get_last_local_model(interaction.user.id, refresh_local=True)
            embed = _model_embed(current_provider, current_model, local_runtime, None, 0, len(local_models) + len(cloud_models))
            view = ModelSelectView(
                client,
                interaction.user.id,
                current_provider=current_provider,
                current_model=current_model,
                local_models=local_models,
                cloud_models=cloud_models,
            )
            await interaction.followup.send(embed=embed, view=view)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}")
