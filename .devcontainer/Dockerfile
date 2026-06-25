FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y \
    git \
    curl \
    ripgrep \
    bash \
    python3 \
    python3-pip \
    python-is-python3 \
    procps \
    gosu \
    build-essential && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list && \
    apt-get update && apt-get install -y gh docker-ce-cli docker-compose-plugin

# Playwright Chromium system dependencies (apt packages — generic, version-independent)
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 && \
    rm -rf /var/lib/apt/lists/*

RUN pip3 install claude-agent-sdk uv --break-system-packages

RUN npm install -g pnpm @anthropic-ai/claude-code

RUN mkdir -p /home/node/.local/bin && \
    ln -s /usr/local/bin/claude /home/node/.local/bin/claude && \
    chown -R node:node /home/node/.local

# PROJECT-SPECIFIC: Add Playwright browser installation here.
# Pin it to the version in your project's lock file so Docker rebuilds
# this layer automatically when the version changes. Example:
#
#   COPY pnpm-lock.yaml /tmp/pnpm-lock.yaml
#   RUN PLAYWRIGHT_VERSION=$(grep "'@playwright/test@" /tmp/pnpm-lock.yaml | head -1 | grep -oE "[0-9]+\.[0-9]+\.[0-9]+") && \
#       npm install -g @playwright/test@${PLAYWRIGHT_VERSION} && \
#       su -s /bin/bash node -c "playwright install chromium" && \
#       rm /tmp/pnpm-lock.yaml

COPY .devcontainer/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]
