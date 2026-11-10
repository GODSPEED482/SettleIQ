#!/usr/bin/env bash
# Copies production values from the gitignored .env.production into SSM Parameter Store, which
# deploy.sh reads on the host, and sets the BUDGET_ALERT_EMAIL secret that infra.yml reads.
# Run from the repo root with your own AWS and gh logins: bash infra/push-prod-env.sh
# Prints names only. Nothing is sent unless every check passes.
set -euo pipefail
export MSYS_NO_PATHCONV=1 # stops Git Bash rewriting /settleiq/... into a Windows path

REGION=ap-south-1
PARAM_PATH=/settleiq/prod
PROD_FILE=.env.production
LOCAL_FILE=.env

required="ENVIRONMENT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD APP_DB_PASSWORD ACME_EMAIL BUDGET_ALERT_EMAIL"
optional="DISPATCHER_DB_PASSWORD AGENT_TOKEN_SECRET LLM_API_KEY LLM_MODEL COMPOSE_PROFILES APP_BASE_URL SMTP_HOST SMTP_PORT SMTP_USERNAME SMTP_PASSWORD SMTP_FROM"
# Not secrets, or one relay account shared by both environments; these may match .env.
may_match_local="COMPOSE_PROFILES LLM_MODEL SMTP_HOST SMTP_PORT SMTP_USERNAME SMTP_FROM"
# Embedded in connection URLs and read from a Compose .env, so $, @ and / would break them.
url_safe="POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD APP_DB_PASSWORD DISPATCHER_DB_PASSWORD"

value_of() {
  [ -f "$1" ] || return 0
  sed -n "s/^$2=//p" "$1" | tail -n 1 | tr -d '\r' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g'
}

errors=0
problem() {
  echo "!! $1" >&2
  errors=$((errors + 1))
}

if [ ! -f "$PROD_FILE" ]; then
  echo "!! $PROD_FILE not found; .env.example lists what it needs" >&2
  exit 1
fi
git check-ignore -q "$PROD_FILE" || problem "$PROD_FILE is not ignored by git"

while IFS= read -r name; do
  case " $required $optional " in
    *" $name "*) ;;
    *) echo "warning: $name is in $PROD_FILE but is not sent; add it to the lists in this script" >&2 ;;
  esac
done < <(sed -n -E 's/^([A-Za-z_][A-Za-z0-9_]*)=.*/\1/p' "$PROD_FILE" | tr -d '\r')

for name in $required $optional; do
  value="$(value_of "$PROD_FILE" "$name")"
  if [ -z "$value" ]; then
    case " $required " in *" $name "*) problem "$name is empty" ;; esac
    continue
  fi
  [ "$value" != CHANGE_ME ] || problem "$name is still CHANGE_ME"
  case " $url_safe " in
    *" $name "*) [[ "$value" =~ ^[A-Za-z0-9_-]+$ ]] || problem "$name has characters that break a connection URL" ;;
  esac
  case " $may_match_local " in *" $name "*) continue ;; esac
  if [ "$value" = "$(value_of "$LOCAL_FILE" "$name")" ]; then
    problem "$name is the same as in $LOCAL_FILE; production must use its own value"
  fi
done
[ "$(value_of "$PROD_FILE" ENVIRONMENT)" = production ] || problem "ENVIRONMENT must be production"

if [ "$errors" -gt 0 ]; then
  echo "!! $errors problem(s) in $PROD_FILE; nothing was sent" >&2
  exit 1
fi

for name in $required $optional; do
  [ "$name" != BUDGET_ALERT_EMAIL ] || continue
  value="$(value_of "$PROD_FILE" "$name")"
  if [ -z "$value" ]; then
    echo "skipped $name (empty)"
    continue
  fi
  aws ssm put-parameter --region "$REGION" --name "$PARAM_PATH/$name" --type SecureString \
    --overwrite --value "$value" >/dev/null
  echo "set $name"
done

printf '%s' "$(value_of "$PROD_FILE" BUDGET_ALERT_EMAIL)" | gh secret set BUDGET_ALERT_EMAIL
echo "set GitHub secret BUDGET_ALERT_EMAIL"
