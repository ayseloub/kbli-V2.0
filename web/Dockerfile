# Frontend Next.js (output standalone, ±150 MB).
# PENTING: NEXT_PUBLIC_BASE_PATH, NEXT_PUBLIC_HOME_URL, dan KBLI_API_URL dibakar saat build.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_BASE_PATH=/chat
ARG NEXT_PUBLIC_HOME_URL=/
ARG KBLI_API_URL=http://api:8000
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH NEXT_PUBLIC_HOME_URL=$NEXT_PUBLIC_HOME_URL \
    KBLI_API_URL=$KBLI_API_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
