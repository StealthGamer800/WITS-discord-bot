import discord
from discord.ext import commands

EXTENSIONS = (
    "wits.cogs.linking",
    "wits.cogs.guild_sync",
)


class WitsBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="%", intents=intents)

    async def setup_hook(self) -> None:
        # Runs once before connecting to Discord (on_ready can fire again on reconnects)
        for extension in EXTENSIONS:
            await self.load_extension(extension)

    async def on_ready(self) -> None:
        await self.tree.sync()
        print(f"We have logged in as {self.user}")
