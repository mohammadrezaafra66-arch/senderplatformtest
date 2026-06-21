/**
 * Baileys worker — BullMQ consumer with in-memory connection cache.
 *
 * Env: REDIS_URL, ACCOUNT_IDS (optional shard filter), SESSIONS_DIR, WORKER_ID
 */
import dotenv from 'dotenv';
import fs from 'node:fs';
import path from 'node:path';
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';
import { Worker, Queue } from 'bullmq';
import { childLogger } from './logger.js';
import {
  bullmqConnection,
  redis,
  pushResult,
  pushSessionStatus,
  KILL_SWITCH_KEY,
} from './redisClient.js';
import { requireHealthyProxy } from './proxyManager.js';
import { sendWithHumanBehavior, defaultTypingSeconds, sleep } from './behavioralEngine.js';
import { startRawOutgoingBridge } from './rawOutgoingBridge.js';
import { sendSessionInvalidAlert } from './alertWebhook.js';
import { OUTGOING_QUEUE, RESULTS_QUEUE } from './queueConfig.js';
import { resolveSessionsDir } from './paths.js';

dotenv.config();

const log = childLogger({ module: 'worker' });

const SESSIONS_DIR = resolveSessionsDir();
const CONNECTION_OPEN_TIMEOUT_MS = Number(process.env.CONNECTION_OPEN_TIMEOUT_MS || 90000);
const FAILED_JOB_CLEANUP_LIMIT = Number(process.env.FAILED_JOB_CLEANUP_LIMIT || 10000);

/** @type {Record<string, { sock: import('@whiskeysockets/baileys').WASocket, proxyId: string }>} */
export const activeConnections = {};

/** Accounts blocked after 401 / invalid session — no further processing. */
const blockedAccounts = new Set();

const assignedAccountIds = (process.env.ACCOUNT_IDS || '')
  .split(',')
  .map((s) => s.trim().replace(/\D/g, ''))
  .filter(Boolean);

const outgoingQueueName = OUTGOING_QUEUE;
const resultsQueueName = RESULTS_QUEUE;

console.log('DEBUG: Queue name being used is:', outgoingQueueName);
console.log('DEBUG: Results queue name being used is:', resultsQueueName);
console.log('DEBUG: process.env.OUTGOING_QUEUE =', process.env.OUTGOING_QUEUE);
console.log('DEBUG: process.env.RESULTS_QUEUE =', process.env.RESULTS_QUEUE);

if (outgoingQueueName.includes(':') || resultsQueueName.includes(':')) {
  throw new Error(
    `Invalid BullMQ queue name contains ":" — outgoing="${outgoingQueueName}" results="${resultsQueueName}"`,
  );
}

const outgoingQueue = new Queue(outgoingQueueName, { connection: bullmqConnection() });
const resultsQueue = new Queue(resultsQueueName, { connection: bullmqConnection() });

function normalizeAccountId(raw) {
  return String(raw || '').replace(/\D/g, '');
}

function normalizeJid(jid) {
  if (!jid) {
    throw new Error('jid is required');
  }
  if (jid.includes('@')) {
    return jid;
  }
  const digits = normalizeAccountId(jid);
  return `${digits}@s.whatsapp.net`;
}

function sessionDir(accountId) {
  return path.join(SESSIONS_DIR, normalizeAccountId(accountId));
}

function sessionCredsPath(accountId) {
  return path.join(sessionDir(accountId), 'creds.json');
}

/**
 * Verify linked session files exist before attempting send.
 * @returns {{ valid: boolean, reason?: string }}
 */
function checkSessionCreds(accountId) {
  const key = normalizeAccountId(accountId);
  if (!key) {
    return { valid: false, reason: 'accountId is required' };
  }

  const credsPath = sessionCredsPath(key);
  if (!fs.existsSync(credsPath)) {
    return { valid: false, reason: 'creds.json not found — scan QR to link session' };
  }

  try {
    const raw = fs.readFileSync(credsPath, 'utf8').trim();
    if (!raw) {
      return { valid: false, reason: 'creds.json is empty' };
    }
    const creds = JSON.parse(raw);
    if (!creds || typeof creds !== 'object') {
      return { valid: false, reason: 'creds.json is invalid' };
    }
    return { valid: true };
  } catch (err) {
    return {
      valid: false,
      reason: `creds.json unreadable: ${err?.message || String(err)}`,
    };
  }
}

