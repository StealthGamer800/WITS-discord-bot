import os
import sqlite3
import time
import discord

from enum import StrEnum
from pathlib import Path
from typing import Any
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


@bot.tree.command(
    name="link", description="Link your Minecraft account using the code you got"
)
async def link(interaction: discord.Interaction, code: str):
    code = code.strip().upper().replace("-", "").replace(" ", "")

    with db:  # one transaction: consume the code and write the link together
        row = db.execute(
            "DELETE FROM pending_codes WHERE code = ? AND expires_at > ? RETURNING mc_uuid, mc_name",
            (code, int(time.time())),
        ).fetchone()

        if row is None:
            await interaction.response.send_message(
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

    await interaction.response.send_message(
        f"Linked to Minecraft account **{mc_name}**.", ephemeral=True
    )


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
