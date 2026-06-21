FROM node:20-alpine

WORKDIR /app

RUN apk add --no-cache tini \
  && npm install -g pm2@5

COPY package.json package-lock.json* ./

RUN npm install --omit=dev

COPY . .

RUN mkdir -p sessions config

ENV NODE_ENV=production \
    API_PORT=3000 \
    SESSIONS_DIR=/app/sessions \
    PROXIES_CONFIG=/app/config/proxies.json \
    REDIS_URL=redis://redis:6379/0

EXPOSE 3000

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["pm2-runtime", "start", "ecosystem.config.cjs"]
