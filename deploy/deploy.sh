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
# Terraform seeds every parameter with CHANGE_ME. Leave those out, so an optional value nobody
# has set falls back to its Compose default instead of reaching the app (SMTP_PORT=CHANGE_ME
# stopped the API from starting).
aws ssm get-parameters-by-path --region "$REGION" --path "$PARAM_PATH" --with-decryption \
  --query 'Parameters[].[Name,Value]' --output text \
  | awk -F'\t' '$2 != "CHANGE_ME" { n = $1; sub(".*/", "", n); print n "=" $2 }' > .env
{
  echo "TAG=$TAG"
  echo "REGISTRY=$REGISTRY"
} >> .env
chmod 600 .env

echo "==> rendering source secrets from SSM ${PARAM_PATH}/source"
# One file per parameter, source-secrets/<tenant_id>/<NAME>, read by a `managed:<NAME>` reference
# on that tenant's sources only (backend/app/sources/secrets.py). Containers cannot reach the
# instance role, so the host renders them, readable only by the app user. Values are never printed.
# Rendered afresh each run, so a deleted parameter does not linger. The directory is emptied, not
# replaced: a redeploy of the same release leaves api and worker running, and their bind mount
# would otherwise keep pointing at the deleted directory and see no secrets at all.
install -d -o 10001 -g 10001 -m 500 source-secrets
find source-secrets -mindepth 1 -delete
uuid='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
aws ssm get-parameters-by-path --region "$REGION" --path "${PARAM_PATH%/}/source" --recursive \
  --with-decryption --query 'Parameters[].[Name,Value]' --output text \
  | while IFS=$'\t' read -r name value; do
      name="${name#"${PARAM_PATH%/}/source/"}"
      # A tenant ID, then a name secrets.py accepts: upper case, digits and underscores. Anything
      # else, including the flat layout from before tenant namespacing, is skipped.
      [[ "$name" =~ ^$uuid/[A-Z][A-Z0-9_]{0,99}$ ]] || continue
      [ "$value" != "CHANGE_ME" ] || continue
      [ -d "source-secrets/${name%/*}" ] \
        || install -d -o 10001 -g 10001 -m 500 "source-secrets/${name%/*}"
      install -o 10001 -g 10001 -m 400 /dev/null "source-secrets/$name"
      printf '%s' "$value" > "source-secrets/$name"
    done

# A required value still on CHANGE_ME was left out above, so it fails here as unset.
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
# user_data sets this up only when an instance is created. On an older host Docker creates the
# bind mount as root, and the app (uid 10001 in compose.yaml) cannot write evidence to it.
install -d -o 10001 -g 10001 -m 750 /data/raw-evidence
docker compose up -d --remove-orphans --wait --wait-timeout 120

echo "$TAG" > "$APP/current"
echo "==> live: $TAG (previous: ${PREV:-none})"

# housekeeping: keep the last 5 release dirs (rollback targets) and recent images
# shellcheck disable=SC2012 # release directories are named by git SHA
ls -1dt "$APP"/releases/*/ | tail -n +6 | xargs -r rm -rf
docker image prune -af --filter "until=168h" >/dev/null || true
