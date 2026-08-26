# syntax=docker/dockerfile:1

# ============================================================
# Dependencies
# ============================================================

FROM node:22-alpine AS deps

WORKDIR /app

COPY package.json package-lock.json ./

RUN npm ci


# ============================================================
# Build
# ============================================================

FROM node:22-alpine AS builder

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

# These values are required WHILE Next.js is being built.
ARG MMP_API_PROXY_TARGET=http://core_api:8000
ARG NEXT_PUBLIC_API_URL=/backend

ENV MMP_API_PROXY_TARGET=${MMP_API_PROXY_TARGET}
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build


# ============================================================
# Runtime
# ============================================================

FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3010
ENV HOSTNAME=0.0.0.0

ENV MMP_API_PROXY_TARGET=http://core_api:8000
ENV NEXT_PUBLIC_API_URL=/backend

COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/package-lock.json ./package-lock.json
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public

EXPOSE 3010

CMD ["npm", "run", "start", "--", "-p", "3010"]