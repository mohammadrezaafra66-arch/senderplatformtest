import fs from 'node:fs';
import path from 'node:path';
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';
import { childLogger } from './logger.js';
import { pushAlert } from './redisClient.js';
import { requireHealthyProxy } from './proxyManager.js';
import { resolveSessionsDir } from './paths.js';

const log = childLogger({ module: 'sessionManager' });

const SESSIONS_DIR = resolveSessionsDir();
const BACKUP_INTERVAL_MS = 24 * 60 * 60 * 1000;

/** @type {Map<string, { sock: import('@whiskeysockets/baileys').WASocket, proxy: object }>} */
const sockets = new Map();

/** Accounts with invalid session — no auto-reconnect, no further jobs. */
const invalidSessions = new Set();

/** @type {Map<string, NodeJS.Timeout>} */
const backupTimers = new Map();

function sessionDir(accountId) {
  const digits = String(accountId).replace(/\D/g, '');
  return path.join(SESSIONS_DIR, digits);
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function backupCreds(accountId) {
  const dir = sessionDir(accountId);
  const credsFile = path.join(dir, 'creds.json');
  if (!fs.existsSync(credsFile)) {
    return;
  }
  const backupDir = path.join(dir, 'backups');
  ensureDir(backupDir);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const dest = path.join(backupDir, `creds-${stamp}.json`);
  fs.copyFileSync(credsFile, dest);
  log.info({ accountId, dest }, 'session creds backup created');
}

function scheduleBackup(accountId) {
  if (backupTimers.has(accountId)) {
    return;
  }
  const timer = setInterval(() => backupCreds(accountId), BACKUP_INTERVAL_MS);
  backupTimers.set(accountId, timer);
}

async function emitSessionAlert(accountId, reason, detail = {}) {
  const payload = {
    type: 'session_invalid',
    accountId: String(accountId),
    reason,
    timestamp: new Date().toISOString(),
    ...detail,
  };
  await pushAlert(payload);
  log.error({ accountId, reason, ...detail }, 'session alert emitted');
}

export function isSessionInvalid(accountId) {
  return invalidSessions.has(String(accountId).replace(/\D/g, ''));
}

export function markSessionInvalid(accountId, reason, detail = {}) {
  const key = String(accountId).replace(/\D/g, '');
  invalidSessions.add(key);
  return emitSessionAlert(key, reason, detail);
}

export async function closeSocket(accountId, reason = 'manual') {
  const key = String(accountId).replace(/\D/g, '');
  const entry = sockets.get(key);
  if (entry?.sock) {
    try {
      entry.sock.end(undefined);
    } catch (err) {
      log.warn({ err: err.message, accountId: key }, 'socket end error');
    }
  }
  sockets.delete(key);
  log.info({ accountId: key, reason }, 'socket closed');
}

/**
 * Create or return Baileys socket for account.
 * Golden rule: never connect without a healthy dedicated proxy.
 */
export async function getSocket(accountId) {
  const key = String(accountId).replace(/\D/g, '');
  if (invalidSessions.has(key)) {
    throw new Error(`Session invalid for ${key} — manual re-link required`);
  }

  const existing = sockets.get(key);
  if (existing?.sock) {
    return existing.sock;
  }

  const { proxy, agent } = await requireHealthyProxy(key);
  ensureDir(sessionDir(key));

  const { state, saveCreds } = await useMultiFileAuthState(sessionDir(key));
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    agent,
    printQRInTerminal: false,
    syncFullHistory: false,
    markOnlineOnConnect: false,
    generateHighQualityLinkPreview: false,
    browser: ['Sender Platform', 'Chrome', '120.0.0'],
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect } = update;
    if (connection === 'open') {
      log.info({ accountId: key, proxyId: proxy.id }, 'baileys connection open');
      scheduleBackup(key);
    }
    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      const forbidden = statusCode === 401 || statusCode === DisconnectReason.loggedOut;

      log.warn({ accountId: key, statusCode }, 'baileys connection closed');

      await closeSocket(key, `connection_close_${statusCode}`);

      if (forbidden || loggedOut) {
        invalidSessions.add(key);
        await emitSessionAlert(key, 'session_invalid_401', { statusCode });
        return;
      }

      // No auto-reconnect on any close — next job will recreate socket deliberately.
    }
  });

  sockets.set(key, { sock, proxy });
  return sock;
}

export async function shutdownAll() {
  for (const key of [...sockets.keys()]) {
    await closeSocket(key, 'shutdown');
  }
  for (const timer of backupTimers.values()) {
    clearInterval(timer);
  }
  backupTimers.clear();
}
