import fs from 'node:fs';
import path from 'node:path';
import { resolveSessionsDir } from './paths.js';

const SESSIONS_DIR = resolveSessionsDir();

export function normalizeAccountId(raw) {
  const digits = String(raw || '').replace(/\D/g, '');
  if (!digits || digits.length < 10) {
    return null;
  }
  return digits;
}

export function sessionDirFor(accountId) {
  const key = normalizeAccountId(accountId);
  if (!key) {
    return null;
  }
  return path.join(SESSIONS_DIR, key);
}

/**
 * @returns {{ accountId: string, linked: boolean, sessionPath: string | null, reason?: string }}
 */
export function getSessionLinkStatus(accountId) {
  const key = normalizeAccountId(accountId);
  if (!key) {
    return { accountId: String(accountId || ''), linked: false, sessionPath: null, reason: 'invalid_account_id' };
  }

  const dir = sessionDirFor(key);
  const credsPath = path.join(dir, 'creds.json');

  if (!fs.existsSync(dir)) {
    return { accountId: key, linked: false, sessionPath: dir, reason: 'session_dir_missing' };
  }

  if (!fs.existsSync(credsPath)) {
    return { accountId: key, linked: false, sessionPath: dir, reason: 'creds_missing' };
  }

  try {
    const raw = fs.readFileSync(credsPath, 'utf8');
    const creds = JSON.parse(raw);
    const linked = Boolean(creds?.me?.id || creds?.registered);
    return {
      accountId: key,
      linked,
      sessionPath: dir,
      reason: linked ? undefined : 'creds_not_registered',
    };
  } catch {
    return { accountId: key, linked: false, sessionPath: dir, reason: 'creds_invalid' };
  }
}
