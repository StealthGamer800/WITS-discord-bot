import os

from wits.bot import WitsBot

# importing wits loads .env, so DISCORD_TOKEN is available here
WitsBot().run(os.environ["DISCORD_TOKEN"])
