'use strict'

// Minimal Minecraft "server" that only exists to prove account ownership.
// A player joins, Mojang authenticates them (online mode), and they are
// immediately kicked with a one-time code they redeem with /link in Discord.

const path = require('node:path')
const crypto = require('node:crypto')
const { DatabaseSync } = require('node:sqlite')
const mc = require('minecraft-protocol')

const PORT = Number(process.env.LINK_SERVER_PORT ?? 25565)
const DB_PATH = process.env.LINK_DB_PATH ?? path.join(__dirname, '..', 'links.db')
const CODE_TTL_SECONDS = 5 * 60
const IP_COOLDOWN_MS = 5_000

// Must match the schema in main.py
const db = new DatabaseSync(DB_PATH)
db.exec('PRAGMA journal_mode = WAL')
db.exec('PRAGMA busy_timeout = 5000')
db.exec(`CREATE TABLE IF NOT EXISTS pending_codes (
  code       TEXT PRIMARY KEY,
  mc_uuid    TEXT NOT NULL,
  mc_name    TEXT NOT NULL,
  expires_at INTEGER NOT NULL
)`)

const purge = db.prepare('DELETE FROM pending_codes WHERE mc_uuid = ? OR expires_at < ?')
const insert = db.prepare('INSERT INTO pending_codes (code, mc_uuid, mc_name, expires_at) VALUES (?, ?, ?, ?)')

// No 0/O/1/I so codes are easy to read off the kick screen. 32 symbols, so
// `byte % 32` is unbiased.
const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
const makeCode = () => Array.from(crypto.randomBytes(6), (b) => ALPHABET[b % 32]).join('')

const lastSeenByIp = new Map()

// Runs once Mojang has verified the player (client.uuid is trustworthy) but
// before the login success packet. Kicking here uses the login-state
// disconnect packet, which every client version displays correctly; kicking
// from the 'login' event instead breaks on 1.20.2+ clients, which are already
// in the configuration state by then.
function issueCode (client) {
  // minecraft-protocol keeps writing compress/success packets after this hook
  // returns; drop them so the disconnect is the last thing the client sees.
  const kick = (message) => {
    client.end(message)
    client.write = () => {}
  }

  const ip = client.socket.remoteAddress ?? 'unknown'
  const now = Date.now()
  if (now - (lastSeenByIp.get(ip) ?? 0) < IP_COOLDOWN_MS) {
    kick('§cToo many attempts, wait a few seconds and try again.')
    return
  }
  lastSeenByIp.set(ip, now)

  const nowSeconds = Math.floor(now / 1000)
  let code
  try {
    // One live code per Minecraft account; also sweeps expired codes.
    purge.run(client.uuid, nowSeconds)
    code = makeCode()
    insert.run(code, client.uuid, client.username, nowSeconds + CODE_TTL_SECONDS)
  } catch (err) {
    console.error('Failed to issue code:', err)
    kick('§cSomething went wrong, please try again later.')
    return
  }

  console.log(`Issued code to ${client.username} (${client.uuid})`)
  kick(
    `§aYour Discord link code:\n\n§e§l${code}\n\n` +
    `§7Run §f/link ${code} §7in the WITS Discord.\n` +
    `§7This code expires in ${CODE_TTL_SECONDS / 60} minutes.`
  )
}

const server = mc.createServer({
  'online-mode': true, // REQUIRED: this is what makes client.uuid trustworthy
  port: PORT,
  version: false, // accept any client version minecraft-protocol supports
  motd: '§bWITS Discord link\n§7Join to get your link code',
  maxPlayers: 0,
  hideErrors: true,
  beforeLogin: issueCode
})

// Forget old IP entries so the map doesn't grow forever.
setInterval(() => {
  const cutoff = Date.now() - IP_COOLDOWN_MS
  for (const [ip, t] of lastSeenByIp) if (t < cutoff) lastSeenByIp.delete(ip)
}, 60_000).unref()

server.on('listening', () => console.log(`Link server listening on port ${PORT}, db: ${DB_PATH}`))
server.on('error', (err) => console.error('Server error:', err))
