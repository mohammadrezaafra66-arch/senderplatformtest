/**
 * Bridge: Python RPUSH whatsapp:raw_outgoing → BullMQ whatsapp_outgoing
 */
import { childLogger } from './logger.js';
import { redis } from './redisClient.js';

const log = childLogger({ module: 'rawOutgoingBridge' });

const RAW_OUTGOING_LIST = process.env.RAW_OUTGOING_LIST || 'whatsapp:raw_outgoing';
const BLPOP_TIMEOUT_SEC = Number(process.env.RAW_OUTGOING_BLPOP_TIMEOUT || 5);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

let bridgeStopRequested = false;

/**
 * @param {import('bullmq').Queue} outgoingQueue
 * @returns {() => void} stop function for graceful shutdown
 */
export function startRawOutgoingBridge(outgoingQueue) {
  bridgeStopRequested = false;
  log.info({ list: RAW_OUTGOING_LIST, queue: outgoingQueue.name }, 'raw outgoing bridge started');

  (async function bridgeLoop() {
    while (!bridgeStopRequested) {
      try {
        const popped = await redis.blpop(RAW_OUTGOING_LIST, BLPOP_TIMEOUT_SEC);
        if (!popped || bridgeStopRequested) {
          continue;
        }

        const [, raw] = popped;
        let job;
        try {
          job = JSON.parse(raw);
        } catch (err) {
          log.error({ err: err.message, raw: raw?.slice?.(0, 200) }, 'invalid raw outgoing json');
          continue;
        }

        if (!job?.jobId || !job?.accountId || !job?.jid || !job?.text) {
          log.error({ job }, 'raw job missing required fields');
          continue;
        }

        await outgoingQueue.add('send', job, {
          jobId: String(job.jobId),
          removeOnComplete: 1000,
          removeOnFail: 5000,
          attempts: 1,
        });

        log.info(
          { jobId: job.jobId, accountId: job.accountId, route: job.route },
          'bridged raw job to BullMQ',
        );
      } catch (err) {
        if (bridgeStopRequested) {
          break;
        }
        log.error({ err: err.message }, 'raw outgoing bridge loop error');
        await sleep(1000);
      }
    }
    log.info('raw outgoing bridge stopped');
  })().catch((err) => {
    if (!bridgeStopRequested) {
      log.fatal({ err: err.message }, 'raw outgoing bridge crashed');
      process.exit(1);
    }
  });

  return () => {
    bridgeStopRequested = true;
  };
}
