/**
 * @deprecated Phase 2 — job processing lives in worker.js (activeConnections cache).
 */
import { isKillSwitchOn, pushResult } from './redisClient.js';

export function parseAssignedAccountIds() {
  const raw = process.env.ACCOUNT_IDS || '';
  return raw
    .split(',')
    .map((s) => s.trim().replace(/\D/g, ''))
    .filter(Boolean);
}

export async function processJob(job, assignedAccountIds) {
  const accountKey = String(job.accountId).replace(/\D/g, '');

  if (assignedAccountIds.length > 0 && !assignedAccountIds.includes(accountKey)) {
    return { skipped: true, reason: 'account_not_assigned' };
  }

  if (await isKillSwitchOn()) {
    await pushResult({
      jobId: job.jobId,
      accountId: String(job.accountId),
      jid: job.jid,
      status: 'killed',
      route: job.route || 'campaign',
      message_text: job.text,
      timestamp: new Date().toISOString(),
      error: 'kill_switch_active',
    });
    return { skipped: true, reason: 'kill_switch' };
  }

  return { skipped: true, reason: 'use_worker_js' };
}
