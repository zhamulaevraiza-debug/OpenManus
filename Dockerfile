# syntax=docker/dockerfile:1
#
# OpenManus web: one image with the API server, the web UI and every agent tool
# (Python, headless Chromium for the browser tools, Node.js for the chart renderer).
#
#   docker compose up -d --build          # see docs/DEPLOY.md
#
# Behind a TLS-intercepting proxy, pass its CA certificate to the build:
#   docker build --secret id=extra_ca,src=/path/to/ca.crt .

ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---------------------------------------------------------------------------------
# Node.js toolchain (also copied into the runtime image for the chart renderer).
FROM ${NODE_IMAGE} AS node

# ---------------------------------------------------------------------------------
# 1. Web UI: static PWA bundle in /src/web/dist.
FROM node AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=secret,id=extra_ca \
    --mount=type=cache,target=/root/.npm \
    if [ -f /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------------------------------------------------------------------------------
# 2. Chart renderer dependencies (VMind + Puppeteer). Puppeteer drives the Chromium
#    installed by Playwright in the runtime image, so its own download is skipped.
FROM node AS chart
WORKDIR /src/chart
ENV PUPPETEER_SKIP_DOWNLOAD=1
COPY app/tool/chart_visualization/package.json app/tool/chart_visualization/package-lock.json ./
RUN --mount=type=secret,id=extra_ca \
    --mount=type=cache,target=/root/.npm \
    if [ -f /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund

# ---------------------------------------------------------------------------------
# 3. Runtime.
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="OpenManus Web" \
      org.opencontainers.image.description="Multi-user web UI and API for the OpenManus agent team" \
      org.opencontainers.image.source="https://github.com/FoundationAgents/OpenManus" \
      org.opencontainers.image.licenses="MIT"

# ANONYMIZED_TELEMETRY=false: browser-use does not report usage to its developers.
ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PUPPETEER_SKIP_DOWNLOAD=1 \
    PUPPETEER_EXECUTABLE_PATH=/usr/local/bin/chromium \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    ANONYMIZED_TELEMETRY=false

# tini reaps processes orphaned by agent code; git/curl/unzip/procps are handy for the
# coding agents.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini curl git procps unzip \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged account that runs all agent-written code (OPENMANUS_EXEC_USER).
RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid agent --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin agent

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

WORKDIR /app

COPY requirements.txt ./
RUN --mount=type=secret,id=extra_ca \
    --mount=type=cache,target=/root/.cache/pip \
    if [ -f /run/secrets/extra_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/build-ca.pem; \
        export PIP_CERT=/tmp/build-ca.pem; \
    fi; \
    pip install -r requirements.txt \
    && rm -f /tmp/build-ca.pem

# Chromium + its system libraries and fonts (browser agent, crawler, chart renderer).
# The stable /usr/local/bin/chromium link is what Puppeteer launches.
RUN --mount=type=secret,id=extra_ca \
    if [ -f /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s "$(python -c 'from playwright.sync_api import sync_playwright as p; s = p().start(); print(s.chromium.executable_path); s.stop()')" \
        /usr/local/bin/chromium \
    && chromium --version

# Token counting works offline: tiktoken encodings are baked into the image.
COPY deploy/prepare_tiktoken_cache.py /tmp/
RUN python /tmp/prepare_tiktoken_cache.py "$TIKTOKEN_CACHE_DIR" && rm /tmp/prepare_tiktoken_cache.py

COPY app ./app
COPY config ./config
COPY *.py ./
COPY deploy/reset_password.py ./deploy/
COPY --from=chart /src/chart/node_modules ./app/tool/chart_visualization/node_modules
COPY --from=web /src/web/dist ./web/dist
COPY --chmod=0755 deploy/entrypoint.sh /usr/local/bin/openmanus-entrypoint

# Byte-compile the application (the server never writes into /app), and give the CLI
# workspace to the exec user for `docker compose exec openmanus python main.py`.
RUN python -m compileall -q app ./*.py \
    && install -d -o agent -g agent -m 0755 /app/workspace \
    && install -d -o root -g root -m 0711 /data

ENV OPENMANUS_DATA_DIR=/data \
    OPENMANUS_CONFIG_DIR=/data/config \
    OPENMANUS_LOG_DIR=/data/logs \
    OPENMANUS_BROWSER_HEADLESS=1 \
    OPENMANUS_EXEC_USER=agent \
    OPENMANUS_HOST=0.0.0.0 \
    OPENMANUS_PORT=8000

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS --noproxy '*' -o /dev/null "http://127.0.0.1:${OPENMANUS_PORT:-8000}/api/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/openmanus-entrypoint"]
CMD ["python", "web_main.py"]
