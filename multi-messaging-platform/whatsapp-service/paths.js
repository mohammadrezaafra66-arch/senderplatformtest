/**
 * Absolute paths anchored to whatsapp-service root (stable on Windows + PM2).
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const SERVICE_ROOT = path.dirname(fileURLToPath(import.meta.url));

export function resolveServicePath(relativeOrAbsolute, fallbackRelative) {
  const configured = (relativeOrAbsolute || '').trim();
  if (configured) {
    if (path.isAbsolute(configured)) {
      return configured;
    }
    return path.resolve(SERVICE_ROOT, configured);
  }
  return path.join(SERVICE_ROOT, fallbackRelative);
}

export function resolveSessionsDir() {
  return resolveServicePath(process.env.SESSIONS_DIR, 'sessions');
}

export function resolveProxiesConfigPath() {
  return resolveServicePath(process.env.PROXIES_CONFIG, path.join('config', 'proxies.json'));
}
