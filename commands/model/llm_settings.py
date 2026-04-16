"""Combined per-function LLM model (local/cloud) and persona configuration."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import discord
from discord.ui import View, Select, Button

from commands.shared import bot_embed_thumbnail_url
from config import get_current_persona, set_current_persona
from llm_function_prefs import (
    LLM_FUNCTION_KEYS,
    function_label,
    get_function_persona_name,
    set_function_persona_name,
)
from models import model_manager
from personas import persona_manager
from utils.llm_service import validate_and_set_function_model, validate_and_set_model
from whitelist import get_user_permission, is_admin


def _status_lines(user_id: int) -> Tuple[str, str]:
    """Return (persona_block, model_block) markdown for embed."""
    global_p = get_current_persona()
    base = model_manager.get_user_model_info(user_id)
    base_line = f"**Default chat model:** `{base.get('model')}` (`{base.get('provider')}`)"

    p_lines = [f"**Global default persona:** `{global_p}`"]
    for k in LLM_FUNCTION_KEYS:
        p_lines.append(f"· **{function_label(k)}:** `{get_function_persona_name(k)}`")
    persona_block = "\n".join(p_lines)

    m_lines = [base_line]
    for k in LLM_FUNCTION_KEYS:
        eff = model_manager.get_effective_model_for_function(user_id, k)
        ov = model_manager.get_function_model_override(user_id, k)
        tag = "override" if ov else "default"
        m_lines.append(f"· **{function_label(k)}:** `{eff['model']}` (`{eff['provider']}`) · _{tag}_")
    model_block = "\n".join(m_lines)
    return persona_block, model_block


def _build_model_pairs(user_id: int, fn_key: str) -> List[Optional[Tuple[str, str]]]:
    """Ordered choices for the model menu. Index 0 = clear override (use default chat model). Max 25 entries total."""
    model_manager.refresh_local_models()
    local_models = model_manager.list_all_models(refresh_local=False)
    info = model_manager.get_user_model_info(user_id)
    cloud_hist = info.get("cloud_history", []) if isinstance(info.get("cloud_history"), list) else []
    fm = info.get("function_models") if isinstance(info.get("function_models"), dict) else {}

    pairs: List[Optional[Tuple[str, str]]] = [None]
    seen: set[Tuple[str, str]] = set()

    def add_pair(provider: str, model_name: str) -> None:
        model_name = str(model_name or "").strip()
        if not model_name:
            return
        provider = (provider or "local").strip().lower()
        if provider not in {"local", "cloud"}:
            provider = "local"
        key = (provider, model_name)
        if key in seen:
            return
        seen.add(key)
        pairs.append((provider, model_name))

    if fn_key == "image_generation":
        for m in cloud_hist:
            add_pair("cloud", str(m))
        for m in model_manager.list_known_image_generation_cloud_models():
            add_pair("cloud", m)
        if isinstance(fm, dict):
            for slot in fm.values():
                if isinstance(slot, dict) and str(slot.get("provider", "")).lower() == "cloud":
                    add_pair("cloud", str(slot.get("model", "")))
        eff = model_manager.get_effective_model_for_function(user_id, fn_key)
        if str(eff.get("model", "") or "").strip():
            add_pair("cloud", str(eff["model"]))
        return pairs[:25]

    add_pair(str(info.get("provider", "local")), str(info.get("model", "")))
    for m in local_models:
        add_pair("local", m)
    for m in cloud_hist:
        add_pair("cloud", str(m))
    if isinstance(fm, dict):
        for slot in fm.values():
            if isinstance(slot, dict):
                add_pair(str(slot.get("provider", "local")), str(slot.get("model", "")))

    eff = model_manager.get_effective_model_for_function(user_id, fn_key)
    add_pair(eff["provider"], eff["model"])

    return pairs[:25]


def _model_pick_label(pairs: List[Optional[Tuple[str, str]]], idx: Optional[int]) -> str:
    if idx is None:
        return "(unchanged)"
    if idx < 0 or idx >= len(pairs):
        return "(unchanged)"
    slot = pairs[idx]
    if slot is None:
        return "Default (my chat model)"
    p, m = slot
    return f"{p}: `{m}`"


def _persona_pick_label(val: Optional[str]) -> str:
    if not val:
        return "(unchanged)"
    if val == "__global_default__":
        return "Global default persona"
    return f"`{val}`"


def _build_embed(
    user_id: int,
    fn_key: str,
    pairs: List[Optional[Tuple[str, str]]],
    pending_model_idx: Optional[int],
    pending_persona: Optional[str],
    thumbnail_url: Optional[str],
    footer: str = "",
) -> discord.Embed:
    persona_block, model_block = _status_lines(user_id)
    embed = discord.Embed(
        title="LLM settings (per function)",
        description=(
            "Use the three menus, then **Apply**. "
            "**Adaptive DM** uses the **default chat model** only and ignores per-function personas "
            "(**image generation** has its own model below; `/imagine` and adaptive-triggered images use it).\n\n"
            "**Personas** (per function; global, admin to change)\n"
            f"{persona_block}\n\n"
            "**Models** (per function; per user)\n"
            f"{model_block}"
        ),
        color=discord.Color.dark_teal(),
    )
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    embed.add_field(
        name="Pending for this message",
        value=(
            f"**Function:** {function_label(fn_key)}\n"
            f"**Model:** {_model_pick_label(pairs, pending_model_idx)}\n"
            f"**Persona:** {_persona_pick_label(pending_persona)}"
        ),
        inline=False,
    )
    if footer:
        embed.set_footer(text=footer)
    return embed


def _persona_options() -> List[discord.SelectOption]:
    names = persona_manager.list_personas()
    opts: List[discord.SelectOption] = [
        discord.SelectOption(
            label="Global default persona",
            value="__global_default__",
            description="Same as config “current” persona",
        )
    ]
    for n in names[:24]:
        opts.append(discord.SelectOption(label=n[:100], value=n[:100]))
    return opts[:25]


class LLMSettingsView(View):
    def __init__(self, client: discord.Client, user_id: int, fn_key: str, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.client = client
        self.user_id = user_id
        self.fn_key = fn_key if fn_key in LLM_FUNCTION_KEYS else "chat"
        self._model_pairs: List[Optional[Tuple[str, str]]] = _build_model_pairs(user_id, self.fn_key)
        self.pending_model_idx: Optional[int] = None
        self.pending_persona: Optional[str] = None
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        self._model_pairs = _build_model_pairs(self.user_id, self.fn_key)
        self.clear_items()
        fn_opts = [discord.SelectOption(label=function_label(k)[:100], value=k) for k in LLM_FUNCTION_KEYS]
        fn_sel = Select(placeholder="1 · Function", options=fn_opts, custom_id="llm_fn")
        fn_sel.callback = self._on_fn
        self.add_item(fn_sel)

        m_opts: List[discord.SelectOption] = []
        for i, pair in enumerate(self._model_pairs):
            if pair is None:
                m_opts.append(
                    discord.SelectOption(
                        label="Default (my chat model)"[:100],
                        value=str(i),
                        description="Clear per-function override",
                    )
                )
            else:
                prov, mname = pair
                eff = model_manager.get_effective_model_for_function(self.user_id, self.fn_key)
                is_eff = prov == eff.get("provider") and mname == eff.get("model")
                m_opts.append(
                    discord.SelectOption(
                        label=f"[{prov}] {mname}"[:100],
                        value=str(i),
                        description="Current for this function" if is_eff else None,
                    )
                )
        if m_opts:
            m_sel = Select(placeholder="2 · Model (local or cloud)", options=m_opts, custom_id="llm_model")
            m_sel.callback = self._on_model
            self.add_item(m_sel)

        p_sel = Select(placeholder="3 · Persona", options=_persona_options(), custom_id="llm_persona")
        p_sel.callback = self._on_persona
        self.add_item(p_sel)

        apply_btn = Button(label="Apply", style=discord.ButtonStyle.success, custom_id="llm_apply")
        apply_btn.callback = self._on_apply
        self.add_item(apply_btn)

        global_btn = Button(
            label="Set global default persona (admin)",
            style=discord.ButtonStyle.secondary,
            custom_id="llm_global_persona",
        )
        global_btn.callback = self._on_global_persona
        self.add_item(global_btn)

    async def _edit(self, interaction: discord.Interaction):
        embed = _build_embed(
            self.user_id,
            self.fn_key,
            self._model_pairs,
            self.pending_model_idx,
            self.pending_persona,
            bot_embed_thumbnail_url(self.client.user),
        )
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def _on_fn(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        v = (interaction.data.get("values") or [None])[0]
        if v in LLM_FUNCTION_KEYS:
            self.fn_key = v
        self.pending_model_idx = None
        self.pending_persona = None
        self._rebuild_rows()
        await self._edit(interaction)

    async def _on_model(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        raw = (interaction.data.get("values") or [None])[0]
        try:
            self.pending_model_idx = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            self.pending_model_idx = None
        await self._edit(interaction)

    async def _on_persona(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        self.pending_persona = (interaction.data.get("values") or [None])[0]
        await self._edit(interaction)

    async def _on_apply(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        await interaction.response.defer()
        msgs: List[str] = []

        if self.pending_model_idx is not None:
            idx = self.pending_model_idx
            if idx < 0 or idx >= len(self._model_pairs):
                msgs.append("Model: invalid selection.")
            else:
                slot = self._model_pairs[idx]
                if slot is None:
                    if self.fn_key == "chat":
                        await interaction.followup.send(
                            "Chat always uses the default chat model. Pick a concrete model to set your default, "
                            "or choose another function to clear an override.",
                            ephemeral=True,
                        )
                    else:
                        model_manager.clear_function_model(self.user_id, self.fn_key)
                        msgs.append("Model: cleared override (uses default chat model).")
                else:
                    prov, mname = slot
                    if self.fn_key == "chat":
                        ok, msg = await validate_and_set_model(self.user_id, prov, mname)
                    elif self.fn_key == "image_generation":
                        ok, msg = await validate_and_set_function_model(self.user_id, "image_generation", prov, mname)
                    else:
                        ok, msg = await validate_and_set_function_model(self.user_id, self.fn_key, prov, mname)
                    msgs.append("Model: " + msg)
            self.pending_model_idx = None

        if self.pending_persona:
            if not is_admin(interaction.user.id):
                msgs.append("Persona: admin only (unchanged).")
            else:
                if self.pending_persona == "__global_default__":
                    set_function_persona_name(self.fn_key, "__default__")
                    msgs.append("Persona: reset to global default for this function.")
                else:
                    if persona_manager.persona_exists(self.pending_persona):
                        set_function_persona_name(self.fn_key, self.pending_persona)
                        msgs.append(f"Persona: set to `{self.pending_persona}` for this function.")
                    else:
                        msgs.append("Persona: name not found.")
            self.pending_persona = None

        self._rebuild_rows()
        foot = " · ".join(msgs) if msgs else "Nothing applied — pick model and/or persona first."
        embed = _build_embed(
            self.user_id,
            self.fn_key,
            self._model_pairs,
            None,
            None,
            bot_embed_thumbnail_url(self.client.user),
            footer=foot[:2048],
        )
        await interaction.edit_original_response(embed=embed, view=self)

    async def _on_global_persona(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the user who ran the command can use this.", ephemeral=True)
            return
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        if not self.pending_persona or self.pending_persona == "__global_default__":
            await interaction.response.send_message(
                "Pick a **persona** in menu 3 first (not “Global default persona”).",
                ephemeral=True,
            )
            return
        if not persona_manager.persona_exists(self.pending_persona):
            await interaction.response.send_message("❌ Unknown persona name.", ephemeral=True)
            return
        set_current_persona(self.pending_persona)
        await interaction.response.send_message(
            f"✅ Bot-wide default persona set to **`{self.pending_persona}`** "
            "(functions that use “Global default persona” inherit this).",
            ephemeral=True,
        )

    async def on_timeout(self) -> None:
        try:
            ch = self.message.channel if self.message else None
            if ch and self.message:
                embed = _build_embed(
                    self.user_id,
                    self.fn_key,
                    self._model_pairs,
                    None,
                    None,
                    bot_embed_thumbnail_url(self.client.user),
                    footer="Menus expired — run /llm-settings again.",
                )
                await self.message.edit(embed=embed, view=None)
        except Exception:
            pass


def register(client: discord.Client):
    @client.tree.command(
        name="llm-settings",
        description="Per-function model (local/cloud) and persona; default chat; separate OpenRouter image model",
    )
    async def llm_settings(interaction: discord.Interaction):
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        if not persona_manager.list_personas():
            await interaction.response.send_message("📝 No personas available. Use `/persona-create` first.")
            return
        view = LLMSettingsView(client, interaction.user.id, "chat")
        embed = _build_embed(
            interaction.user.id,
            "chat",
            view._model_pairs,
            None,
            None,
            bot_embed_thumbnail_url(interaction.client.user),
        )
        await interaction.response.send_message(embed=embed, view=view)
        try:
            view.message = await interaction.original_response()
        except Exception:
            view.message = None
