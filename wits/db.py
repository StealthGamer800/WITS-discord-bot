"""The SQLite database shared by the bot and the link server."""

import sqlite3

from wits.config import DB_PATH

db = sqlite3.connect(DB_PATH)
db.execute("PRAGMA journal_mode = WAL")
db.execute("PRAGMA busy_timeout = 5000")

# pending_codes and links must match link-server/index.ts
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
    CREATE TABLE IF NOT EXISTS guild_members (
        mc_uuid    TEXT PRIMARY KEY,
        mc_name    TEXT NOT NULL,
        rank       TEXT NOT NULL,
        online     INTEGER NOT NULL,
        last_join  INTEGER,          -- unix seconds, NULL if unknown
        fetched_at INTEGER NOT NULL
    );
""")
