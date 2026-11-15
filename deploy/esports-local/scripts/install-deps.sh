#!/bin/sh
set -eu
cd /workspace/fronted
fingerprint="$(sha256sum package-lock.json | cut -d ' ' -f 1):$(node --version):$(uname -m)"
marker=node_modules/.esports-local-dependencies
if [ -f "$marker" ] && [ "$(cat "$marker")" = "$fingerprint" ] && [ -x node_modules/.bin/vite ]; then
  printf 'Linux workspace dependencies are current.\n'
  exit 0
fi
if [ -d /npm-cache-readonly/_cacache ] && [ ! -f /root/.npm/.esports-local-host-cache-seeded ]; then
  printf 'Seeding the Linux npm cache from the read-only Windows cache...\n'
  mkdir -p /root/.npm/_cacache
  cp -rn /npm-cache-readonly/_cacache/. /root/.npm/_cacache/
  touch /root/.npm/.esports-local-host-cache-seeded
  printf 'Cache seed completed; installing locked Linux dependencies.\n'
fi
npm ci --prefer-offline --no-audit --no-fund --loglevel=http --fetch-retries=2 --fetch-timeout=60000
printf '%s' "$fingerprint" > "$marker"
