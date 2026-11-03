import os
import sqlite3
import time
import aiohttp
import discord

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import quote
from pathlib import Path
from typing import Any, Iterable
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Shared with link-server/index.js, which writes pending_codes
DB_PATH = os.environ.get("LINK_DB_PATH", str(Path(__file__).parent / "links.db"))

db = sqlite3.connect(DB_PATH)
db.execute("PRAGMA journal_mode = WAL")
db.execute("PRAGMA busy_timeout = 5000")
db.executescript("""
    CREATE TABLE IF NOT EXISTS pending_codes (
        code       TEXT PRIMARY KEY,
        mc_uuid    TEXT NOT NULL,
        mc_name    TEXT NOT NULL,
        expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS links (
        discord_id INTEGER PRIMARY KEY,
        mc_uuid    TEXT NOT NULL UNIQUE,
        mc_name    TEXT NOT NULL,
        linked_at  INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS guild_members (
        mc_uuid    TEXT PRIMARY KEY,
        mc_name    TEXT NOT NULL,
        rank       TEXT NOT NULL,
        online     INTEGER NOT NULL,
        last_join  INTEGER,          -- unix seconds, NULL if unknown
        fetched_at INTEGER NOT NULL
    );
""")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="%", intents=intents)


class Role(StrEnum):
    EVERYONE = "@everyone"
    RECRUIT = "Recruit"
    RECRUITER = "Recruiter"
    CAPTAIN = "Captain"
    STRATEGIST = "Strategist"
    CHIEF = "Chief"
    OWNER = "Owner"

    @property
    def rank(self) -> int:
        # lower index = higher rank
        return list(Role).index(self)


def get_role(member: discord.Member) -> discord.Role | None:
    """Loops through the member's roles from highest to lowest and returns the first one in the Enum"""
    for r in reversed(member.roles):
        if r.name in Role:
            return r
    return None


@dataclass
class GuildMember:
    name: str
    uuid: str
    rank: Role
    online: bool
    last_join: datetime | None  # None if the player has never joined / hides it


# uuid -> member, filled from the Wynncraft API on startup
guild_members: dict[str, GuildMember] = {}


async def fetch_guild_members() -> dict[str, GuildMember]:
    guild_name = os.environ["WYNN_GUILD_NAME"]
    url = f"https://api.wynncraft.com/v3/guild/{quote(guild_name)}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            data = await resp.json()

    members: dict[str, GuildMember] = {}
    # data["members"] is {"total": n, "owner": {name: {...}}, "chief": {...}, ...}
    for rank_name, by_name in data["members"].items():
        if not isinstance(by_name, dict):
            continue
        rank = Role(rank_name.capitalize())
        for name, info in by_name.items():
            last_join = info.get("lastJoin")
            members[info["uuid"]] = GuildMember(
                name=name,
                uuid=info["uuid"],
                rank=rank,
                online=info.get("online", False),
                last_join=datetime.fromisoformat(last_join) if last_join else None,
            )
    return members


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


async def refresh_guild_members() -> bool:
    """Fetch the roster from the Wynncraft API into memory and the database."""
    try:
        members = await fetch_guild_members()
    except (aiohttp.ClientError, TimeoutError, KeyError, ValueError) as e:
        print(f"Failed to fetch Wynncraft guild members: {e!r}")
        return False

    guild_members.clear()
    guild_members.update(members)
    save_guild_members(members.values())
    return True


@bot.event
async def setup_hook():
    # Runs once before connecting to Discord (on_ready can fire again on reconnects)
    if not await refresh_guild_members():
        return

    print(f"Loaded {len(guild_members)} Wynncraft guild members:")
    for m in sorted(guild_members.values(), key=lambda m: -m.rank.rank):
        seen = (
            "online now"
            if m.online
            else m.last_join.strftime("%Y-%m-%d %H:%M UTC") if m.last_join else "never"
        )
        print(f"  {m.rank:<10} {m.name:<16} {seen}")


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"We have logged in as {bot.user}")


async def shift_rank(
    interaction: discord.Interaction, target_user: discord.Member, step: int
):
    """Move target_user `step` ranks up (+1) or down (-1) the Role ladder."""
    # guild_only() guarantees this at runtime; the check narrows the types for mypy
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return

    caller_role: discord.Role | Any | None = get_role(interaction.user)

    caller_index = 0
    if caller_role != None:
        caller_index = Role(caller_role.name).rank

    target_user_current_role = get_role(target_user)

    target_user_current_index = 0
    if target_user_current_role != None:
        target_user_current_index = Role(target_user_current_role.name).rank

    target_user_new_index = target_user_current_index + step

    if target_user_current_index >= caller_index:
        error = "You cannot change the rank of someone at or above your own."

    elif target_user_new_index >= caller_index:
        error = "You cannot promote someone to your own rank or higher."

    elif target_user_new_index < 0:
        error = "They are already at the lowest rank."

    elif (
        new_role := discord.utils.get(
            interaction.guild.roles, name=list(Role)[target_user_new_index]
        )
    ) is None:
        error = f"The {list(Role)[target_user_new_index]} role doesn't exist in this server."

    else:
        # shows up in the server's audit log next to the bot's action
        reason = f"/{'promote' if step > 0 else 'demote'} used by {interaction.user} ({interaction.user.id})"

        # @everyone is implicit on every member, so it can't be added or removed
        if (
            target_user_current_role is not None
            and target_user_current_role.name != Role.EVERYONE
        ):
            await target_user.remove_roles(target_user_current_role, reason=reason)

        if new_role.name != Role.EVERYONE:
            await target_user.add_roles(new_role, reason=reason)

        await interaction.response.send_message(
            f"{target_user.mention} is now {new_role.name}.", ephemeral=True
        )
        return

    await interaction.response.send_message(error, ephemeral=True)


