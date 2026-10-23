import os
import discord

from enum import StrEnum
from typing import Any
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="%", intents=intents)


class Role(StrEnum):
    OWNER = "Owner"
    CHIEF = "Chief"
    STRATEGIST = "Strategist"
    CAPTAIN = "Captain"
    RECRUITER = "Recruiter"
    RECRUIT = "Recruit"

    @property
    def rank(self) -> int:
        # lower index = higher rank
        return list(Role).index(self)


def get_role(member: discord.Member) -> discord.Role | None:
    """Loops through the members roles to find the first role that is in the Enum"""
    for r in member.roles:
        if r.name in Role:
            return r
    return None


def has_at_least(member: discord.Member, minimum: Role) -> bool:
    role = get_role(member)
    return role is not None and Role(role.name).rank <= minimum.rank


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"We have logged in as {bot.user}")


@bot.tree.command(name="editrole", description="Edit a user's role")
@app_commands.guild_only()
async def edit_role(
    interaction: discord.Interaction, target_user: discord.Member, input_role: Role
):
    # guild_only() guarantees this at runtime; the check narrows the types for mypy
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return

    discord_role: discord.Role | Any | None = discord.utils.get(
        interaction.guild.roles, name=input_role
    )

    if discord_role is None:
        await interaction.response.send_message(
            f"The {input_role} role doesn't exist in this server.", ephemeral=True
        )
        return

    if not has_at_least(interaction.user, Role.CAPTAIN):
        await interaction.response.send_message(
            "You must be at least Captain.", ephemeral=True
        )
        return

    # right now you can give someone the same rank as your self i.e. as 
    # a Captain you can give Captain. and we might want to change that
    caller_role: discord.Role | Any | None = get_role(interaction.user)
    if caller_role is None or input_role.rank < Role(caller_role.name).rank:
        await interaction.response.send_message(
            "You cannot give a role higher than yours", ephemeral=True
        )
        return

    # now we need to find target_user's old role
    old_role: discord.Role | None = get_role(target_user)

    if old_role is not None:
        await target_user.remove_roles(old_role)
    await target_user.add_roles(discord_role)

    await interaction.response.send_message(
        f"Gave {target_user.mention} the {input_role} role.", ephemeral=True
    )


bot.run(os.environ["DISCORD_TOKEN"])