function evictConnection(accountId, reason) {
  const key = normalizeAccountId(accountId);
  const entry = activeConnections[key];
  if (!entry?.sock) {
    delete activeConnections[key];
    return;
  }

  try {
    entry.sock.end(undefined);
  } catch (err) {
    log.warn({ accountId: key, err: err.message }, 'socket end warning during eviction');
  }

  delete activeConnections[key];
  log.info({ accountId: key, reason }, 'connection evicted from activeConnections cache');
}

async function purgeFailedQueueJobs() {
  const graceMs = 0;
  const queues = [
    { queue: outgoingQueue, label: OUTGOING_QUEUE },
    { queue: resultsQueue, label: RESULTS_QUEUE },
  ];

  for (const { queue, label } of queues) {
    try {
      const removed = await queue.clean(graceMs, FAILED_JOB_CLEANUP_LIMIT, 'failed');
      log.info(
        { queue: label, removed: removed.length },
        'purged failed jobs from BullMQ queue',
      );
    } catch (err) {
      log.warn({ queue: label, err: err.message }, 'failed-job cleanup skipped');
    }
  }
}

async function isKillSwitchOn() {
  const value = await redis.get(KILL_SWITCH_KEY);
  if (!value) {
    return false;
  }
  return ['1', 'true', 'yes', 'on'].includes(String(value).trim().toLowerCase());
}

function buildResult(job, status, extra = {}) {
  return {
    jobId: job.jobId,
    accountId: String(job.accountId),
    jid: job.jid,
    status,
    route: job.route || 'campaign',
    message_text: job.text,
    timestamp: new Date().toISOString(),
    ...extra,
  };
}

async function publishSessionInvalid(accountId, reason, detail = {}) {
  const payload = {
    accountId: String(accountId),
    status: 'session_invalid',
    reason,
    timestamp: new Date().toISOString(),
    ...detail,
  };
  await pushSessionStatus(payload);
  log.warn({ accountId, reason }, 'session status published');
  void sendSessionInvalidAlert(accountId).catch((err) =>
    log.warn({ accountId, err: err.message }, 'session invalid webhook alert failed'),
  );
}

async function publishResult(payload) {
  await pushResult(payload);
  try {
    await resultsQueue.add('result', payload, {
      removeOnComplete: 1000,
      removeOnFail: 5000,
    });
  } catch (err) {
    log.warn({ err: err.message, jobId: payload.jobId }, 'bullmq results queue add failed (redis list ok)');
  }
  log.info(
    {
      jobId: payload.jobId,
      accountId: payload.accountId,
      status: payload.status,
      route: payload.route,
    },
    'result published to whatsapp:results',
  );
}

class SessionInvalidError extends Error {
  constructor(accountId, detail = '') {
    super(`Session invalid for ${accountId}${detail ? `: ${detail}` : ''}`);
    this.name = 'SessionInvalidError';
    this.accountId = accountId;
  }
}

/**
 * Wait until Baileys socket reports connection open (or fail on 401).
 */
function waitForSocketReady(sock, accountId, proxyId) {
  return new Promise((resolve, reject) => {
    let settled = false;

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error(`Connection open timeout after ${CONNECTION_OPEN_TIMEOUT_MS}ms`));
      }
    }, CONNECTION_OPEN_TIMEOUT_MS);

    const onUpdate = (update) => {
      const { connection, lastDisconnect } = update;

      if (connection === 'connecting') {
        log.info({ accountId, proxyId }, 'socket connecting');
      }

      if (connection === 'open' && !settled) {
        settled = true;
        clearTimeout(timer);
        sock.ev.off('connection.update', onUpdate);
        log.info({ accountId, proxyId }, 'socket connection open — cached');
        resolve();
      }

      if (connection === 'close' && !settled) {
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const loggedOut =
          statusCode === DisconnectReason.loggedOut || statusCode === 401;

        if (loggedOut) {
          settled = true;
          clearTimeout(timer);
          sock.ev.off('connection.update', onUpdate);
          blockedAccounts.add(accountId);
          evictConnection(accountId, 'session_invalid_401');
          void publishSessionInvalid(accountId, 'session_invalid_401', {
            statusCode,
          }).catch((err) => log.warn({ err: err.message }, 'session status publish failed'));
          reject(new SessionInvalidError(accountId, `statusCode=${statusCode}`));
        }
      }
    };

    sock.ev.on('connection.update', onUpdate);
  });
}

