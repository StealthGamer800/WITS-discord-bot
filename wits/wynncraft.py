"""Wynncraft API client."""

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import aiohttp

from wits.config import WYNN_GUILD_NAME
from wits.ranks import Role


@dataclass
class GuildMember:
    name: str
    uuid: str
    rank: Role
    online: bool
    last_join: datetime | None  # None if the player has never joined / hides it


async def fetch_guild_members() -> dict[str, GuildMember]:
    """Return the guild's members keyed by Minecraft UUID.

    Note the API caches this endpoint for up to 2 minutes."""
    url = f"https://api.wynncraft.com/v3/guild/{quote(WYNN_GUILD_NAME)}"

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
