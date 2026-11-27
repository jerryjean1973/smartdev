#!/bin/sh
set -eu
umask 077
case "$DEV_GATEWAY_TOKEN" in ''|*[!a-f0-9]*) echo "Invalid DEV gateway authorization configuration" >&2; exit 1;; esac
[ "${#DEV_GATEWAY_TOKEN}" -eq 64 ]
printf 'proxy_set_header X-SmartDev-Gateway "%s";\n' "$DEV_GATEWAY_TOKEN" > /etc/nginx/dev-gateway-auth.inc
nginx -t
exec nginx -g 'daemon off;'
