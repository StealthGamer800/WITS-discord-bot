"""Keeping the saved guild roster, and linked members' Discord roles, up to date."""

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass

import aiohttp
import discord
from discord.ext import commands

from wits.config import DISCORD_GUILD_ID
from wits.db import db
from wits.ranks import set_rank_role
from wits.wynncraft import GuildMember, fetch_guild_members


@dataclass
class RefreshResult:
    members: list[GuildMember]
    roster_changes: list[str]  # promotions/demotions, joins and leaves
    role_updates: list[str]  # what happened to linked Discord members


# The hourly loop, startup and /sync can all refresh; don't let them interleave
# or the same change could be diffed and applied twice.
_refresh_lock = asyncio.Lock()


def save_guild_members(members: Iterable[GuildMember]) -> None:
    """Replace the stored roster with `members`, so people who left are removed."""
    now = int(time.time())
    with db:
        db.execute("DELETE FROM guild_members")
        db.executemany(
            "INSERT INTO guild_members (mc_uuid, mc_name, rank, online, last_join, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    m.uuid,
                    m.name,
                    str(m.rank),
                    int(m.online),
                    int(m.last_join.timestamp()) if m.last_join else None,
                    now,
                )
                for m in members
            ],
        )


async def refresh_guild_members(bot: commands.Bot) -> RefreshResult | None:
    """Fetch the roster from the Wynncraft API into the database, then update
    Discord roles for linked members whose rank changed.

    Returns None if the API request failed."""
    async with _refresh_lock:
        try:
            members = await fetch_guild_members()
        except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as e:
            print(f"Failed to fetch Wynncraft guild members: {e!r}")
            return None

        # Diff against the saved roster (not memory) so changes that happened
        # while the bot was offline are picked up on the next startup.
        old: dict[str, tuple[str, str]] = {
            uuid: (rank, name)
            for uuid, rank, name in db.execute(
                "SELECT mc_uuid, rank, mc_name FROM guild_members"
            )
        }
        # New rank, or newly joined the guild
        changed = [
            m for m in members.values() if m.uuid not in old or old[m.uuid][0] != m.rank
        ]

        roster_changes = [
            (
                f"{m.name}: {old[m.uuid][0]} → {m.rank}"
                if m.uuid in old
                else f"{m.name} joined as {m.rank}"
            )
            for m in changed
        ]
        roster_changes += [
            f"{name} left the guild (Discord roles left unchanged)"
            for uuid, (_, name) in old.items()
            if uuid not in members
        ]

        save_guild_members(members.values())

        for line in roster_changes:
            print(f"Roster: {line}")
        role_updates = await sync_rank_roles(bot, changed) if changed else []

        return RefreshResult(list(members.values()), roster_changes, role_updates)


async def sync_rank_roles(bot: commands.Bot, changed: list[GuildMember]) -> list[str]:
    """Give linked Discord members the role for their new Wynncraft rank.

    Returns a line per linked member describing what happened."""
    placeholders = ",".join("?" * len(changed))
    linked: dict[str, int] = {
        uuid: discord_id
        for discord_id, uuid in db.execute(
            f"SELECT discord_id, mc_uuid FROM links WHERE mc_uuid IN ({placeholders})",
            [m.uuid for m in changed],
        )
    }
    if not linked:
        return []

    # The startup refresh runs before the bot has connected
    await bot.wait_until_ready()
    guild = bot.get_guild(DISCORD_GUILD_ID)
    if guild is None:
        print("Can't sync rank roles: bot isn't in DISCORD_GUILD_ID")
        return ["Couldn't update roles: the bot isn't in the configured server."]

    updates = []
    for m in changed:
        if (discord_id := linked.get(m.uuid)) is None:
            continue
        try:
            member = await guild.fetch_member(discord_id)
        except discord.NotFound:
            continue  # linked, but no longer in the Discord server
        result = await set_rank_role(
            member, m.rank, f"Wynncraft rank sync: {m.name} is now {m.rank}"
        )
        print(f"Rank sync {m.name} -> {member}: {result}")
        updates.append(f"{member.mention} ({m.name}): {result}")
    return updates
