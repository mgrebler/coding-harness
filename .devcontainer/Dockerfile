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
    apt-get update && apt-get install -y gh docker-ce-cli docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*

RUN pip3 install claude-agent-sdk uv --break-system-packages

RUN npm install -g pnpm @anthropic-ai/claude-code

RUN mkdir -p /home/node/.local/bin && \
    ln -s /usr/local/bin/claude /home/node/.local/bin/claude && \
    chown -R node:node /home/node/.local

COPY .devcontainer/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]
