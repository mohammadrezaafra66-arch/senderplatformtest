/**
 * PM2 ecosystem — 5 worker shards + admin API.
 *
 * Set account shards before start:
 *   WPP1_ACCOUNT_IDS=98901...,98902...
 */
const SHARED_ENV = {
  NODE_ENV: 'production',
  REDIS_URL: process.env.REDIS_URL || 'redis://127.0.0.1:6379/0',
  SESSIONS_DIR: 'sessions',
  PROXIES_CONFIG: 'config/proxies.json',
  OUTGOING_QUEUE: 'whatsapp_outgoing',
  RESULTS_QUEUE: 'whatsapp_results',
  ALLOW_NO_PROXY_FALLBACK: process.env.ALLOW_NO_PROXY_FALLBACK || 'false',
};

function workerEnv(workerId, accountIdsEnvKey) {
  return {
    ...SHARED_ENV,
    WORKER_ID: String(workerId),
    ACCOUNT_IDS: process.env[accountIdsEnvKey] || '',
  };
}

module.exports = {
  apps: [
    {
      name: 'wpp-worker-1',
      script: 'worker.js',
      instances: 1,
      exec_mode: 'fork',
      cwd: __dirname,
      env: workerEnv(1, 'WPP1_ACCOUNT_IDS'),
    },
    {
      name: 'wpp-worker-2',
      script: 'worker.js',
      instances: 1,
      exec_mode: 'fork',
      cwd: __dirname,
      env: workerEnv(2, 'WPP2_ACCOUNT_IDS'),
    },
    {
      name: 'wpp-worker-3',
      script: 'worker.js',
      instances: 1,
      exec_mode: 'fork',
      cwd: __dirname,
      env: workerEnv(3, 'WPP3_ACCOUNT_IDS'),
    },
    {
      name: 'wpp-worker-4',
      script: 'worker.js',
      instances: 1,
      exec_mode: 'fork',
      cwd: __dirname,
      env: workerEnv(4, 'WPP4_ACCOUNT_IDS'),
    },
    {
      name: 'wpp-worker-5',
      script: 'worker.js',
      instances: 1,
      exec_mode: 'fork',
      cwd: __dirname,
      env: workerEnv(5, 'WPP5_ACCOUNT_IDS'),
    },
    {
      name: 'wpp-api',
      script: 'server.js',
      instances: 1,
      exec_mode: 'fork',
      cwd: __dirname,
      env: {
        ...SHARED_ENV,
        API_PORT: process.env.API_PORT || '3000',
        API_KEY: process.env.API_KEY || '',
        LINK_SESSION_TIMEOUT_MS: process.env.LINK_SESSION_TIMEOUT_MS || '60000',
      },
    },
  ],
};
