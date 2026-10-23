
import asyncio
import os
import re
import sqlite3
from enum import StrEnum

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ---------- Configuration ----------

intents = discord.Intents.default()
intents.members = True

GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])
OFFICER_CHANNEL_ID = int(os.environ["OFFICER_CHANNEL_ID"])
WYNN_GUILD = os.environ["WYNN_GUILD_NAME"]
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
API = "https://api.wynncraft.com/v3"


# Highest rank first.
class Role(StrEnum):
    OWNER = "Owner"
    CHIEF = "Chief"
    STRATEGIST = "Strategist"
    CAPTAIN = "Captain"
    RECRUITER = "Recruiter"
    RECRUIT = "Recruit"
    EVERYONE = "@everyone"

    @property
    def rank(self) -> int:
        return list(Role).index(self)


RANK_ROLES = {
    role.value: role
    for role in Role
    if role != Role.EVERYONE
}


# ---------- Database ----------

db = sqlite3.connect("links.db")
db.execute(
    """
    CREATE TABLE IF NOT EXISTS links (
        discord_id INTEGER PRIMARY KEY,
        uuid TEXT UNIQUE NOT NULL,
        mc_name TEXT NOT NULL
    )
    """
)
db.commit()


# ---------- Role helpers ----------

def get_role(member: discord.Member) -> Role | None:
    """Return the member's highest recognized rank."""
    ranks = [
        RANK_ROLES[discord_role.name]
        for discord_role in member.roles
        if discord_role.name in RANK_ROLES
    ]

    return min(ranks, key=lambda role: role.rank) if ranks else None


def can_manage_roles(member: discord.Member) -> bool:
    """Only Recruiter or higher can manage roles."""
    rank = get_role(member)
    return rank is not None and rank.rank <= Role.RECRUITER.rank


async def apply_rank(
    member: discord.Member,
    rank: Role | None,
) -> str | None:
    """Give the member exactly one rank role, or remove all rank roles."""
    guild = member.guild

    rank_roles = {
        discord_role
        for discord_role in guild.roles
        if discord_role.name in RANK_ROLES
    }

    target = None
    if rank is not None and rank != Role.EVERYONE:
        target = discord.utils.get(guild.roles, name=rank.value)
        if target is None:
            raise ValueError(f"Discord role '{rank.value}' does not exist.")

    to_remove = [
        role
        for role in member.roles
        if role in rank_roles and role != target
    ]

    to_add = [target] if target and target not in member.roles else []

    if not to_remove and not to_add:
        return None

    message = (
        f"{member} ({member.id}): "
        f"+{[role.name for role in to_add]} "
        f"-{[role.name for role in to_remove]}"
    )

    if DRY_RUN:
        print(f"[DRY RUN] {message}")
        return f"[DRY RUN] {message}"

    if to_remove:
        await member.remove_roles(
            *to_remove,
            reason="Updating Wynncraft rank",
        )

    if to_add:
        await member.add_roles(
            *to_add,
            reason="Updating Wynncraft rank",
        )

    print(message)
    return message


# ---------- Wynncraft API ----------

