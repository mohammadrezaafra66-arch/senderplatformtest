/**
 * Fire-and-forget HTTP alerts for support team (Telegram / generic webhook).
 */
import { childLogger } from './logger.js';

const log = childLogger({ module: 'alertWebhook' });

function resolveWebhookUrl() {
  return (
    (process.env.TELEGRAM_WEBHOOK_URL || '').trim() ||
    (process.env.ADMIN_ALERT_WEBHOOK || '').trim()
  );
}

/**
 * Notify support when a WhatsApp session becomes invalid (401 / logged out).
 * @param {string} accountId
 */
export async function sendSessionInvalidAlert(accountId) {
  const url = resolveWebhookUrl();
  if (!url) {
    log.debug({ accountId }, 'no alert webhook configured — skipping HTTP alert');
    return;
  }

  const text = `خطا: سشن واتس‌اپ برای شماره ${accountId} باطل شده است. لطفاً برای اسکن مجدد QR کد اقدام کنید.`;

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        message: text,
        accountId: String(accountId),
        type: 'session_invalid',
        timestamp: new Date().toISOString(),
      }),
      signal: AbortSignal.timeout(10000),
    });

    if (!response.ok) {
      log.warn(
        { accountId, status: response.status },
        'alert webhook returned non-OK status',
      );
      return;
    }

    log.info({ accountId }, 'session invalid alert sent to webhook');
  } catch (err) {
    log.warn({ accountId, err: err.message }, 'alert webhook request failed');
  }
}
