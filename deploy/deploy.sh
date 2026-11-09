#!/usr/bin/env bash
# Runs ON the host (via SSM). Usage: deploy.sh <image-tag = git sha> [--rollback]
# Idempotent: re-running with the same tag is a no-op restart.
# --rollback moves to an older tag and skips migrations: Alembic cannot upgrade to "head" from an
# image that predates the revision the database is already on.
set -euo pipefail

TAG="${1:?usage: deploy.sh <tag> [--rollback]}"
MODE="${2:-}"
APP=/opt/settleiq
REL="$APP/releases/$TAG"
# shellcheck source=/dev/null
source "$APP/host.env" # REGION, REGISTRY, PARAM_PATH, RELEASE_BUCKET

PREV="$(cat "$APP/current" 2>/dev/null || true)"

rollback() {
  echo "!! deploy of $TAG failed; if it was the migration, see $REL/migrate.log on the host"
  if [ -n "$PREV" ] && [ "$PREV" != "$TAG" ] && [ -d "$APP/releases/$PREV" ]; then
    echo "!! rolling back to $PREV"
    (cd "$APP/releases/$PREV" && docker compose up -d --remove-orphans --wait --wait-timeout 120) || true
  fi
  exit 1
}
trap rollback ERR

cd "$REL"
touch "$REL" # newest mtime, so housekeeping below never prunes the live release

echo "==> rendering .env from SSM $PARAM_PATH"
aws ssm get-parameters-by-path --region "$REGION" --path "$PARAM_PATH" --with-decryption \
  --query 'Parameters[].[Name,Value]' --output text \
  | awk -F'\t' '{ n = $1; sub(".*/", "", n); print n "=" $2 }' > .env
{
  echo "TAG=$TAG"
  echo "REGISTRY=$REGISTRY"
} >> .env
chmod 600 .env

# Terraform seeds every parameter with CHANGE_ME; never start a database on that password.
required="ENVIRONMENT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD APP_DB_PASSWORD DISPATCHER_DB_PASSWORD ACME_EMAIL"
if grep -q '^COMPOSE_PROFILES=.*agent' .env; then
  required="$required AGENT_TOKEN_SECRET LLM_API_KEY LLM_MODEL"
fi
for name in $required; do
  if ! grep -q "^$name=." .env || grep -q "^$name=CHANGE_ME$" .env; then
    echo "!! $PARAM_PATH$name is not set; nothing was changed"
    exit 1
  fi
done

echo "==> pulling $TAG"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker compose pull --quiet

if [ "$MODE" = "--rollback" ]; then
  echo "==> rollback: skipping migrations"
else
  echo "==> migrating (before traffic moves; must be backward compatible)"
  # Kept on the host: a failed migration can print its SQL, which includes APP_DB_PASSWORD,
  # and everything this script writes to stdout or stderr reaches the public Actions log.
  install -m 600 /dev/null migrate.log
  docker compose run --rm migrate >migrate.log 2>&1
fi

echo "==> rolling services"
docker compose up -d --remove-orphans --wait --wait-timeout 120

echo "$TAG" > "$APP/current"
echo "==> live: $TAG (previous: ${PREV:-none})"

# housekeeping: keep the last 5 release dirs (rollback targets) and recent images
# shellcheck disable=SC2012 # release directories are named by git SHA
ls -1dt "$APP"/releases/*/ | tail -n +6 | xargs -r rm -rf
docker image prune -af --filter "until=168h" >/dev/null || true
