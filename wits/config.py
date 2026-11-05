"""Settings read from the environment (and .env). Missing required values fail at startup."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Shared with link-server/index.ts, which writes pending_codes
DB_PATH = os.environ.get(
    "LINK_DB_PATH", str(Path(__file__).resolve().parent.parent / "links.db")
)

WYNN_GUILD_NAME = os.environ["WYNN_GUILD_NAME"]

# The Discord server whose rank roles are kept in sync
DISCORD_GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])
