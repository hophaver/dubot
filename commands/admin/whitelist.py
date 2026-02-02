"""View whitelist or set a user's role. /whitelist to view; /whitelist @user admin to add/set (admin only)."""
from typing import Optional
import discord
from discord import app_commands
from whitelist import is_admin, get_user_permission, set_user_role, load_whitelist


async def build_whitelist_embed(client: discord.Client) -> discord.Embed:
    data = load_whitelist()
    role_config = [("admin", "👑 Admin"), ("himas", "🏠 Himas"), ("user", "👤 User")]
    all_ids = list(dict.fromkeys(uid for key, _ in role_config for uid in data.get(key, []) or []))
    names = {}
    for uid in all_ids[:50]:
        try:
            u = await client.fetch_user(int(uid))
            names[uid] = u.display_name or str(uid)
        except Exception:
            names[uid] = f"ID {uid}"
    embed = discord.Embed(
        title="Whitelist",
        description="Use `/whitelist @user admin` (or himas/user) to add or change a user's role. Admin only.",
        color=discord.Color.dark_blue(),
    )
    embed.set_thumbnail(url=client.user.display_avatar.url if client.user else None)
    for key, label in role_config:
        ids = data.get(key, []) or []
        if not ids:
            embed.add_field(name=f"{label} — 0", value="*No members*", inline=False)
        else:
            value = "\n".join(f"• {names.get(uid, str(uid))}" for uid in ids[:20])
            if len(ids) > 20:
                value += f"\n*… and {len(ids) - 20} more*"
            embed.add_field(name=f"{label} — {len(ids)}", value=value, inline=False)
    return embed


def register(client: discord.Client):
    @client.tree.command(name="whitelist", description="View whitelist, or set a user's role (admin only for set)")
    @app_commands.describe(
        user="User to add or change (optional; omit to just view)",
        role="Role: admin, himas, or user (required if user is set)",
    )
    async def whitelist(
        interaction: discord.Interaction,
        user: Optional[discord.User] = None,
        role: Optional[str] = None,
    ):
        if user is not None and role is not None:
            if not is_admin(interaction.user.id):
                await interaction.response.send_message("❌ Admin only.", ephemeral=True)
                return
            role_clean = role.strip().lower()
            if role_clean not in ("admin", "himas", "user"):
                await interaction.response.send_message("❌ Role must be: admin, himas, or user.", ephemeral=True)
                return
            if set_user_role(user.id, role_clean):
                await interaction.response.send_message(f"✅ <@{user.id}> set to **{role_clean}**.", ephemeral=False)
            else:
                await interaction.response.send_message("❌ Could not set (protected user or invalid).", ephemeral=True)
            return
        if user is not None or role is not None:
            await interaction.response.send_message(
                "❌ Provide both user and role to set, or omit both to view the whitelist.",
                ephemeral=True,
            )
            return
        if not get_user_permission(interaction.user.id):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.defer()
        embed = await build_whitelist_embed(client)
        await interaction.followup.send(embed=embed)