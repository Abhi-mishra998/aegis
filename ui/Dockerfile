FROM nginx:stable-alpine

# dist/ is pre-built by the deploy pipeline (outside Docker) so BuildKit
# multi-stage cache cannot silently return a stale empty dist/.
COPY dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Fail the image build if the COPY produced no JS assets.
RUN sh -c 'ls /usr/share/nginx/html/assets/*.js' \
    && chmod -R 755 /usr/share/nginx/html \
    && echo "Image OK: $(sh -c 'ls /usr/share/nginx/html/assets/*.js | wc -l') JS file(s)"

EXPOSE 80

# nginx master runs as root (standard), workers drop to nginx user internally.
# Use --spider (HEAD request) on 127.0.0.1 — avoids grep/pipe exit-code
# ambiguity and localhost DNS resolution issues inside the container.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
