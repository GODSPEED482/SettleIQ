#!/usr/bin/env bash
# Runs ON the demo host (via SSM). Usage: deploy.sh <image-tag = git sha>
# Idempotent: re-running with the same tag is a no-op restart. The shop applies its own schema on
# start, so there is no migration step, and the previous release is restored if this one fails.
set -euo pipefail

TAG="${1:?usage: deploy.sh <tag>}"
APP=/opt/kitebase
REL="$APP/releases/$TAG"
# shellcheck source=/dev/null
source "$APP/host.env" # REGION, REGISTRY, PARAM_PATH, RELEASE_BUCKET

PREV="$(cat "$APP/current" 2>/dev/null || true)"

rollback() {
  echo "!! deploy of $TAG failed"
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
  | awk -F'\t' '$2 != "CHANGE_ME" { n = $1; sub(".*/", "", n); print n "=" $2 }' > .env
{
  echo "TAG=$TAG"
  echo "REGISTRY=$REGISTRY"
} >> .env
chmod 600 .env

required="POSTGRES_PASSWORD MERCHANT_READER_PASSWORD MERCHANT_WRITER_PASSWORD ACME_EMAIL"
for name in $required; do
  if ! grep -q "^$name=." .env; then
    echo "!! $PARAM_PATH$name is not set; run infra/push-demo-env.sh, then re-run this deploy. Nothing was changed."
    trap - ERR
    exit 1
  fi
done
grep -q '^PAYPAL_CLIENT_ID=.' .env || echo "warning: PAYPAL_CLIENT_ID is not set; checkout stays off"

echo "==> pulling $TAG"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker compose pull --quiet

echo "==> rolling services"
docker compose up -d --remove-orphans --wait --wait-timeout 180

echo "$TAG" > "$APP/current"
echo "==> live: $TAG (previous: ${PREV:-none})"

# housekeeping: keep the last 5 release dirs and recent images
# shellcheck disable=SC2012 # release directories are named by git SHA
ls -1dt "$APP"/releases/*/ | tail -n +6 | xargs -r rm -rf
docker image prune -af --filter "until=168h" >/dev/null || true