/**
 * Load or return cached WhatsApp socket for accountId.
 */
async function getCachedConnection(accountId) {
  const key = normalizeAccountId(accountId);
  if (!key) {
    throw new Error('accountId is required');
  }

  if (blockedAccounts.has(key)) {
    throw new SessionInvalidError(key, 'account blocked after prior 401');
  }

  log.info({ accountId: key }, `Attempting connection for account: ${key}`);

  const cached = activeConnections[key];
  if (cached?.sock) {
    log.debug({ accountId: key, proxyId: cached.proxyId }, 'reusing cached socket');
    return cached.sock;
  }

  log.info({ accountId: key }, 'loading new socket — resolving proxy');
  const { proxy, agent, source, direct } = await requireHealthyProxy(key);
  log.info(
    {
      accountId: key,
      proxyId: proxy.id,
      type: proxy.type,
      host: proxy.host,
      port: proxy.port,
      source,
      direct: Boolean(direct),
    },
    direct ? 'no proxy — direct connection allowed by env' : 'proxy healthy — creating Baileys socket',
  );

  const dir = sessionDir(key);
  fs.mkdirSync(dir, { recursive: true });

  const credsPath = sessionCredsPath(key);
  if (!fs.existsSync(credsPath)) {
    log.error({ accountId: key, dir }, 'session creds missing — run link-session.js first');
    throw new SessionInvalidError(key, 'creds.json not found');
  }

  const { state, saveCreds } = await useMultiFileAuthState(dir);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    ...(agent ? { agent } : {}),
    printQRInTerminal: false,
    syncFullHistory: false,
    markOnlineOnConnect: false,
    generateHighQualityLinkPreview: false,
    browser: ['Sender Platform', 'Chrome', '120.0.0'],
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    if (update.connection === 'close') {
      const statusCode = update.lastDisconnect?.error?.output?.statusCode;
      if (statusCode === 401 || statusCode === DisconnectReason.loggedOut) {
        log.error({ accountId: key, statusCode }, 'socket closed 401 — blocking account');
        blockedAccounts.add(key);
        evictConnection(key, 'session_invalid_401');
        void publishSessionInvalid(key, 'session_invalid_401', { statusCode }).catch(
          (err) => log.warn({ err: err.message }, 'session status publish failed'),
        );
      } else {
        evictConnection(key, 'socket_closed');
      }
    }
  });

  await waitForSocketReady(sock, key, proxy.id);
  activeConnections[key] = { sock, proxyId: proxy.id };
  return sock;
}

