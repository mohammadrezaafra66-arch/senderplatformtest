/**
 * Phase 1 onboarding — link one WhatsApp account via QR (CLI).
 *
 * Usage:
 *   node link-session.js 98912xxxxxxx
 *
 * Requires:
 *   - config/proxies.json with a dedicated proxy for this accountId
 *   - WORKER_ID env (optional) for proxy fallback mapping
 */
import dotenv from 'dotenv';
import fs from 'node:fs';
import path from 'node:path';
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';
import { childLogger } from './logger.js';
import { resolveSessionsDir } from './paths.js';
import {
  buildAgent,
  checkProxyHealth,
  getProxyForAccount,
} from './proxyManager.js';

dotenv.config();

const log = childLogger({ module: 'link-session' });
const SESSIONS_DIR = resolveSessionsDir();

function usage() {
  console.error(`
Usage: node link-session.js <accountId>

  accountId   E.164 phone digits (e.g. 989048249523)

Environment:
  SESSIONS_DIR      Session root (default: ./sessions)
  PROXIES_CONFIG    Path to proxies.json
  WORKER_ID         Optional shard id for proxy fallback
`);
}

function normalizeAccountId(raw) {
  const digits = String(raw || '').replace(/\D/g, '');
  if (!digits || digits.length < 10) {
    return null;
  }
  return digits;
}

function sessionDir(accountId) {
  return path.join(SESSIONS_DIR, accountId);
}

async function resolveProxyOrExit(accountId) {
  const proxy = getProxyForAccount(accountId);
  if (!proxy) {
    log.error(
      { accountId },
      'No dedicated proxy mapped — add an entry to config/proxies.json (accountId field)',
    );
    console.error(
      `\nERROR: No proxy configured for account ${accountId}.\n` +
        'Edit config/proxies.json and set "accountId" for this line.\n' +
        'Connection without proxy is blocked (IP leak prevention).\n',
    );
    process.exit(1);
  }

  log.info(
    {
      accountId,
      proxyId: proxy.id,
      type: proxy.type,
      host: proxy.host,
      port: proxy.port,
    },
    'proxy resolved — running health check',
  );

  const healthy = await checkProxyHealth(proxy);
  if (!healthy) {
    log.error({ accountId, proxyId: proxy.id }, 'proxy health check failed');
    console.error(
      `\nERROR: Proxy "${proxy.id}" is unreachable or unhealthy.\n` +
        `Fix proxy settings (${proxy.type}://${proxy.host}:${proxy.port}) before linking.\n`,
    );
    process.exit(1);
  }

  log.info({ accountId, proxyId: proxy.id }, 'proxy health check passed');
  return { proxy, agent: buildAgent(proxy) };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function linkSession(accountId) {
  const { proxy, agent } = await resolveProxyOrExit(accountId);
  const dir = sessionDir(accountId);

  fs.mkdirSync(dir, { recursive: true });
  log.info({ accountId, sessionDir: dir }, 'session directory ready');

  const { state, saveCreds } = await useMultiFileAuthState(dir);
  const { version } = await fetchLatestBaileysVersion();

  let finished = false;

  const finish = async (sock, code, message) => {
    if (finished) {
      return;
    }
    finished = true;
    if (message) {
      if (code === 0) {
        log.info({ accountId }, message);
        console.log(`\n✓ ${message}\n`);
      } else {
        log.error({ accountId }, message);
        console.error(`\n✗ ${message}\n`);
      }
    }
    try {
      sock?.end(undefined);
    } catch (err) {
      log.warn({ err: err.message }, 'socket end warning');
    }
    await sleep(300);
    process.exit(code);
  };

  console.log('\n--- WhatsApp session linking ---');
  console.log(`Account:  ${accountId}`);
  console.log(`Proxy:    ${proxy.id} (${proxy.type}://${proxy.host}:${proxy.port})`);
  console.log(`Session:  ${path.resolve(dir)}`);
  console.log('\nScan the QR code below with WhatsApp → Linked Devices\n');

  const sock = makeWASocket({
    version,
    auth: state,
    agent,
    printQRInTerminal: true,
    syncFullHistory: false,
    markOnlineOnConnect: false,
    generateHighQualityLinkPreview: false,
    browser: ['Sender Platform', 'Chrome', '120.0.0'],
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      log.info({ accountId }, 'QR code generated — waiting for scan');
    }

    if (connection === 'connecting') {
      log.info({ accountId }, 'connecting to WhatsApp…');
    }

    if (connection === 'open') {
      log.info({ accountId, proxyId: proxy.id }, 'connection open — session linked successfully');
      console.log('\nSession linked successfully. Saving credentials…');
      await sleep(3000);
      await finish(
        sock,
        0,
        `Session saved for ${accountId}. You can start the worker (PM2) for this line.`,
      );
    }

    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const reason = lastDisconnect?.error?.message || 'unknown';

      if (statusCode === DisconnectReason.loggedOut || statusCode === 401) {
        await finish(
          sock,
          1,
          `Session rejected (logged out / 401). Remove ${dir} and retry.`,
        );
        return;
      }

      if (statusCode === DisconnectReason.restartRequired) {
        log.info({ accountId }, 'restart required after pairing — reconnect manually if needed');
        return;
      }

      log.warn({ accountId, statusCode, reason }, 'connection closed before open');
      await finish(
        sock,
        1,
        `Connection closed before linking completed (code=${statusCode ?? 'n/a'}). ${reason}`,
      );
    }
  });
}

async function main() {
  const rawArg = process.argv[2];
  if (!rawArg || rawArg === '--help' || rawArg === '-h') {
    usage();
    process.exit(rawArg ? 0 : 1);
  }

  const accountId = normalizeAccountId(rawArg);
  if (!accountId) {
    console.error(`Invalid accountId: "${rawArg}" — use E.164 digits only.`);
    usage();
    process.exit(1);
  }

  log.info({ accountId }, 'starting session link');

  try {
    await linkSession(accountId);
  } catch (err) {
    log.error({ accountId, err: err.message }, 'link-session failed');
    console.error(`\nFATAL: ${err.message}\n`);
    process.exit(1);
  }
}

process.on('SIGINT', () => {
  log.info('interrupted by user');
  process.exit(130);
});

main();
