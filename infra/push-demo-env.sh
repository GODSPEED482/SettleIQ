#!/usr/bin/env bash
# Copies the demo business's values from the gitignored .env.demo into SSM Parameter Store under
# /settleiq/demo, which deploy-demo/deploy.sh reads on the demo host. It creates the parameters,
# so it can run before or after Terraform creates the host.
# Run from the repo root with your own AWS login: bash infra/push-demo-env.sh
# Prints names only. Nothing is sent unless every check passes.
set -euo pipefail
export MSYS_NO_PATHCONV=1 # stops Git Bash rewriting /settleiq/... into a Windows path

REGION=ap-south-1
PARAM_PATH=/settleiq/demo
DEMO_FILE=.env.demo

required="POSTGRES_PASSWORD MERCHANT_READER_PASSWORD MERCHANT_WRITER_PASSWORD ACME_EMAIL PAYPAL_CLIENT_ID PAYPAL_CLIENT_SECRET"
optional="MERCHANT_ID SETTLEIQ_URL RESET_COOLDOWN_SECONDS"
# Embedded in connection URLs and read from a Compose .env, so $, @ and / would break them.
url_safe="POSTGRES_PASSWORD MERCHANT_READER_PASSWORD MERCHANT_WRITER_PASSWORD"

value_of() {
  [ -f "$1" ] || return 0
  sed -n "s/^$2=//p" "$1" | tail -n 1 | tr -d '\r' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g'
}

errors=0
problem() {
  echo "!! $1" >&2
  errors=$((errors + 1))
}

if [ ! -f "$DEMO_FILE" ]; then
  echo "!! $DEMO_FILE not found; docs/DEMO.md lists what it needs" >&2
  exit 1
fi
git check-ignore -q "$DEMO_FILE" || problem "$DEMO_FILE is not ignored by git"

while IFS= read -r name; do
  case " $required $optional " in
    *" $name "*) ;;
    *) echo "warning: $name is in $DEMO_FILE but is not sent; add it to the lists in this script" >&2 ;;
  esac
done < <(sed -n -E 's/^([A-Za-z_][A-Za-z0-9_]*)=.*/\1/p' "$DEMO_FILE" | tr -d '\r')

for name in $required $optional; do
  value="$(value_of "$DEMO_FILE" "$name")"
  if [ -z "$value" ]; then
    case " $required " in *" $name "*) problem "$name is empty" ;; esac
    continue
  fi
  [ "$value" != CHANGE_ME ] || problem "$name is still CHANGE_ME"
  case " $url_safe " in
    *" $name "*) [[ "$value" =~ ^[A-Za-z0-9_-]{16,}$ ]] || problem "$name must be at least 16 letters, digits, - or _" ;;
  esac
done
for a in $url_safe; do
  for b in $url_safe; do
    if [ "$a" \< "$b" ] && [ "$(value_of "$DEMO_FILE" "$a")" = "$(value_of "$DEMO_FILE" "$b")" ]; then
      problem "$a and $b must differ"
    fi
  done
done

if [ "$errors" -gt 0 ]; then
  echo "!! $errors problem(s) in $DEMO_FILE; nothing was sent" >&2
  exit 1
fi

for name in $required $optional; do
  value="$(value_of "$DEMO_FILE" "$name")"
  if [ -z "$value" ]; then
    echo "skipped $name (empty)"
    continue
  fi
  aws ssm put-parameter --region "$REGION" --name "$PARAM_PATH/$name" --type SecureString \
    --overwrite --value "$value" >/dev/null
  echo "set $name"
done
