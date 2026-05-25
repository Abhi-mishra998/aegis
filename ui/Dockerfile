FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
# npm ci respects package-lock.json exactly — reproducible builds
RUN npm ci --fetch-retries=5 --fetch-retry-mintimeout=10000 --fetch-retry-maxtimeout=60000

COPY . .

# Accept build-time gateway URL (empty = nginx proxy, set for external deployments)
ARG VITE_GATEWAY_URL=""
ENV VITE_GATEWAY_URL=$VITE_GATEWAY_URL

RUN npm run build

FROM nginx:stable-alpine

# Run nginx as non-root user for security
RUN addgroup -g 101 -S nginx 2>/dev/null || true && \
    chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx && \
    touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://localhost/health 2>/dev/null || exit 1

USER nginx

CMD ["nginx", "-g", "daemon off;"]
