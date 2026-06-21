/**
 * Admin mini-API — session status + QR linking (isolated from worker.js).
 *
 * Routes:
 *   GET  /api/status/:accountId
 *   POST /api/link-session  { accountId }
 *   POST /api/warmup
 *   GET  /health
 */
import dotenv from 'dotenv';
import fs from 'node:fs';
import express from 'express';
import { Queue } from 'bullmq';
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';
import QRCode from 'qrcode';
import { childLogger } from './logger.js';
import { bullmqConnection } from './redisClient.js';
import { requireHealthyProxy } from './proxyManager.js';
import { getSessionLinkStatus, normalizeAccountId, sessionDirFor } from './sessionStatus.js';
import { scheduleWarmupMatrix, WarmupInsufficientAccountsError } from './warmup.js';
import { OUTGOING_QUEUE } from './queueConfig.js';

dotenv.config();

const log = childLogger({ module: 'wpp-api' });
const app = express();
const PORT = Number(process.env.API_PORT || 3000);
const LINK_TIMEOUT_MS = Number(process.env.LINK_SESSION_TIMEOUT_MS || 60000);

const outgoingQueue = new Queue(OUTGOING_QUEUE, { connection: bullmqConnection() });

/** @type {Map<string, { sock: import('@whiskeysockets/baileys').WASocket, startedAt: number }>} */
const activeLinkSessions = new Map();

app.use(express.json({ limit: '32kb' }));

function optionalApiKey(req, res, next) {
  const required = (process.env.API_KEY || '').trim();
  if (!required) {
    return next();
  }
  const header = req.get('x-api-key') || req.get('authorization')?.replace(/^Bearer\s+/i, '');
  if (header !== required) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  return next();
}

app.use('/api', optionalApiKey);

app.get('/health', (_req, res) => {
  res.json({ ok: true, service: 'whatsapp-service-api' });
});

app.get('/api/status/:accountId', (req, res) => {
  const status = getSessionLinkStatus(req.params.accountId);
  if (!normalizeAccountId(req.params.accountId)) {
    return res.status(400).json({ error: 'invalid_account_id', ...status });
  }
  return res.json({
    accountId: status.accountId,
    linked: status.linked,
  });
});

async function resolveHealthyProxy(accountId) {
  try {
    return await requireHealthyProxy(accountId);
  } catch (err) {
    const wrapped = err;
    if (String(err.message || '').includes('No dedicated proxy')) {
      wrapped.code = 'PROXY_NOT_FOUND';
    } else if (String(err.message || '').includes('unhealthy')) {
      wrapped.code = 'PROXY_UNHEALTHY';
    }
    throw wrapped;
  }
}

function endLinkSocket(accountId, sock, reason) {
  try {
    sock?.end(undefined);
  } catch (err) {
    log.warn({ accountId, err: err.message }, 'link socket end warning');
  }
  activeLinkSessions.delete(accountId);
  log.info({ accountId, reason }, 'link session socket closed');
}

/**
 * Start Baileys socket for QR linking only. Returns base64 QR; closes socket after scan or timeout.
 */
