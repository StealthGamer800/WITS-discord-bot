"""/link and /unlink: connect a Discord account to a Minecraft account using a
code from the link server (link-server/index.ts)."""

import time

import discord
from discord import app_commands
from discord.ext import commands

from wits.config import WYNN_GUILD_NAME
from wits.db import db
from wits.ranks import Role, set_rank_role


class Linking(commands.Cog):
    @app_commands.command(
        name="link", description="Link your Minecraft account using the code you got"
    )
    @app_commands.guild_only()
    async def link(self, interaction: discord.Interaction, code: str):
        # guild_only() guarantees this at runtime; the check narrows the type for mypy
        if not isinstance(interaction.user, discord.Member):
            return

        # Changing roles can be slow if Discord rate limits us; Discord needs a reply within 3s
        await interaction.response.defer(ephemeral=True)
        code = code.strip().upper().replace("-", "").replace(" ", "")

        # Checked before touching the code, so it isn't used up
        linked = db.execute(
            "SELECT mc_name FROM links WHERE discord_id = ?", (interaction.user.id,)
        ).fetchone()
        if linked is not None:
            await interaction.followup.send(
                f"Your Discord account is already linked to **{linked[0]}**.\n"
                "If something is wrong, run `/unlink`, then join the link server again to get a new code.",
                ephemeral=True,
            )
            return

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

        # Uses the saved roster (refreshed hourly / by /sync) instead of calling the
        # API. If they joined the guild since then, the next refresh sees them as
        # newly joined and gives them the role, since they're linked by then.
        rank_row = db.execute(
            "SELECT rank FROM guild_members WHERE mc_uuid = ?", (mc_uuid,)
        ).fetchone()

        if rank_row is None:
            message += (
                f"\n**{mc_name}** isn't in the {WYNN_GUILD_NAME} guild as of the last sync, "
                "so no rank role was given. If you just joined, you'll get it automatically within the hour."
            )
        else:
            reason = f"/link: {mc_name} is {rank_row[0]} in {WYNN_GUILD_NAME}"
            message += "\n" + await set_rank_role(
                interaction.user, Role(rank_row[0]), reason
            )

        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="unlink", description="Unlink your Minecraft account")
    async def unlink(self, interaction: discord.Interaction):
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Linking())
