/**
 * Cross-Warmup Matrix — schedule organic two-way chats between linked lines.
 *
 * Discovers accounts from sessions/{accountId}/creds.json (no env account lists).
 * Enqueues delayed jobs directly to BullMQ whatsapp_outgoing.
 *
 * Usage: node warmup.js
 * API:   POST /api/warmup (server.js)
 */
import dotenv from 'dotenv';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { Queue } from 'bullmq';
import { childLogger } from './logger.js';
import { bullmqConnection, redis } from './redisClient.js';
import { OUTGOING_QUEUE } from './queueConfig.js';
import { resolveSessionsDir } from './paths.js';

dotenv.config();

const log = childLogger({ module: 'warmup' });

const SESSIONS_DIR = resolveSessionsDir();

/** Spread matrix between 10 min and 180 min from now. */
const WINDOW_MIN_MS = 10 * 60 * 1000;
const WINDOW_MAX_MS = 180 * 60 * 1000;

const WARMUP_PHRASES = [
  'سلام وقت بخیر',
  'پیام من رو دریافت کردید؟',
  'بله رسید ممنون',
  'تست شبکه ارتباطی',
  'صدای من رو دارید؟',
  'ارتباط برقراره',
  'خوبید؟',
  'یه تست کوتاه بود',
  'ممنون از پاسخ‌تون',
  'همه چیز اوکیه',
  'دریافت شد',
  'الان می‌بینمتون',
];

export class WarmupInsufficientAccountsError extends Error {
  constructor(count) {
    super(`At least 2 linked sessions required (found ${count})`);
    this.name = 'WarmupInsufficientAccountsError';
    this.code = 'INSUFFICIENT_ACCOUNTS';
    this.count = count;
  }
}

/**
 * Scan sessions/ for folders containing creds.json.
 * @returns {string[]} E.164 accountId digits
 */
export function discoverLinkedAccounts() {
  if (!fs.existsSync(SESSIONS_DIR)) {
    return [];
  }

  const accountIds = [];

  for (const entry of fs.readdirSync(SESSIONS_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory()) {
      continue;
    }
    const credsPath = path.join(SESSIONS_DIR, entry.name, 'creds.json');
    if (!fs.existsSync(credsPath)) {
      continue;
    }
    const digits = String(entry.name).replace(/\D/g, '');
    if (digits.length >= 10) {
      accountIds.push(digits);
    }
  }

  return [...new Set(accountIds)].sort();
}

