/**
 * Manual CLI enqueue → BullMQ (dev only).
 * Production Python uses RPUSH whatsapp:raw_outgoing — NOT this script.
 */
import dotenv from 'dotenv';
import { Queue } from 'bullmq';
import { bullmqConnection } from './redisClient.js';
import { OUTGOING_QUEUE } from './queueConfig.js';

dotenv.config();

async function main() {
  const raw = process.argv[2];
  if (!raw) {
    console.error('Usage: node enqueueJob.js \'<json>\'');
    process.exit(1);
  }
  const job = JSON.parse(raw);
  if (!job.jobId || !job.accountId || !job.jid || !job.text) {
    console.error('job requires jobId, accountId, jid, text');
    process.exit(1);
  }

  const queue = new Queue(OUTGOING_QUEUE, { connection: bullmqConnection() });
  try {
    await queue.add('send', job, {
      jobId: String(job.jobId),
      removeOnComplete: 1000,
      removeOnFail: 5000,
      attempts: 1,
    });
    console.log(JSON.stringify({ ok: true, jobId: job.jobId }));
  } finally {
    await queue.close();
  }
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, error: err.message }));
  process.exit(1);
});
