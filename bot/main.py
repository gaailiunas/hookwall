import os
from typing import Any

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
ROOT_TOKEN = os.getenv("ROOT_TOKEN")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is required in .env")

if not ROOT_TOKEN:
    raise RuntimeError("ROOT_TOKEN is required in .env")


def api_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {ROOT_TOKEN}",
        "Content-Type": "application/json",
    }


class SecureWebhookBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        await self.tree.sync()
        print("Slash commands synced.")


bot = SecureWebhookBot()


async def api_get(path: str) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{API_BASE_URL}{path}",
            headers=api_headers(),
        )
        try:
            data = response.json()
        except Exception:
            data = response.text
        return response.status_code, data


async def api_post(path: str) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{API_BASE_URL}{path}",
            headers=api_headers(),
        )
        try:
            data = response.json()
        except Exception:
            data = response.text
        return response.status_code, data


async def api_delete(path: str) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.delete(
            f"{API_BASE_URL}{path}",
            headers=api_headers(),
        )
        try:
            data = response.json()
        except Exception:
            data = response.text
        return response.status_code, data


def api_error_detail(status_code: int, data: Any) -> str:
    if isinstance(data, dict):
        detail = data.get("detail", str(data))
    else:
        detail = str(data)
    return f"API error {status_code}: {detail}"


async def requester_is_moderator(user_id: int) -> bool:
    status_code, data = await api_get(f"/moderators/{user_id}")
    if status_code != 200 or not isinstance(data, dict):
        return False
    return bool(data.get("is_moderator", False))


def base_embed(
    interaction: discord.Interaction,
    title: str,
    description: str,
    color: discord.Color,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )
    embed.set_footer(
        text=f"Requested by {interaction.user}",
        icon_url=interaction.user.display_avatar.url,
    )
    return embed


def success_embed(
    interaction: discord.Interaction,
    title: str,
    description: str,
) -> discord.Embed:
    return base_embed(interaction, title, description, discord.Color.green())


def error_embed(
    interaction: discord.Interaction,
    title: str,
    description: str,
) -> discord.Embed:
    return base_embed(interaction, title, description, discord.Color.red())


def info_embed(
    interaction: discord.Interaction,
    title: str,
    description: str,
) -> discord.Embed:
    return base_embed(interaction, title, description, discord.Color.blurple())