async def fetch_guild_members() -> dict[str, tuple[str, Role]] | None:
    """Return {uuid: (Minecraft name, rank)}."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API}/guild/{WYNN_GUILD}",
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status != 200:
                    print(f"Wynncraft API returned HTTP {response.status}")
                    return None

                data = await response.json()

    except Exception as error:
        print(f"Wynncraft API error: {error}")
        return None

    members = {}

    for rank_name, players in (data.get("members") or {}).items():
        try:
            rank = Role(rank_name.capitalize())
        except ValueError:
            continue

        if not isinstance(players, dict):
            continue

        for name, info in players.items():
            if isinstance(info, dict) and info.get("uuid"):
                members[info["uuid"].lower()] = (name, rank)

    return members or None


# ---------- Persistent approval buttons ----------

class LinkButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"link:(?P<action>approve|deny):(?P<uid>\d+):(?P<mc>[0-9a-f-]{36})",
):
    def __init__(self, action: str, uid: int, mc: str):
        self.action = action
        self.uid = uid
        self.mc = mc

        super().__init__(
            discord.ui.Button(
                label="Approve" if action == "approve" else "Deny",
                style=(
                    discord.ButtonStyle.success
                    if action == "approve"
                    else discord.ButtonStyle.danger
                ),
                custom_id=f"link:{action}:{uid}:{mc}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(
            match["action"],
            int(match["uid"]),
            match["mc"],
        )

    async def callback(self, interaction: discord.Interaction):
        if (
            not isinstance(interaction.user, discord.Member)
            or not can_manage_roles(interaction.user)
        ):
            await interaction.response.send_message(
                "You must be Recruiter rank or higher to approve link requests.",
                ephemeral=True,
            )
            return

        if interaction.message is None:
            await interaction.response.send_message(
                "Could not find the link request.",
                ephemeral=True,
            )
            return

        original = interaction.message.content

        if self.action == "deny":
            await interaction.response.edit_message(
                content=f"{original}\nDenied by {interaction.user.mention}",
                view=None,
            )
            return

        name = original.split("`")[1] if "`" in original else self.mc

        existing_uuid = db.execute(
            "SELECT discord_id FROM links WHERE uuid = ?",
            (self.mc,),
        ).fetchone()

        if existing_uuid and existing_uuid[0] != self.uid:
            await interaction.response.send_message(
                "That Minecraft account is already linked to someone else.",
                ephemeral=True,
            )
            return

        try:
            db.execute(
                """
                INSERT INTO links (discord_id, uuid, mc_name)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_id) DO UPDATE SET
                    uuid = excluded.uuid,
                    mc_name = excluded.mc_name
                """,
                (self.uid, self.mc, name),
            )
            db.commit()

        except sqlite3.IntegrityError:
            await interaction.response.send_message(
                "That Minecraft account is already linked to someone else.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=f"{original}\nApproved by {interaction.user.mention}",
            view=None,
        )

        member = interaction.guild.get_member(self.uid) if interaction.guild else None
        members = await fetch_guild_members()

        if member and members and self.mc in members:
            try:
                result = await apply_rank(member, members[self.mc][1])
                if result:
                    await interaction.followup.send(result, ephemeral=True)
            except (discord.HTTPException, ValueError) as error:
                print(f"Failed to apply rank: {error}")
                await interaction.followup.send(
                    "The link was approved, but applying the rank failed. "
                    "Check the bot's role permissions and role hierarchy.",
                    ephemeral=True,
                )


# ---------- Bot lifecycle ----------

class GuildBot(commands.Bot):
    async def setup_hook(self):
        self.add_dynamic_items(LinkButton)
        await self.tree.sync()


bot = GuildBot(command_prefix="%", intents=intents)


# ---------- /link ----------

@bot.tree.command(
    name="link",
    description="Link a Discord member to their Minecraft account",
)
@app_commands.describe(
    discord_user="The Discord member to link",
    username="Their Minecraft username",
)
@app_commands.guild_only()
async def link(
    interaction: discord.Interaction,
    discord_user: discord.Member,
    username: str,
):
    if not isinstance(interaction.user, discord.Member):
        return

    if not can_manage_roles(interaction.user):
        await interaction.response.send_message(
            "You need Recruiter rank or higher to use /link.",
            ephemeral=True,
        )
        return

    if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", username):
        await interaction.response.send_message(
            "That doesn't look like a valid Minecraft username.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    members = await fetch_guild_members()

    if members is None:
        await interaction.followup.send(
            "Couldn't reach the Wynncraft API. Try again later.",
            ephemeral=True,
        )
        return

    match = next(
        (
            (uuid, name, rank)
            for uuid, (name, rank) in members.items()
            if name.lower() == username.lower()
        ),
        None,
    )

    if match is None:
        await interaction.followup.send(
            f"`{username}` isn't in the Wynncraft guild `{WYNN_GUILD}`.",
            ephemeral=True,
        )
        return

    uuid, name, rank = match

    taken = db.execute(
        "SELECT discord_id FROM links WHERE uuid = ?",
        (uuid,),
    ).fetchone()

    if taken and taken[0] != discord_user.id:
        await interaction.followup.send(
            "That Minecraft account is already linked to another Discord member.",
            ephemeral=True,
        )
        return

    channel = bot.get_channel(OFFICER_CHANNEL_ID)

    if channel is None or not hasattr(channel, "send"):
        await interaction.followup.send(
            "Couldn't find the officer approval channel. Check OFFICER_CHANNEL_ID.",
            ephemeral=True,
        )
        return

    view = discord.ui.View(timeout=None)
    view.add_item(LinkButton("approve", discord_user.id, uuid))
    view.add_item(LinkButton("deny", discord_user.id, uuid))

    await channel.send(
        f"Link request for {discord_user.mention}: "
        f"`{name}` ({rank.value}), requested by {interaction.user.mention}",
        view=view,
    )

    await interaction.followup.send(
        f"Sent {discord_user.mention}'s link request to officers for approval.",
        ephemeral=True,
    )


# ---------- /editrole ----------

@bot.tree.command(
    name="editrole",
    description="Add or remove a Discord role from a member",
)
@app_commands.describe(
    action="Whether to add or remove the role",
    target_user="The member to edit",
    role="The Discord role to edit",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Remove", value="remove"),
    ]
)
@app_commands.guild_only()
async def editrole(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    target_user: discord.Member,
    role: discord.Role,
):
    if not isinstance(interaction.user, discord.Member):
        return

    if not can_manage_roles(interaction.user):
        await interaction.response.send_message(
            "You need Recruiter rank or higher to manage roles.",
            ephemeral=True,
        )
        return

    if role.is_default() or role.managed:
        await interaction.response.send_message(
            "That role cannot be managed by this command.",
            ephemeral=True,
        )
        return

    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "I can't manage that role because it is at or above my highest role.",
            ephemeral=True,
        )
        return

    is_rank_role = role.name in RANK_ROLES

    if action.value == "add" and is_rank_role:
        rank = RANK_ROLES[role.name]

        try:
            result = await apply_rank(target_user, rank)
        except (discord.HTTPException, ValueError) as error:
            print(f"Role update failed: {error}")
            await interaction.response.send_message(
                "Couldn't update the member's rank role. Check role hierarchy "
                "and bot permissions.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            result or f"{target_user.mention} already has only {role.mention}.",
            ephemeral=True,
        )
        return

    if is_rank_role and action.value == "remove":
        try:
            result = await apply_rank(target_user, None)
        except discord.HTTPException as error:
            print(f"Role removal failed: {error}")
            await interaction.response.send_message(
                "Couldn't remove the member's rank role.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            result or f"{target_user.mention} has no rank role to remove.",
            ephemeral=True,
        )
        return

    # Non-rank roles do not affect the member's Wynncraft rank.
    if DRY_RUN:
        await interaction.response.send_message(
            f"[DRY RUN] Would {action.value} {role.name} "
            f"{'from' if action.value == 'remove' else 'to'} "
            f"{target_user.mention}.",
            ephemeral=True,
        )
        return

    try:
        if action.value == "add":
            await target_user.add_roles(role, reason=f"Requested by {interaction.user}")
        else:
            await target_user.remove_roles(
                role,
                reason=f"Requested by {interaction.user}",
            )
    except discord.HTTPException as error:
        print(f"Role update failed: {error}")
        await interaction.response.send_message(
            "Couldn't update that role. Check role hierarchy and bot permissions.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"{'Added' if action.value == 'add' else 'Removed'} "
        f"{role.mention} {'to' if action.value == 'add' else 'from'} "
        f"{target_user.mention}.",
        ephemeral=True,
    )


# ---------- /unlink ----------

@bot.tree.command(
    name="unlink",
    description="Remove your Minecraft account link",
)
@app_commands.guild_only()
async def unlink(interaction: discord.Interaction):
    db.execute(
        "DELETE FROM links WHERE discord_id = ?",
        (interaction.user.id,),
    )
    db.commit()

    if isinstance(interaction.user, discord.Member):
        try:
            await apply_rank(interaction.user, None)
        except (discord.HTTPException, ValueError) as error:
            print(f"Failed to remove rank during unlink: {error}")
            await interaction.response.send_message(
                "Your link was removed, but your Discord rank role could not be removed.",
                ephemeral=True,
            )
            return

    await interaction.response.send_message(
        "Your account link has been removed.",
        ephemeral=True,
    )


# ---------- /sync ----------

@bot.tree.command(
    name="sync",
    description="Sync linked Discord members with Wynncraft guild ranks",
)
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.checks.cooldown(1, 60.0)
async def sync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    members = await fetch_guild_members()

    if members is None:
        await interaction.followup.send(
            "Couldn't get guild data from Wynncraft. Nothing changed.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    changes = []

    links = db.execute(
        "SELECT discord_id, uuid FROM links"
    ).fetchall()

    for discord_id, uuid in links:
        member = guild.get_member(discord_id)

        if member is None:
            continue

        rank = members[uuid][1] if uuid in members else None

        try:
            result = await apply_rank(member, rank)
        except (discord.HTTPException, ValueError) as error:
            print(f"Role update failed for {discord_id}: {error}")
            continue

        if result:
            changes.append(result)

        await asyncio.sleep(0.5)

    if not changes:
        await interaction.followup.send(
            "Everything already matches.",
            ephemeral=True,
        )
        return

    report = "Role changes:\n" + "\n".join(changes)
    channel = bot.get_channel(OFFICER_CHANNEL_ID)

    if channel is not None and hasattr(channel, "send"):
        await channel.send(report[:1900])

    await interaction.followup.send(
        f"Sync finished: {len(changes)} change(s).",
        ephemeral=True,
    )


@sync.error
async def sync_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.CommandOnCooldown):
        message = f"Try again in {error.retry_after:.0f} seconds."
    elif isinstance(error, app_commands.MissingPermissions):
        message = "You need the Discord Manage Roles permission to use /sync."
    else:
        print(f"/sync error: {error}")
        message = "Sync failed. Check the bot console for details."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# ---------- Start bot ----------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"DRY_RUN is {'ON' if DRY_RUN else 'OFF'}")


bot.run(os.environ["DISCORD_TOKEN"])
