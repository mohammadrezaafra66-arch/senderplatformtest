/**
 * PM2 ecosystem (ESM) — local dev: `pm2 start ecosystem.config.js`
 * Docker/production uses ecosystem.config.cjs (CommonJS).
 */
const SHARED_ENV = {
  REDIS_URL: process.env.REDIS_URL || 'redis://127.0.0.1:6379/0',
  SESSIONS_DIR: 'sessions',
  PROXIES_CONFIG: 'config/proxies.json',
  OUTGOING_QUEUE: 'whatsapp_outgoing',
  RESULTS_QUEUE: 'whatsapp_results',
};

export default {
  apps: [
    {
      name: 'wpp-worker-1',
      script: 'worker.js',
      instances: 1,
      exec_mode: 'fork',
      env: { ...SHARED_ENV, WORKER_ID: '1', ACCOUNT_IDS: process.env.WPP1_ACCOUNT_IDS || '' },
    },
    {
      name: 'wpp-worker-2',
      script: 'worker.js',
      instances: 1,
      exec_mode: 'fork',
      env: { ...SHARED_ENV, WORKER_ID: '2', ACCOUNT_IDS: process.env.WPP2_ACCOUNT_IDS || '' },
    },
    {
      name: 'wpp-worker-3',
      script: 'worker.js',
      instances: 1,
      exec_mode: 'fork',
      env: { ...SHARED_ENV, WORKER_ID: '3', ACCOUNT_IDS: process.env.WPP3_ACCOUNT_IDS || '' },
    },
    {
      name: 'wpp-worker-4',
      script: 'worker.js',
      instances: 1,
      exec_mode: 'fork',
      env: { ...SHARED_ENV, WORKER_ID: '4', ACCOUNT_IDS: process.env.WPP4_ACCOUNT_IDS || '' },
    },
    {
      name: 'wpp-worker-5',
      script: 'worker.js',
      instances: 1,
      exec_mode: 'fork',
      env: { ...SHARED_ENV, WORKER_ID: '5', ACCOUNT_IDS: process.env.WPP5_ACCOUNT_IDS || '' },
    },
    {
      name: 'wpp-api',
      script: 'server.js',
      instances: 1,
      exec_mode: 'fork',
      env: {
        ...SHARED_ENV,
        API_PORT: process.env.API_PORT || '3000',
        API_KEY: process.env.API_KEY || '',
        LINK_SESSION_TIMEOUT_MS: process.env.LINK_SESSION_TIMEOUT_MS || '60000',
      },
    },
  ],
};
