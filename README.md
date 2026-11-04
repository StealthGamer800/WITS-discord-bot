# WITS-discord-bot

## Minecraft account linking

`link-server/` is a tiny online-mode Minecraft server. When a player joins, Mojang
verifies their account and they are immediately kicked with a one-time code. They
run `/link <code>` in Discord to link the two accounts (`/unlink` removes it).

The server and the bot share a SQLite database (`links.db` in the repo root, or
set `LINK_DB_PATH` for both).

```sh
cd link-server
npm install
npm start          # listens on 25565, override with LINK_SERVER_PORT
```

Requires Node 23.6+ (runs `index.ts` directly via type stripping and uses the built-in `node:sqlite`). `npm run typecheck` checks the types. Accepts Minecraft 1.7 – 26.1 clients.