async function processOutgoingJob(job) {
  const data = job.data || {};
  const accountKey = normalizeAccountId(data.accountId);
  const jobId = data.jobId || job.id;

  log.info({ jobId, accountId: accountKey, route: data.route }, 'job received');

  if (assignedAccountIds.length > 0 && !assignedAccountIds.includes(accountKey)) {
    log.debug({ jobId, accountId: accountKey }, 'not assigned to this worker — deferring');
    await job.moveToDelayed(Date.now() + 2000);
    return { deferred: true };
  }

  const jobPayload = {
    jobId,
    accountId: data.accountId,
    jid: data.jid,
    text: data.text,
    typingSeconds: data.typingSeconds,
    delayAfter: data.delayAfter,
    route: data.route,
  };

  if (await isKillSwitchOn()) {
    log.warn({ jobId, accountId: accountKey }, 'kill switch ON — job killed');
    await publishResult(buildResult(jobPayload, 'killed', { error: 'kill_switch_active' }));
    return { killed: true };
  }

  if (blockedAccounts.has(accountKey)) {
    log.warn({ jobId, accountId: accountKey }, 'account session blocked');
    await publishResult(buildResult(jobPayload, 'session_invalid', { error: 'session_invalid' }));
    return { sessionInvalid: true };
  }

  const sessionCheck = checkSessionCreds(accountKey);
  if (!sessionCheck.valid) {
    log.warn(
      { jobId, accountId: accountKey, reason: sessionCheck.reason },
      'session health check failed before send',
    );
    evictConnection(accountKey, 'session_health_check_failed');
    blockedAccounts.add(accountKey);
    await publishSessionInvalid(accountKey, 'session_missing', {
      error: sessionCheck.reason,
    });
    await publishResult(
      buildResult(jobPayload, 'session_invalid', { error: sessionCheck.reason }),
    );
    return { sessionInvalid: true };
  }

  const jid = normalizeJid(jobPayload.jid);
  const text = String(jobPayload.text || '').trim();
  if (!text) {
    await publishResult(buildResult(jobPayload, 'failed', { error: 'empty_message_text' }));
    return { failed: true };
  }

  const typingSeconds =
    jobPayload.typingSeconds != null
      ? Number(jobPayload.typingSeconds)
      : defaultTypingSeconds(text);
  const delayAfterMs = Math.max(0, Number(jobPayload.delayAfter ?? 5000));

  try {
    const sock = await getCachedConnection(accountKey);
    const sent = await sendWithHumanBehavior(sock, jid, text, typingSeconds);

    await publishResult(
      buildResult(jobPayload, 'delivered', {
        platform_message_id: sent?.key?.id || null,
        error: null,
      }),
    );

    log.info({ jobId, accountId: accountKey, jid, delayAfterMs }, 'jitter sleep before next job');
    await sleep(delayAfterMs);

    return { delivered: true };
  } catch (err) {
    if (err instanceof SessionInvalidError) {
      blockedAccounts.add(accountKey);
      evictConnection(accountKey, 'session_invalid');
      await publishSessionInvalid(accountKey, 'session_invalid', { error: err.message });
      await publishResult(
        buildResult(jobPayload, 'session_invalid', { error: err.message }),
      );
      return { sessionInvalid: true };
    }

    const message = err?.message || String(err);
    log.error({ jobId, accountId: accountKey, err: message }, 'job processing failed');

    if (/proxy|unhealthy|ECONNREFUSED|ETIMEDOUT|connection|socket/i.test(message)) {
      evictConnection(accountKey, 'transport_error');
      log.warn({ accountId: accountKey }, 'proxy/socket error — evicted from connection cache');
    }

    await publishResult(buildResult(jobPayload, 'failed', { error: message }));
    return { failed: true };
  }
}

log.info(
  {
    workerId: process.env.WORKER_ID,
    outgoingQueue: OUTGOING_QUEUE,
    resultsQueue: RESULTS_QUEUE,
    sessionsDir: SESSIONS_DIR,
    assignedAccountIds: assignedAccountIds.length ? assignedAccountIds : 'all',
  },
  'whatsapp worker starting',
);

let bullWorker = null;
let shuttingDown = false;
const stopRawBridge = startRawOutgoingBridge(outgoingQueue);

async function bootstrapWorker() {
  await purgeFailedQueueJobs();

  console.log('DEBUG: Worker queue name being used is:', outgoingQueueName);

  bullWorker = new Worker(outgoingQueueName, processOutgoingJob, {
    connection: bullmqConnection(),
    concurrency: 1,
    lockDuration: 300000,
    maxStalledCount: 1,
    stalledInterval: 30000,
  });

  bullWorker.on('completed', (job, result) => {
    log.debug({ bullJobId: job.id, result }, 'bull job completed');
  });

  bullWorker.on('failed', (job, err) => {
    log.error({ bullJobId: job?.id, err: err.message }, 'bull job failed');
  });

  bullWorker.on('stalled', (jobId, previousState) => {
    log.warn(
      { jobId, previousState },
      'job stalled — BullMQ re-queuing automatically (max 1 recovery)',
    );
  });

  log.info('listening on BullMQ queue whatsapp_outgoing (+ raw bridge whatsapp:raw_outgoing)');
}

bootstrapWorker().catch((err) => {
  log.fatal({ err: err.message }, 'worker bootstrap failed');
  process.exit(1);
});

async function shutdown(signal) {
  if (shuttingDown) {
    return;
  }
  shuttingDown = true;

  log.info({ signal }, 'shutting down worker');

  stopRawBridge();

  if (bullWorker) {
    await bullWorker.close();
  }
  await outgoingQueue.close();
  await resultsQueue.close();

  for (const [accountId, entry] of Object.entries(activeConnections)) {
    try {
      entry.sock?.end(undefined);
      log.info({ accountId }, 'cached socket closed');
    } catch (err) {
      log.warn({ accountId, err: err.message }, 'socket close warning');
    }
  }

  await redis.quit();
  process.exit(0);
}

process.on('SIGINT', () => {
  void shutdown('SIGINT');
});
process.on('SIGTERM', () => {
  void shutdown('SIGTERM');
});