function shuffle(items) {
  const arr = [...items];
  for (let i = arr.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function pickPhrase(index) {
  return WARMUP_PHRASES[index % WARMUP_PHRASES.length];
}

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

function toJid(accountId) {
  return `${String(accountId).replace(/\D/g, '')}@s.whatsapp.net`;
}

/**
 * Build pair/trio groups from shuffled account list.
 * Even count: all pairs.
 * Odd count: pair the middle slice; last person + first person + one leftover form a trio.
 */
export function buildWarmupGroups(accountIds) {
  const shuffled = shuffle(accountIds);

  if (shuffled.length % 2 === 0) {
    const groups = [];
    for (let i = 0; i < shuffled.length; i += 2) {
      groups.push({ type: 'pair', members: [shuffled[i], shuffled[i + 1]] });
    }
    return groups;
  }

  const first = shuffled[0];
  const last = shuffled[shuffled.length - 1];
  const middle = shuffled.slice(1, -1);
  const groups = [];
  const mid = [...middle];

  while (mid.length > 1) {
    groups.push({ type: 'pair', members: mid.splice(0, 2) });
  }

  const trioThird = mid[0];
  groups.push({ type: 'trio', members: [first, last, trioThird] });

  return groups;
}

function pairConversationSteps(a, b) {
  return [
    { from: a, to: b, text: pickPhrase(0), step: 1 },
    { from: b, to: a, text: pickPhrase(2), step: 2 },
    { from: a, to: b, text: pickPhrase(4), step: 3 },
    { from: b, to: a, text: pickPhrase(5), step: 4 },
  ];
}

function trioConversationSteps(a, b, c) {
  return [
    { from: a, to: b, text: pickPhrase(0), step: 1 },
    { from: b, to: c, text: pickPhrase(1), step: 2 },
    { from: c, to: a, text: pickPhrase(2), step: 3 },
    { from: a, to: b, text: pickPhrase(3), step: 4 },
  ];
}

function buildJobPayload(from, to, text, step, groupKey) {
  return {
    jobId: `warmup-${groupKey}-s${step}-${randomUUID().slice(0, 8)}`,
    accountId: from,
    jid: toJid(to),
    text,
    typingSeconds: Number((2 + Math.random() * 4).toFixed(1)),
    delayAfter: 0,
    route: 'warmup',
  };
}

function buildStepDelays(groupStartMs, groupSpanMs) {
  const gap = groupSpanMs / 5;
  return [
    Math.round(groupStartMs + gap * 0.5),
    Math.round(groupStartMs + gap * 1.5),
    Math.round(groupStartMs + gap * 2.5),
    Math.round(groupStartMs + gap * 3.5),
  ];
}

async function scheduleGroup(queue, group, groupIndex, windowMs, scheduleLog) {
  const [a, b, c] = group.members;
  const groupKey =
    group.type === 'pair' ? `${a}-${b}` : `${a}-${b}-${c}`;

  const groupSpan = windowMs / Math.max(1, groupIndex + 2);
  const groupStart = randomBetween(0, Math.max(windowMs - groupSpan, 0));
  const delays = buildStepDelays(groupStart, groupSpan);

  const steps =
    group.type === 'pair'
      ? pairConversationSteps(a, b)
      : trioConversationSteps(a, b, c);

  for (let i = 0; i < steps.length; i += 1) {
    const step = steps[i];
    const job = buildJobPayload(step.from, step.to, step.text, step.step, groupKey);

    await queue.add('warmup', job, {
      jobId: job.jobId,
      delay: delays[i],
      attempts: 1,
      removeOnComplete: 500,
      removeOnFail: 1000,
    });

    const delayMinutes = Math.round(delays[i] / 60000);
    scheduleLog.info(
      {
        from: step.from,
        to: step.to,
        step: step.step,
        delayMinutes,
        groupType: group.type,
        text: step.text,
      },
      `برنامه‌ریزی مکالمه warmup بین خط ${step.from} و ${step.to} برای ${delayMinutes} دقیقه دیگر انجام شد`,
    );
  }

  const startMinutes = Math.round(groupStart / 60000);
  const endMinutes = Math.round((groupStart + groupSpan) / 60000);
  scheduleLog.info(
    { groupKey, groupType: group.type, members: group.members, startMinutes, endMinutes },
    'warmup group scheduled',
  );
}

/**
 * Discover linked accounts, pair them, and enqueue delayed warmup jobs.
 * Does not close the queue or Redis — safe for long-running servers.
 *
 * @param {import('bullmq').Queue} queue
 * @param {{ log?: import('pino').Logger }} [options]
 * @returns {Promise<{ pairedAccounts: number, totalJobs: number, groups: number, windowMinutes: number, accountIds: string[] }>}
 */
export async function scheduleWarmupMatrix(queue, options = {}) {
  const scheduleLog = options.log || log;
  const accountIds = discoverLinkedAccounts();

  scheduleLog.info({ count: accountIds.length, accountIds }, 'discovered linked sessions');

  if (accountIds.length < 2) {
    throw new WarmupInsufficientAccountsError(accountIds.length);
  }

  const groups = buildWarmupGroups(accountIds);
  const windowMs = Math.round(randomBetween(WINDOW_MIN_MS, WINDOW_MAX_MS));
  const windowMinutes = Math.round(windowMs / 60000);

  scheduleLog.info(
    { groups: groups.length, windowMinutes, windowMs },
    'warmup matrix window selected',
  );

  let totalJobs = 0;
  for (let i = 0; i < groups.length; i += 1) {
    await scheduleGroup(queue, groups[i], i, windowMs, scheduleLog);
    totalJobs += 4;
  }

  scheduleLog.info(
    { totalJobs, groups: groups.length, windowMinutes },
    'warmup matrix scheduling complete',
  );

  return {
    pairedAccounts: accountIds.length,
    totalJobs,
    groups: groups.length,
    windowMinutes,
    accountIds,
  };
}

async function main() {
  const queue = new Queue(OUTGOING_QUEUE, { connection: bullmqConnection() });

  try {
    await scheduleWarmupMatrix(queue);
  } finally {
    await queue.close();
    await redis.quit();
  }

  process.exit(0);
}

const isDirectRun =
  process.argv[1] &&
  import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;

if (isDirectRun) {
  main().catch((err) => {
    if (err instanceof WarmupInsufficientAccountsError) {
      log.error({ count: err.count }, 'حداقل ۲ خط با creds.json در sessions/ لازم است — warmup متوقف شد');
      process.exit(1);
    }
    log.error({ err: err.message }, 'warmup failed');
    process.exit(1);
  });
}
