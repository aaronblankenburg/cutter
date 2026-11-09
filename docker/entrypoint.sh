#!/bin/sh
# The container runs with the caller's UID:GID. ssh (deploy, feedback) needs a passwd entry for it.
if ! whoami >/dev/null 2>&1; then
  echo "cutter:x:$(id -u):$(id -g):cutter:${HOME:-/home/cutter}:/bin/sh" >> /etc/passwd 2>/dev/null || true
fi
exec cutter "$@"
