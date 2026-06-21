import Redis from 'ioredis';
import dotenv from 'dotenv';

dotenv.config();

const redisUrl = process.env.REDIS_URL || 'redis://127.0.0.1:6379/0';

export const redis = new Redis(redisUrl, {
  maxRetriesPerRequest: null,
  enableReadyCheck: true,
});

export function bullmqConnection() {
  return { url: redisUrl };
}

export const RESULTS_LIST = process.env.RESULTS_LIST || 'whatsapp:results';
export const SESSION_STATUS_LIST = process.env.SESSION_STATUS_LIST || 'whatsapp:session_status';
export const RAW_OUTGOING_LIST = process.env.RAW_OUTGOING_LIST || 'whatsapp:raw_outgoing';
export const ALERTS_LIST = process.env.ALERTS_LIST || 'whatsapp:alerts';
export const KILL_SWITCH_KEY = process.env.KILL_SWITCH_KEY || 'whatsapp:kill_switch';
export const LEGACY_KILL_SWITCH_KEY =
  process.env.LEGACY_KILL_SWITCH_KEY || 'system:whatsapp_send_disabled';

export async function isKillSwitchOn() {
  const [primary, legacy] = await redis.mget(KILL_SWITCH_KEY, LEGACY_KILL_SWITCH_KEY);
  const truthy = (v) => v && ['1', 'true', 'yes', 'on'].includes(String(v).trim().toLowerCase());
  return truthy(primary) || truthy(legacy);
}

export async function pushResult(result) {
  await redis.lpush(RESULTS_LIST, JSON.stringify(result));
}

export async function pushSessionStatus(payload) {
  await redis.lpush(SESSION_STATUS_LIST, JSON.stringify(payload));
}

export async function pushAlert(alert) {
  await redis.lpush(ALERTS_LIST, JSON.stringify(alert));
}
