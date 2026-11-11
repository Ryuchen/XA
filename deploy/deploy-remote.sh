#!/usr/bin/env bash
# Runner-side SSH preflight and immutable deployment. Never enable shell tracing.
set -euo pipefail

: "${DEPLOY_HOST:?DEPLOY_HOST is required}"
: "${DEPLOY_USER:?DEPLOY_USER is required}"
: "${DOCKERHUB_TOKEN:?DOCKERHUB_TOKEN is required}"
[[ "${DOCKERHUB_USERNAME:-}" =~ ^[a-z0-9]+([_.-][a-z0-9]+)*$ ]] || { echo 'Invalid Docker Hub username.' >&2; exit 1; }
[[ "${DEPLOY_COMMIT:-}" =~ ^[a-f0-9]{40}$ ]] || { echo 'Expected a full deployment commit SHA.' >&2; exit 1; }
[[ "${XA_DATABASE_MODE:-embedded}" == embedded || "${XA_DATABASE_MODE:-embedded}" == managed ]] || { echo 'XA_DATABASE_MODE must be embedded or managed.' >&2; exit 1; }

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ssh_options=(-T -o BatchMode=yes -o ConnectTimeout=15 -o ConnectionAttempts=1 -o StrictHostKeyChecking=accept-new)
target="$DEPLOY_USER@$DEPLOY_HOST"

# Authenticate before handing Docker Hub credentials to SSH or changing services.
# Preserve SSH's exit code; a failed preflight must never start a deployment.
if ssh "${ssh_options[@]}" "$target" true </dev/null; then
  echo 'SSH authentication preflight passed.'
else
  status=$?
  echo 'SSH preflight failed; registry login and deployment were not attempted.' >&2
  echo 'For Permission denied (publickey), verify DEPLOY_HOST, DEPLOY_USER and that DEPLOY_SSH_PRIVATE_KEY matches a key authorized for that exact server account.' >&2
  echo 'An ssh-agent key comment warning is not an authentication result. Do not disable host-key checking or print private keys to debug this failure.' >&2
  exit "$status"
fi

printf '%s' "$DOCKERHUB_TOKEN" | ssh "${ssh_options[@]}" "$target" \
  "docker login --username '$DOCKERHUB_USERNAME' --password-stdin"
ssh "${ssh_options[@]}" "$target" \
  "DOCKERHUB_NAMESPACE='$DOCKERHUB_USERNAME' XA_IMAGE_TAG='sha-$DEPLOY_COMMIT' DEPLOY_COMMIT='$DEPLOY_COMMIT' XA_DATABASE_MODE='${XA_DATABASE_MODE:-embedded}' bash -s" \
  < "$script_dir/deploy-release.sh"
