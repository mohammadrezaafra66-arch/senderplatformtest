/**
 * Hard reset: stop PM2, flush Redis (removes legacy bull:whatsapp:* keys), restart stack.
 *
 * WARNING: FLUSHALL wipes ALL Redis data (kill switch, warmup lock, Celery queues, etc.)
 *
 * Usage (from whatsapp-service/):
 *   node restart_all.js
 */
import { execSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SERVICE_ROOT = path.dirname(fileURLToPath(import.meta.url));

function run(command, options = {}) {
  console.log(`\n> ${command}`);
  execSync(command, {
    stdio: 'inherit',
    cwd: SERVICE_ROOT,
    shell: true,
    ...options,
  });
}

console.log('=== WhatsApp service hard restart ===');
console.log(`Service root: ${SERVICE_ROOT}`);

try {
  run('pm2 delete all');
} catch {
  console.log('(pm2 delete all — no processes or already stopped)');
}

run('docker exec mmp_redis redis-cli FLUSHALL');
run('pm2 start ecosystem.config.cjs');
run('pm2 status');

console.log('\nDone. BullMQ queues: whatsapp_outgoing, whatsapp_results (no colons).');
