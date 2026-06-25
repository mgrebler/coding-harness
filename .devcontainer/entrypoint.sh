#!/bin/bash
set -e

if [ -S /var/run/docker.sock ]; then
    SOCKET_GID=$(stat -c '%g' /var/run/docker.sock)
    if ! getent group "$SOCKET_GID" > /dev/null 2>&1; then
        groupadd -g "$SOCKET_GID" docker_host
    fi
    usermod -aG "$SOCKET_GID" node
fi

exec gosu node "$@"
