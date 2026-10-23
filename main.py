import os
from enum import StrEnum

import discord
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
    """The member's rank role. They only have one, so return the first match."""
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
async def edit_role(interaction: discord.Interaction, target_user: discord.Member, input_role: Role):
    discord_role: discord.Role = discord.utils.get(interaction.guild.roles, name=input_role)

    if discord_role is None:
        await interaction.response.send_message(
            f"The {input_role} role doesn't exist in this server.", ephemeral=True
        )
        return

    if not has_at_least(interaction.user, Role.CAPTAIN):
        await interaction.response.send_message("You must be at least Captain.", ephemeral=True)
        return


    # now we need to find target_user's old role
    old_role = get_role(target_user)
    if old_role is not None:
        await target_user.remove_roles(old_role)
    await target_user.add_roles(discord_role)

    await interaction.response.send_message(
        f"Gave {target_user.mention} the {input_role} role.", ephemeral=True
    )

bot.run(os.environ["DISCORD_TOKEN"])
