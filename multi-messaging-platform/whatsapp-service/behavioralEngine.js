import { childLogger } from './logger.js';

const log = childLogger({ module: 'behavioralEngine' });

/**
 * Human-like send: composing → typing pause → message → paused.
 *
 * @param {import('@whiskeysockets/baileys').WASocket} sock
 * @param {string} jid
 * @param {string} text
 * @param {number} typingSeconds
 * @returns {Promise<import('@whiskeysockets/baileys').proto.WebMessageInfo>}
 */
export async function sendWithHumanBehavior(sock, jid, text, typingSeconds) {
  const seconds = Number(typingSeconds);
  const typingMs = Math.max(500, Math.round((Number.isFinite(seconds) ? seconds : 3) * 1000));

  log.debug({ jid: jid.slice(-12), typingMs, textLen: String(text).length }, 'human behavior start');

  await sock.sendPresenceUpdate('composing', jid);
  await new Promise((resolve) => setTimeout(resolve, typingMs));

  const result = await sock.sendMessage(jid, { text: String(text) });

  await sock.sendPresenceUpdate('paused', jid);

  log.debug({ jid: jid.slice(-12), messageId: result?.key?.id }, 'human behavior complete');
  return result;
}

export function defaultTypingSeconds(text) {
  const len = String(text || '').length;
  return len / 10 + 2 + Math.random() * 3;
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