@bot.tree.command(name="promote", description="Promote a user by one rank")
@app_commands.guild_only()
async def promote(interaction: discord.Interaction, target_user: discord.Member):
    await shift_rank(interaction, target_user, +1)


@bot.tree.command(name="demote", description="Demote a user by one rank")
@app_commands.guild_only()
async def demote(interaction: discord.Interaction, target_user: discord.Member):
    await shift_rank(interaction, target_user, -1)


async def set_rank_role(member: discord.Member, rank: Role, reason: str) -> str:
    """Give `member` the Discord role for `rank`, removing any other rank roles.

    Returns a message describing what happened, for the user.
    """
    if rank == Role.EVERYONE:
        target = None
    elif (target := discord.utils.get(member.guild.roles, name=rank)) is None:
        return f"The **{rank}** role doesn't exist in this server, so I couldn't assign it."

    # Every rank role they have except the one they should have
    stale = [
        r for r in member.roles if r.name in Role and r != target and not r.is_default()
    ]

    try:
        if stale:
            await member.remove_roles(*stale, reason=reason)
        if target is not None and target not in member.roles:
            await member.add_roles(target, reason=reason)
    except discord.Forbidden:
        return (
            f"I don't have permission to give you the **{rank}** role. "
            "My role needs to be above the rank roles in the server settings."
        )
    return f"You've been given the **{rank}** role."


@bot.tree.command(
    name="link", description="Link your Minecraft account using the code you got"
)
@app_commands.guild_only()
async def link(interaction: discord.Interaction, code: str):
    # guild_only() guarantees this at runtime; the check narrows the type for mypy
    if not isinstance(interaction.user, discord.Member):
        return

    # Fetching the roster can take a few seconds; Discord needs a reply within 3
    await interaction.response.defer(ephemeral=True)
    code = code.strip().upper().replace("-", "").replace(" ", "")

    with db:  # one transaction: consume the code and write the link together
        row = db.execute(
            "DELETE FROM pending_codes WHERE code = ? AND expires_at > ? RETURNING mc_uuid, mc_name",
            (code, int(time.time())),
        ).fetchone()

        if row is None:
            await interaction.followup.send(
                "That code is invalid or expired. Join the link server again to get a new one.",
                ephemeral=True,
            )
            return

        mc_uuid, mc_name = row
        # The code is fresh proof of owning both accounts, so it replaces any
        # previous link either account had.
        db.execute(
            "DELETE FROM links WHERE discord_id = ? OR mc_uuid = ?",
            (interaction.user.id, mc_uuid),
        )
        db.execute(
            "INSERT INTO links (discord_id, mc_uuid, mc_name, linked_at) VALUES (?, ?, ?, ?)",
            (interaction.user.id, mc_uuid, mc_name, int(time.time())),
        )

    message = f"Linked to Minecraft account **{mc_name}**."

    # Use the latest roster so people who joined the guild since startup are
    # found; if the API is down this falls back to the last saved roster.
    await refresh_guild_members()
    rank_row = db.execute(
        "SELECT rank FROM guild_members WHERE mc_uuid = ?", (mc_uuid,)
    ).fetchone()

    if rank_row is None:
        message += f"\n**{mc_name}** isn't in the {os.environ['WYNN_GUILD_NAME']} guild, so no rank role was given."
    else:
        reason = f"/link: {mc_name} is {rank_row[0]} in {os.environ['WYNN_GUILD_NAME']}"
        message += "\n" + await set_rank_role(
            interaction.user, Role(rank_row[0]), reason
        )

    await interaction.followup.send(message, ephemeral=True)


@bot.tree.command(name="unlink", description="Unlink your Minecraft account")
async def unlink(interaction: discord.Interaction):
    with db:
        row = db.execute(
            "DELETE FROM links WHERE discord_id = ? RETURNING mc_name",
            (interaction.user.id,),
        ).fetchone()

    if row is None:
        message = "You don't have a linked Minecraft account."
    else:
        message = f"Unlinked Minecraft account **{row[0]}**."
    await interaction.response.send_message(message, ephemeral=True)


bot.run(os.environ["DISCORD_TOKEN"])
