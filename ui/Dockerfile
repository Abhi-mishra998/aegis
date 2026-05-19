FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
# Staff Engineer Fix: Increase retries for robust builds (PE-11)
RUN npm install --fetch-retries=5 --fetch-retry-mintimeout=10000 --fetch-retry-maxtimeout=60000

COPY . .

# Accept build-time gateway URL (empty = nginx proxy, set for external deployments)
ARG VITE_GATEWAY_URL=""
ENV VITE_GATEWAY_URL=$VITE_GATEWAY_URL

RUN npm run build

FROM nginx:stable-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
