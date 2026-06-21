/**
 * Phase 2 E2E test — inject one job into whatsapp_outgoing.
 *
 * Usage:
 *   node test-send.js
 *
 * Env overrides:
 *   TEST_ACCOUNT_ID=989048249523
 *   TEST_JID=989122270261@s.whatsapp.net
 *   TEST_TEXT=پیام تست فاز ۲
 *   TEST_TYPING_SECONDS=3
 *   TEST_DELAY_AFTER=5000
 *   REDIS_URL=redis://127.0.0.1:6379/0
 */
import dotenv from 'dotenv';
import { randomUUID } from 'node:crypto';
import { Queue } from 'bullmq';
import { childLogger } from './logger.js';
import { bullmqConnection } from './redisClient.js';
import { OUTGOING_QUEUE } from './queueConfig.js';

dotenv.config();

const log = childLogger({ module: 'test-send' });

async function main() {
  const accountId = process.env.TEST_ACCOUNT_ID || '989048249523';
  const recipient = process.env.TEST_RECIPIENT || '989122270261';
  const jid = process.env.TEST_JID || `${recipient.replace(/\D/g, '')}@s.whatsapp.net`;
  const text = process.env.TEST_TEXT || 'Baileys phase 2 — live test message';
  const typingSeconds = Number(process.env.TEST_TYPING_SECONDS || 3);
  const delayAfter = Number(process.env.TEST_DELAY_AFTER || 5000);
  const jobId = process.env.TEST_JOB_ID || `test-${randomUUID().slice(0, 8)}`;

  const job = {
    jobId,
    accountId: accountId.replace(/\D/g, ''),
    jid,
    text,
    typingSeconds,
    delayAfter,
    route: process.env.TEST_ROUTE || 'test',
  };

  log.info({ job }, 'enqueueing test job');

  const queue = new Queue(OUTGOING_QUEUE, { connection: bullmqConnection() });
  try {
    await queue.add('send', job, {
      jobId: String(jobId),
      removeOnComplete: 100,
      removeOnFail: 100,
      attempts: 1,
    });
    console.log('\nTest job enqueued successfully.');
    console.log('Job:', JSON.stringify(job, null, 2));
    console.log('\nNext steps:');
    console.log('  1. Ensure kill switch is OFF: whatsapp:kill_switch');
    console.log('  2. Run worker: node worker.js');
    console.log('  3. Watch results: redis-cli LRANGE whatsapp:results 0 0\n');
  } finally {
    await queue.close();
  }
}

main().catch((err) => {
  log.error({ err: err.message }, 'test-send failed');
  process.exit(1);
});
