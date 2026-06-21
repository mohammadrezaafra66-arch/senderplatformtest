/**
 * BullMQ queue names — MUST NOT contain ":" (BullMQ restriction).
 * Names are hardcoded; legacy env values are ignored and cleared.
 *
 * Redis list keys (whatsapp:raw_outgoing, whatsapp:results) live in redisClient.js only.
 */
import { childLogger } from './logger.js';

const log = childLogger({ module: 'queueConfig' });

/** @type {readonly string} */
export const OUTGOING_QUEUE = 'whatsapp_outgoing';

/** @type {readonly string} */
export const RESULTS_QUEUE = 'whatsapp_results';

console.log('DEBUG [queueConfig]: OUTGOING_QUEUE hardcoded =', OUTGOING_QUEUE);
console.log('DEBUG [queueConfig]: RESULTS_QUEUE hardcoded =', RESULTS_QUEUE);

if (OUTGOING_QUEUE.includes(':') || RESULTS_QUEUE.includes(':')) {
  throw new Error(
    `queueConfig: BullMQ names must not contain ":" — outgoing="${OUTGOING_QUEUE}" results="${RESULTS_QUEUE}"`,
  );
}

const LEGACY_ENV_KEYS = [
  ['OUTGOING_QUEUE', OUTGOING_QUEUE],
  ['RESULTS_QUEUE', RESULTS_QUEUE],
];

for (const [envKey, forced] of LEGACY_ENV_KEYS) {
  const legacy = (process.env[envKey] || '').trim();
  if (legacy && legacy !== forced) {
    log.warn(
      { envKey, legacy, forced },
      'ignoring legacy BullMQ queue env — colons are forbidden; using hardcoded name',
    );
  }
  process.env[envKey] = forced;
}

export const DEFAULT_OUTGOING_QUEUE = OUTGOING_QUEUE;
export const DEFAULT_RESULTS_QUEUE = RESULTS_QUEUE;

/** @deprecated env override disabled — returns hardcoded safe name */
export function resolveQueueName(_envValue, fallback) {
  return fallback;
}
