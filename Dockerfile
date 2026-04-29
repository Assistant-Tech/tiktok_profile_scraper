FROM node:20-slim AS builder

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY tsconfig.json ./
COPY src ./src
RUN npm run build


FROM node:20-slim AS runtime

ENV NODE_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHON_BIN=/opt/venv/bin/python \
    PYTHON_SCRAPER_PATH=/app/python/scraper.py \
    EXECUTABLE_PATH=/usr/bin/chromium

# Chromium + chromedriver + python + minimal headless deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        python3 \
        python3-venv \
        python3-pip \
        ca-certificates \
        fonts-liberation \
        fonts-noto-color-emoji \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        dumb-init \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python venv with selenium
COPY python/requirements.txt ./python/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r python/requirements.txt

# Node prod deps
COPY package.json package-lock.json* ./
RUN npm install --omit=dev --no-audit --no-fund && npm cache clean --force

COPY --from=builder /app/dist ./dist
COPY python ./python

# Run as non-root
RUN groupadd -r app && useradd -r -g app -G audio,video app \
    && mkdir -p /home/app/Downloads \
    && chown -R app:app /home/app /app /opt/venv
USER app

EXPOSE 8080

ENTRYPOINT ["dumb-init", "--"]
CMD ["node", "dist/server.js"]
