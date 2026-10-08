#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_COMMIT:?DEPLOY_COMMIT is required}"
: "${DOCKERHUB_NAMESPACE:?DOCKERHUB_NAMESPACE is required}"
: "${XA_IMAGE_TAG:?XA_IMAGE_TAG is required}"
[[ "$DEPLOY_COMMIT" =~ ^[a-f0-9]{40}$ ]] || { echo 'Expected a full commit SHA.' >&2; exit 1; }
[[ "$XA_IMAGE_TAG" == "sha-$DEPLOY_COMMIT" ]] || { echo 'Image tag must match the source commit.' >&2; exit 1; }
[[ "$DOCKERHUB_NAMESPACE" =~ ^[a-z0-9]+([_.-][a-z0-9]+)*$ ]] || { echo 'Invalid Docker Hub namespace.' >&2; exit 1; }
case "${XA_DATABASE_MODE:-embedded}" in
  embedded) compose_files=(-f docker-compose.remote.yml -f docker-compose.release.yml) ;;
  managed) compose_files=(-f docker-compose.managed.yml) ;;
  *) echo 'XA_DATABASE_MODE must be embedded or managed.' >&2; exit 1 ;;
esac

cd "${XA_DEPLOY_DIR:-/opt/xa}"
[[ -f .env.release ]] || { echo 'Prepare .env.release before deploying.' >&2; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo 'Tracked server files have changes; deployment stopped without discarding them.' >&2; exit 1; }
git fetch origin "$DEPLOY_COMMIT"
git checkout --detach "$DEPLOY_COMMIT"
[[ "$(git rev-parse HEAD)" == "$DEPLOY_COMMIT" ]] || { echo 'Source commit verification failed.' >&2; exit 1; }

export DOCKERHUB_NAMESPACE XA_IMAGE_TAG
compose=(docker compose --env-file .env.release "${compose_files[@]}")
"${compose[@]}" config --quiet
"${compose[@]}" pull
"${compose[@]}" up -d --no-build --remove-orphans --wait --wait-timeout 180
"${compose[@]}" exec -T web python manage.py check
"${compose[@]}" exec -T web python manage.py check_mysql_readiness
"${compose[@]}" ps
