"""Guild ranks and the Discord roles that represent them."""

from enum import StrEnum

import discord


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
        # higher index = higher rank
        return list(Role).index(self)


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
