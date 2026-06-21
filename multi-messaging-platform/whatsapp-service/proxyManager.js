import fs from 'node:fs';
import https from 'node:https';
import path from 'node:path';
import { HttpsProxyAgent } from 'https-proxy-agent';
import { SocksProxyAgent } from 'socks-proxy-agent';
import { childLogger } from './logger.js';
import { resolveProxiesConfigPath } from './paths.js';

const log = childLogger({ module: 'proxyManager' });

/** @type {{ proxies: object[], file: string, mtimeMs: number, loadedAt: string } | null} */
let proxyCache = null;

function allowNoProxyFallback() {
  return ['1', 'true', 'yes', 'on'].includes(
    String(process.env.ALLOW_NO_PROXY_FALLBACK || '').trim().toLowerCase(),
  );
}

/**
 * Resolve proxies.json path — anchored to whatsapp-service root.
 */
export { resolveProxiesConfigPath };

/**
 * Inspect proxies.json on disk before resolving a proxy for an account.
 * @returns {{ ok: boolean, file: string, exists: boolean, count: number, hasDefault: boolean, readable: boolean, error?: string }}
 */
export function validateProxiesConfig() {
  const file = resolveProxiesConfigPath();

  if (!fs.existsSync(file)) {
    log.warn({ file }, 'proxies.json not found');
    return {
      ok: false,
      file,
      exists: false,
      count: 0,
      hasDefault: false,
      readable: false,
      error: 'proxies.json not found',
    };
  }

  try {
    const proxies = loadProxies({ force: true });
    const hasDefault = Boolean(findDefaultProxy(proxies));
    return {
      ok: proxies.length > 0 || allowNoProxyFallback(),
      file,
      exists: true,
      count: proxies.length,
      hasDefault,
      readable: true,
    };
  } catch (err) {
    log.error({ file, err: err.message }, 'proxies.json unreadable');
    return {
      ok: false,
      file,
      exists: true,
      count: 0,
      hasDefault: false,
      readable: false,
      error: err.message,
    };
  }
}

function normalizeAccountKey(accountId) {
  return String(accountId || '').replace(/\D/g, '');
}

function isDefaultProxy(proxy) {
  return proxy?.default === true || proxy?.isDefault === true || proxy?.id === 'default';
}

function parseProxyFile(file) {
  const raw = fs.readFileSync(file, 'utf8');
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error('proxies.json must be a JSON array');
  }
  return parsed;
}

/**
 * Load proxies from disk into memory cache (mtime-aware).
 * @param {{ force?: boolean }} [options]
 */
export function loadProxies(options = {}) {
  const file = resolveProxiesConfigPath();

  if (!fs.existsSync(file)) {
    log.warn({ file }, 'proxies config missing — no proxies available');
    proxyCache = {
      proxies: [],
      file,
      mtimeMs: 0,
      loadedAt: new Date().toISOString(),
    };
    return proxyCache.proxies;
  }

  const stat = fs.statSync(file);
  if (!options.force && proxyCache && proxyCache.file === file && proxyCache.mtimeMs === stat.mtimeMs) {
    return proxyCache.proxies;
  }

  try {
    const proxies = parseProxyFile(file);
    proxyCache = {
      proxies,
      file,
      mtimeMs: stat.mtimeMs,
      loadedAt: new Date().toISOString(),
    };
    log.info(
      { file, count: proxies.length, defaultProxy: Boolean(findDefaultProxy(proxies)) },
      'proxy config loaded',
    );
    return proxies;
  } catch (err) {
    log.error({ file, err: err.message }, 'failed to parse proxies config');
    if (proxyCache?.file === file) {
      log.warn({ file }, 'keeping previous proxy cache after parse failure');
      return proxyCache.proxies;
    }
    proxyCache = {
      proxies: [],
      file,
      mtimeMs: stat.mtimeMs,
      loadedAt: new Date().toISOString(),
    };
    return [];
  }
}

export function preloadProxyConfig() {
  return loadProxies({ force: true });
}

export function reloadProxyConfig() {
  log.info({ file: resolveProxiesConfigPath() }, 'reloading proxy config');
  return loadProxies({ force: true });
}

export function getProxyConfigStatus() {
  const file = resolveProxiesConfigPath();
  return {
    file,
    exists: fs.existsSync(file),
    count: proxyCache?.proxies?.length ?? 0,
    loadedAt: proxyCache?.loadedAt ?? null,
    allowNoProxyFallback: allowNoProxyFallback(),
    ...validateProxiesConfig(),
  };
}

export function installProxyConfigReload() {
  const handler = () => {
    reloadProxyConfig();
    log.info('proxy config reloaded (SIGHUP)');
  };
  process.on('SIGHUP', handler);
  return () => process.off('SIGHUP', handler);
}

function findDefaultProxy(proxies) {
  return proxies.find((proxy) => isDefaultProxy(proxy)) || null;
}

function buildProxyUrl(proxy) {
  const auth =
    proxy.username && proxy.password
      ? `${encodeURIComponent(proxy.username)}:${encodeURIComponent(proxy.password)}@`
      : '';
  const type = (proxy.type || 'http').toLowerCase();
  return `${type}://${auth}${proxy.host}:${proxy.port}`;
}

