#!/bin/sh
# Render the Redis config at startup. The password arrives as a Compose
# secret file, so it never appears in the container's argv or environment.
set -eu

TEMPLATE=/usr/local/etc/redis/redis.conf.template
GENERATED=/tmp/redis.generated.conf
SECRET_FILE=/run/secrets/redis_password

[ -r "$SECRET_FILE" ] || {
  echo "Redis password secret is missing or unreadable." >&2
  exit 1
}

REDIS_PASSWORD=$(cat "$SECRET_FILE")

[ -n "$REDIS_PASSWORD" ] || {
  echo "Redis password secret is empty." >&2
  exit 1
}

# redis.conf reads an unquoted value up to the first whitespace, so refuse a
# password that would be silently truncated.
case "$REDIS_PASSWORD" in
  *'
'*|*' '*|*'	'*)
    echo "Redis password contains unsupported whitespace." >&2
    exit 1
    ;;
esac

umask 077
cp "$TEMPLATE" "$GENERATED"
printf 'requirepass %s\n' "$REDIS_PASSWORD" >> "$GENERATED"
chmod 600 "$GENERATED"

# The official image drops to the 'redis' user via gosu; hand the file over
# first so it stays readable at 0600.
if id redis >/dev/null 2>&1; then
  chown redis:redis "$GENERATED"
fi

unset REDIS_PASSWORD

if command -v docker-entrypoint.sh >/dev/null 2>&1; then
  exec docker-entrypoint.sh redis-server "$GENERATED"
fi

exec redis-server "$GENERATED"