function startQrLinkSession(accountId) {
  return new Promise(async (resolve, reject) => {
    const startedAt = Date.now();
    let settled = false;
    let sock = null;
    let hardTimeout = null;

    const finish = (fn, value) => {
      if (settled) {
        return;
      }
      settled = true;
      if (hardTimeout) {
        clearTimeout(hardTimeout);
      }
      fn(value);
    };

    const scheduleHardClose = (delayMs, reason) => {
      hardTimeout = setTimeout(() => {
        endLinkSocket(accountId, sock, reason);
      }, delayMs);
    };

    try {
      if (activeLinkSessions.has(accountId)) {
        const err = new Error(`Link session already in progress for ${accountId}`);
        err.code = 'LINK_IN_PROGRESS';
        throw err;
      }

      const dir = sessionDirFor(accountId);
      fs.mkdirSync(dir, { recursive: true });

      const { proxy, agent } = await resolveHealthyProxy(accountId);
      log.info({ accountId, proxyId: proxy.id }, 'starting link-session socket');

      const { state, saveCreds } = await useMultiFileAuthState(dir);
      const { version } = await fetchLatestBaileysVersion();

      sock = makeWASocket({
        version,
        auth: state,
        ...(agent ? { agent } : {}),
        printQRInTerminal: false,
        syncFullHistory: false,
        markOnlineOnConnect: false,
        generateHighQualityLinkPreview: false,
        browser: ['Sender Platform', 'Chrome', '120.0.0'],
      });

      activeLinkSessions.set(accountId, { sock, startedAt });

      sock.ev.on('creds.update', saveCreds);

      const qrWaitMs = Math.min(30000, LINK_TIMEOUT_MS);
      const qrTimer = setTimeout(() => {
        endLinkSocket(accountId, sock, 'qr_timeout');
        finish(reject, Object.assign(new Error('QR generation timeout'), { code: 'QR_TIMEOUT' }));
      }, qrWaitMs);

      sock.ev.on('connection.update', async (update) => {
        const { connection, qr, lastDisconnect } = update;

        if (qr && !settled) {
          try {
            const qrCodeBase64 = await QRCode.toDataURL(qr, { margin: 1, width: 320 });
            clearTimeout(qrTimer);
            log.info({ accountId }, 'QR generated for link-session API');
            finish(resolve, { accountId, qrCodeBase64 });

            const elapsed = Date.now() - startedAt;
            const remaining = Math.max(LINK_TIMEOUT_MS - elapsed, 1000);
            scheduleHardClose(remaining, 'post_qr_timeout');
          } catch (err) {
            clearTimeout(qrTimer);
            endLinkSocket(accountId, sock, 'qr_encode_failed');
            finish(reject, err);
          }
        }

        if (connection === 'open') {
          log.info({ accountId }, 'link-session connected — creds saved');
          const elapsed = Date.now() - startedAt;
          const remaining = Math.max(LINK_TIMEOUT_MS - elapsed, 0);
          setTimeout(() => endLinkSocket(accountId, sock, 'link_success'), Math.min(remaining, 3000));
        }

        if (connection === 'close') {
          const statusCode = lastDisconnect?.error?.output?.statusCode;
          if (statusCode === 401 || statusCode === DisconnectReason.loggedOut) {
            endLinkSocket(accountId, sock, 'logged_out');
          }
        }
      });
    } catch (err) {
      endLinkSocket(accountId, sock, 'start_failed');
      finish(reject, err);
    }
  });
}

app.post('/api/link-session', async (req, res) => {
  const accountId = normalizeAccountId(req.body?.accountId);
  if (!accountId) {
    return res.status(400).json({ error: 'invalid_account_id' });
  }

  const existing = getSessionLinkStatus(accountId);
  if (existing.linked) {
    return res.json({
      accountId,
      linked: true,
      qrCodeBase64: null,
      message: 'already_linked',
    });
  }

  try {
    const payload = await startQrLinkSession(accountId);
    return res.json(payload);
  } catch (err) {
    const code = err.code || 'LINK_FAILED';
    log.error({ accountId, code, err: err.message }, 'link-session API failed');
    const status =
      code === 'PROXY_NOT_FOUND' || code === 'PROXY_UNHEALTHY'
        ? 503
        : code === 'LINK_IN_PROGRESS'
          ? 409
          : code === 'QR_TIMEOUT'
            ? 504
            : 500;
    return res.status(status).json({
      error: code,
      message: err.message,
      accountId,
    });
  }
});

app.post('/api/warmup', async (_req, res) => {
  try {
    const result = await scheduleWarmupMatrix(outgoingQueue, { log });

    return res.json({
      success: true,
      message: 'Warmup scheduled',
      pairedAccounts: result.pairedAccounts,
      totalJobs: result.totalJobs,
    });
  } catch (err) {
    if (err instanceof WarmupInsufficientAccountsError) {
      return res.status(400).json({
        success: false,
        error: 'insufficient_accounts',
        message: 'At least 2 linked sessions with creds.json are required',
        pairedAccounts: err.count,
      });
    }

    log.error({ err: err.message }, 'warmup API failed');
    return res.status(500).json({
      success: false,
      error: 'warmup_failed',
      message: err.message,
    });
  }
});

let httpServer = null;
let apiShuttingDown = false;

async function shutdownApi(signal) {
  if (apiShuttingDown) {
    return;
  }
  apiShuttingDown = true;

  log.info({ signal }, 'shutting down admin API');

  for (const [accountId, entry] of activeLinkSessions.entries()) {
    endLinkSocket(accountId, entry.sock, 'shutdown');
  }

  if (httpServer) {
    await new Promise((resolve) => {
      httpServer.close(() => resolve());
    });
  }

  await outgoingQueue.close();

  process.exit(0);
}

process.on('SIGINT', () => {
  void shutdownApi('SIGINT');
});
process.on('SIGTERM', () => {
  void shutdownApi('SIGTERM');
});

httpServer = app.listen(PORT, () => {
  log.info({ port: PORT }, 'whatsapp admin API listening');
});
