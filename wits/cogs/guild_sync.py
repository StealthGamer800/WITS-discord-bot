"""Keeps the guild roster and rank roles in sync: on startup, every hour, and on /sync."""

import asyncio
from datetime import time, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from wits.roster import refresh_guild_members

# Every hour on the hour (UTC)
EVERY_HOUR = [time(hour=h, tzinfo=timezone.utc) for h in range(24)]


class GuildSync(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self.hourly_refresh.start()
        # In the background: role syncing has to wait until the bot has connected,
        # which can't happen until setup_hook (which loads this cog) returns.
        self._startup_task = asyncio.create_task(self.startup_refresh())

    async def cog_unload(self) -> None:
        self.hourly_refresh.cancel()
        if self._startup_task is not None:
            self._startup_task.cancel()

    async def startup_refresh(self) -> None:
        result = await refresh_guild_members(self.bot)
        if result is None:
            return

        print(f"Loaded {len(result.members)} Wynncraft guild members:")
        for m in sorted(result.members, key=lambda m: -m.rank.rank):
            seen = (
                "online now"
                if m.online
                else (
                    m.last_join.strftime("%Y-%m-%d %H:%M UTC")
                    if m.last_join
                    else "never"
                )
            )
            print(f"  {m.rank:<10} {m.name:<16} {seen}")

    @tasks.loop(time=EVERY_HOUR)
    async def hourly_refresh(self) -> None:
        if result := await refresh_guild_members(self.bot):
            print(f"Hourly refresh: {len(result.members)} Wynncraft guild members")

    @app_commands.command(
        name="sync",
        description="Fetch the latest Wynncraft guild data and update rank roles",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        result = await refresh_guild_members(self.bot)
        if result is None:
            await interaction.followup.send(
                "Couldn't reach the Wynncraft API, try again in a bit.", ephemeral=True
            )
            return

        lines = [f"Synced **{len(result.members)}** guild members."]
        if result.roster_changes:
            lines += ["", "**Roster changes**"] + [
                f"- {c}" for c in result.roster_changes
            ]
        if result.role_updates:
            lines += ["", "**Role updates**"] + [f"- {u}" for u in result.role_updates]
        if not result.roster_changes:
            lines.append("No rank changes since the last sync.")

        message = "\n".join(lines)
        # Discord's message limit; full detail is in the bot log
        if len(message) > 2000:
            message = message[:1950] + "\n… (truncated, see the bot log)"
        await interaction.followup.send(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildSync(bot))
