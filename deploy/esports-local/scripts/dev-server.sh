#!/bin/sh
set -eu
case "${1:-}" in boss|provider|admin) workspace="$1" ;; *) printf 'Expected boss, provider or admin.\n' >&2; exit 2 ;; esac
port="${2:?Missing local Vite port}"
cd /workspace/fronted
exec npm run dev --workspace "@xa/fronted-$workspace" -- --port "$port" --strictPort
