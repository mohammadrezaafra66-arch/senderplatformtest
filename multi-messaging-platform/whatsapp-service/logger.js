import pino from 'pino';

const level = process.env.LOG_LEVEL || 'info';

export const logger = pino({
  level,
  base: {
    service: 'whatsapp-service',
    workerId: process.env.WORKER_ID || '0',
  },
  timestamp: pino.stdTimeFunctions.isoTime,
});

export function childLogger(bindings) {
  return logger.child(bindings);
}