async def send_api_error(
    interaction: discord.Interaction,
    status_code: int,
    data: Any,
) -> None:
    embed = error_embed(
        interaction,
        "Request failed",
        api_error_detail(status_code, data),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


async def send_permission_error(interaction: discord.Interaction) -> None:
    embed = error_embed(
        interaction,
        "Access denied",
        "Moderator access is required for this command.",
    )
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


def format_relay_log_time(created_at: Any) -> str:
    if isinstance(created_at, (int, float)):
        timestamp = int(created_at)
        return f"<t:{timestamp}:f> • <t:{timestamp}:R>"
    return str(created_at)


def format_relay_log_lines(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No relay logs found."

    lines = []
    for item in items:
        uid = item.get("uid", "unknown")
        created_at = item.get("created_at", "unknown")
        status_code = item.get("status_code", "unknown")

        lines.append(
            f"**UID:** `{uid}`\n"
            f"**Created:** {format_relay_log_time(created_at)}\n"
            f"**Status:** `{status_code}`"
        )
    return "\n".join(lines)


def relay_logs_embed(
    interaction: discord.Interaction,
    items: list[dict[str, Any]],
    page: int,
    total_pages: int,
    total: int,
) -> discord.Embed:
    embed = info_embed(
        interaction,
        "Relay logs",
        format_relay_log_lines(items),
    )
    embed.add_field(name="Page", value=f"{page}/{total_pages}", inline=True)
    embed.add_field(name="Total logs", value=str(total), inline=True)
    return embed


class RelayLogsView(discord.ui.View):
    def __init__(
        self,
        requestor_id: int,
        page: int,
        page_size: int,
        total_pages: int,
    ) -> None:
        super().__init__(timeout=300)
        self.requestor_id = requestor_id
        self.page = page
        self.page_size = page_size
        self.total_pages = total_pages
        self.sync_buttons()

    def sync_buttons(self) -> None:
        self.previous_page.disabled = self.page <= 1
        self.next_page.disabled = self.page >= self.total_pages

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requestor_id:
            await interaction.response.send_message(
                "Only the moderator who opened this view can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    async def update_message(self, interaction: discord.Interaction) -> None:
        status_code, data = await api_get(
            f"/relay/logs?page={self.page}&page_size={self.page_size}"
        )

        if status_code != 200 or not isinstance(data, dict):
            embed = error_embed(
                interaction,
                "Request failed",
                api_error_detail(status_code, data),
            )
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
            return

        items = data.get("items", [])
        self.total_pages = max(1, int(data.get("total_pages", 1)))
        total = int(data.get("total", 0))
        self.sync_buttons()

        embed = relay_logs_embed(
            interaction,
            items,
            self.page,
            self.total_pages,
            total,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        if self.page > 1:
            self.page -= 1
        await self.update_message(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        if self.page < self.total_pages:
            self.page += 1
        await self.update_message(interaction)


@bot.event
async def on_ready() -> None:
    if bot.user:
        print(f"Logged in as {bot.user} ({bot.user.id})")


@bot.tree.command(name="ping", description="Check if the bot is alive.")
async def ping(interaction: discord.Interaction) -> None:
    embed = success_embed(
        interaction,
        "Pong",
        "Bot is online and responding.",
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="get_token", description="Create or rotate a member token.")
@app_commands.describe(user="Discord user to issue a token for")
async def get_token(interaction: discord.Interaction, user: discord.Member) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    status_code, data = await api_post(f"/tokens/{user.id}")

    if status_code != 200:
        await send_api_error(interaction, status_code, data)
        return

    token = data.get("token", "unknown")
    uid = data.get("uid", user.id)
    is_moderator = data.get("is_moderator", False)

    embed = success_embed(
        interaction,
        "Token issued",
        f"A new token was created for {user.mention}.",
    )
    embed.add_field(name="UID", value=str(uid), inline=False)
    embed.add_field(name="Moderator", value=str(is_moderator), inline=True)
    embed.add_field(
        name="Token",
        value=f"```{token}```",
        inline=False,
    )
    embed.add_field(
        name="Important",
        value="This token is shown once. Deliver it privately and store it securely.",
        inline=False,
    )

    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="delete_token", description="Delete a member token.")
@app_commands.describe(user="Discord user to delete the token for")
async def delete_token(interaction: discord.Interaction, user: discord.Member) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    status_code, data = await api_delete(f"/tokens/{user.id}")

    if status_code != 200:
        await send_api_error(interaction, status_code, data)
        return

    embed = success_embed(
        interaction,
        "Token deleted",
        f"Deleted the token for {user.mention}.",
    )
    embed.add_field(name="UID", value=str(user.id), inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="promote", description="Promote a user to moderator.")
@app_commands.describe(user="Discord user to promote")
async def promote(interaction: discord.Interaction, user: discord.Member) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    status_code, data = await api_post(f"/moderators/{user.id}")

    if status_code != 200:
        await send_api_error(interaction, status_code, data)
        return

    embed = success_embed(
        interaction,
        "Moderator promoted",
        f"{user.mention} is now a moderator.",
    )
    embed.add_field(name="UID", value=str(user.id), inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="demote", description="Demote a moderator.")
@app_commands.describe(user="Discord user to demote")
async def demote(interaction: discord.Interaction, user: discord.Member) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    status_code, data = await api_delete(f"/moderators/{user.id}")

    if status_code != 200:
        await send_api_error(interaction, status_code, data)
        return

    embed = success_embed(
        interaction,
        "Moderator demoted",
        f"{user.mention} is no longer a moderator.",
    )
    embed.add_field(name="UID", value=str(user.id), inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(
    name="is_moderator", description="Check whether a user is a moderator."
)
@app_commands.describe(user="Discord user to check")
async def is_moderator(interaction: discord.Interaction, user: discord.Member) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)

    status_code, data = await api_get(f"/moderators/{user.id}")

    if status_code != 200:
        await send_api_error(interaction, status_code, data)
        return

    mod_status = data.get("is_moderator", False)

    embed = info_embed(
        interaction,
        "Moderator lookup",
        f"Checked moderator status for {user.mention}.",
    )
    embed.add_field(name="UID", value=str(user.id), inline=False)
    embed.add_field(
        name="Is moderator",
        value="Yes" if mod_status else "No",
        inline=False,
    )

    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(
    name="relay_logs", description="View relay logs with moderator-only pagination."
)
async def relay_logs(interaction: discord.Interaction) -> None:
    if not await requester_is_moderator(interaction.user.id):
        await send_permission_error(interaction)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    page = 1
    page_size = 10
    status_code, data = await api_get(f"/relay/logs?page={page}&page_size={page_size}")

    if status_code != 200 or not isinstance(data, dict):
        await send_api_error(interaction, status_code, data)
        return

    items = data.get("items", [])
    total = int(data.get("total", 0))
    total_pages = max(1, int(data.get("total_pages", 1)))

    embed = relay_logs_embed(interaction, items, page, total_pages, total)
    view = RelayLogsView(
        requestor_id=interaction.user.id,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )

    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


bot.run(DISCORD_BOT_TOKEN)