export function buildAgent(proxy) {
  if (!proxy || proxy.type === 'direct') {
    return undefined;
  }
  const url = buildProxyUrl(proxy);
  const type = (proxy.type || 'http').toLowerCase();
  if (type.startsWith('socks')) {
    return new SocksProxyAgent(url);
  }
  return new HttpsProxyAgent(url);
}

export async function checkProxyHealth(proxy, timeoutMs = 8000) {
  if (!proxy || proxy.type === 'direct') {
    return true;
  }
  const agent = buildAgent(proxy);
  return new Promise((resolve) => {
    const req = https.request(
      {
        method: 'HEAD',
        host: 'www.gstatic.com',
        path: '/generate_204',
        agent,
        timeout: timeoutMs,
      },
      (res) => {
        resolve(res.statusCode != null && res.statusCode >= 200 && res.statusCode < 400);
      },
    );
    req.on('error', (err) => {
      log.warn({ err: err.message, proxyId: proxy.id }, 'proxy health check failed');
      resolve(false);
    });
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

/**
 * Resolve proxy for an account with fallback chain:
 * 1) exact accountId  2) single workerId match  3) default proxy entry
 * @returns {{ proxy: object | null, source: string }}
 */
export function resolveProxyForAccount(accountId) {
  validateProxiesConfig();

  const proxies = loadProxies();
  const workerId = Number(process.env.WORKER_ID || 0);
  const accountKey = normalizeAccountKey(accountId);

  if (accountKey) {
    const byAccount = proxies.find(
      (proxy) => normalizeAccountKey(proxy.accountId) === accountKey,
    );
    if (byAccount) {
      return { proxy: byAccount, source: 'account' };
    }
  }

  const byWorker = proxies.filter((proxy) => Number(proxy.workerId) === workerId);
  if (byWorker.length === 1) {
    log.warn(
      { accountId: accountKey, workerId, proxyId: byWorker[0].id },
      'no account-specific proxy — using workerId mapping',
    );
    return { proxy: byWorker[0], source: 'worker' };
  }

  const defaultProxy = findDefaultProxy(proxies);
  if (defaultProxy) {
    log.warn(
      { accountId: accountKey, proxyId: defaultProxy.id },
      'no dedicated proxy — using default proxy entry',
    );
    return { proxy: defaultProxy, source: 'default' };
  }

  if (proxies.length === 0) {
    log.warn({ accountId: accountKey, file: resolveProxiesConfigPath() }, 'proxy list empty');
  } else {
    log.warn(
      { accountId: accountKey, workerId, proxyCount: proxies.length },
      'no proxy mapping found for account',
    );
  }

  return { proxy: null, source: 'none' };
}

/** @deprecated use resolveProxyForAccount */
export function getProxyForAccount(accountId) {
  return resolveProxyForAccount(accountId).proxy;
}

function directConnectionResult(accountId, source = 'direct') {
  log.warn(
    { accountId: normalizeAccountKey(accountId) },
    'ALLOW_NO_PROXY_FALLBACK enabled — connecting without proxy (direct IP)',
  );
  return {
    proxy: { id: 'direct', type: 'direct' },
    agent: undefined,
    source,
    direct: true,
  };
}

async function resolveHealthyProxyCandidate(proxy, source, accountId) {
  if (!proxy) {
    return null;
  }

  const healthy = await checkProxyHealth(proxy);
  if (healthy) {
    return {
      proxy,
      agent: buildAgent(proxy),
      source,
      direct: false,
    };
  }

  log.warn(
    { accountId: normalizeAccountKey(accountId), proxyId: proxy.id, source },
    'resolved proxy unhealthy',
  );
  return null;
}

/**
 * Resolve a healthy proxy agent for Baileys.
 * Chain: account/worker proxy → default proxy → direct (if ALLOW_NO_PROXY_FALLBACK).
 */
export async function requireHealthyProxy(accountId) {
  validateProxiesConfig();

  const { proxy, source } = resolveProxyForAccount(accountId);
  const accountKey = normalizeAccountKey(accountId);

  const primary = await resolveHealthyProxyCandidate(proxy, source, accountKey);
  if (primary) {
    return primary;
  }

  const defaultProxy = findDefaultProxy(loadProxies());
  if (defaultProxy && defaultProxy.id !== proxy?.id) {
    log.warn(
      { accountId: accountKey, from: proxy?.id, to: defaultProxy.id },
      'primary proxy missing/unhealthy — trying default proxy',
    );
    const fallback = await resolveHealthyProxyCandidate(defaultProxy, 'default_fallback', accountKey);
    if (fallback) {
      return fallback;
    }
  }

  if (allowNoProxyFallback()) {
    return directConnectionResult(accountKey);
  }

  if (!proxy) {
    throw new Error(`No dedicated proxy mapped for account ${accountId}`);
  }

  throw new Error(`Proxy unhealthy for account ${accountId} (proxyId=${proxy.id})`);
}

preloadProxyConfig();
installProxyConfigReload();
