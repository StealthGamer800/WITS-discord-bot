// Minimal Minecraft "server" that only exists to prove account ownership.
// A player joins, Mojang authenticates them (online mode), and they are
// immediately kicked with a one-time code they redeem with /link in Discord.
//
// Run directly with `node index.ts` (Node strips the types; `npm run typecheck`
// checks them).

import path from 'node:path'
import crypto from 'node:crypto'
import fs from 'node:fs'
import { DatabaseSync } from 'node:sqlite'
import mc, { type Client } from 'minecraft-protocol'

const PORT = Number(process.env.LINK_SERVER_PORT ?? 25565)
const DB_PATH = process.env.LINK_DB_PATH ?? path.join(import.meta.dirname, '..', 'links.db')
const CODE_TTL_SECONDS = 5 * 60
const IP_COOLDOWN_MS = 5_000

// The bot owns the database and its schema (wits/db.py). This server never
// creates or alters it; it only checks the parts it uses are as expected.
const EXPECTED_SCHEMA: Record<string, Record<string, string>> = {
  pending_codes: { code: 'TEXT', mc_uuid: 'TEXT', mc_name: 'TEXT', expires_at: 'INTEGER' },
  links: { mc_uuid: 'TEXT' }
}
const INSERTED_TABLES = new Set(['pending_codes'])

interface ColumnInfo {
  name: string
  type: string
  notnull: number
  dflt_value: unknown
  pk: number
}

function fail (message: string): never {
  console.error(`Can't start link server: ${message}`)
  process.exit(1)
}

function checkSchema (db: DatabaseSync): string[] {
  const problems: string[] = []
  for (const [table, expected] of Object.entries(EXPECTED_SCHEMA)) {
    const columns = db.prepare(`PRAGMA table_info(${table})`).all() as unknown as ColumnInfo[]
    if (columns.length === 0) {
      problems.push(`table "${table}" is missing`)
      continue
    }
    const actual = new Map(columns.map((c) => [c.name, c]))
    for (const [name, type] of Object.entries(expected)) {
      const column = actual.get(name)
      if (column === undefined) problems.push(`column "${table}.${name}" is missing`)
      else if (column.type.toUpperCase() !== type) {
        problems.push(`column "${table}.${name}" is ${column.type}, expected ${type}`)
      }
    }
    // Inserts only set the expected columns, so any other required column would make them fail
    if (!INSERTED_TABLES.has(table)) continue
    for (const c of columns) {
      if (!(c.name in expected) && c.notnull && c.dflt_value === null && !c.pk) {
        problems.push(`column "${table}.${c.name}" is NOT NULL with no default, but this server doesn't set it`)
      }
    }
  }
  return problems
}

// DatabaseSync would silently create an empty file, so check it exists first
if (!fs.existsSync(DB_PATH)) {
  fail(`no database at ${DB_PATH}. Start the bot first, it creates the database.`)
}
const db = new DatabaseSync(DB_PATH, { timeout: 5000 })
const schemaProblems = checkSchema(db)
if (schemaProblems.length > 0) {
  fail(
    `${DB_PATH} doesn't match the schema this server expects (see wits/db.py):\n` +
    schemaProblems.map((p) => `  - ${p}`).join('\n')
  )
}

const isLinked = db.prepare('SELECT 1 FROM links WHERE mc_uuid = ?')
const purge = db.prepare('DELETE FROM pending_codes WHERE mc_uuid = ? OR expires_at < ?')
const insert = db.prepare('INSERT INTO pending_codes (code, mc_uuid, mc_name, expires_at) VALUES (?, ?, ?, ?)')

// No 0/O/1/I so codes are easy to read off the kick screen. 32 symbols, so
// `byte % 32` is unbiased.
const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
const makeCode = (): string => Array.from(crypto.randomBytes(6), (b) => ALPHABET[b % 32]).join('')

const lastSeenByIp = new Map<string, number>()

// Runs once Mojang has verified the player (client.uuid is trustworthy) but
// before the login success packet. Kicking here uses the login-state
// disconnect packet, which every client version displays correctly; kicking
// from the 'login' event instead breaks on 1.20.2+ clients, which are already
// in the configuration state by then.
function issueCode (client: Client): void {
  // minecraft-protocol keeps writing compress/success packets after this hook
  // returns; drop them so the disconnect is the last thing the client sees.
  const kick = (message: string): void => {
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
  let code: string
  try {
    if (isLinked.get(client.uuid) !== undefined) {
      console.log(`${client.username} (${client.uuid}) is already linked, no code issued`)
      kick(
        '§eThis Minecraft account is already linked to Discord.\n\n' +
        '§7If something is wrong, run §f/unlink §7in the WITS Discord,\n' +
        '§7then join again to get a new code.'
      )
      return
    }

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
server.on('error', (err: Error) => console.error('Server error:', err))
