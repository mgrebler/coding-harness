FROM ghcr.io/mgrebler/coding-harness-base:latest

# PROJECT-SPECIFIC: Add Playwright (or other E2E) dependencies here if your
# project needs them — system libs + browser binary, pinned to your lockfile
# version so Docker rebuilds this layer when it changes. Example:
#
#   RUN apt-get update && apt-get install -y \
#       libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
#       libatspi2.0-0 libcups2 libdrm2 libgbm1 libxkbcommon0 libxcomposite1 \
#       libxdamage1 libxext6 libxfixes3 libxrandr2 libpango-1.0-0 libcairo2 \
#       libasound2 && \
#       rm -rf /var/lib/apt/lists/*
#
#   COPY pnpm-lock.yaml /tmp/pnpm-lock.yaml
#   RUN PLAYWRIGHT_VERSION=$(grep "'@playwright/test@" /tmp/pnpm-lock.yaml | head -1 | grep -oE "[0-9]+\.[0-9]+\.[0-9]+") && \
#       npm install -g @playwright/test@${PLAYWRIGHT_VERSION} && \
#       su -s /bin/bash node -c "playwright install chromium" && \
#       rm /tmp/pnpm-lock.yaml

# PROJECT-SPECIFIC: Add other project dependencies here.
