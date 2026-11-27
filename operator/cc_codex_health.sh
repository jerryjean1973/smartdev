#!/usr/bin/env bash
set -euo pipefail

api=smart-dev-ide-browser-api-1
runtime=/run/smartdev-cc-codex
socket=$runtime/chat.sock
url='http://localhost/chat?scope=main'

api_probe() {
  /usr/bin/timeout 10s /usr/bin/docker exec "$api" python -c '
import sys
import httpx

with httpx.Client(transport=httpx.HTTPTransport(uds=sys.argv[1]), timeout=5) as client:
    client.get(sys.argv[2]).raise_for_status()
' "$socket" "$url" >/dev/null 2>&1
}

if [[ $(/usr/bin/docker inspect --format '{{.State.Status}} {{index .Config.Labels "com.docker.compose.project"}}/{{index .Config.Labels "com.docker.compose.service"}}' "$api") != 'running smart-dev-ide-browser/api' ]]; then
  echo 'Ask CC health check skipped because the SmartDev API is not running.'
  exit 0
fi

if api_probe; then
  exit 0
fi

if ! /usr/bin/curl --fail --silent --show-error --max-time 5 --unix-socket "$socket" --output /dev/null "$url"; then
  echo 'Ask CC host socket is unhealthy.' >&2
  exit 1
fi

host_directory=$(/usr/bin/stat -c '%d:%i' "$runtime")
api_directory=$(/usr/bin/timeout 10s /usr/bin/docker exec "$api" stat -c '%d:%i' "$runtime")
if [[ "$host_directory" == "$api_directory" ]]; then
  echo 'Ask CC is unreachable from the API despite a healthy host socket and current bind mount.' >&2
  exit 1
fi

echo 'Ask CC socket bind mount is stale. Restarting the SmartDev API.'
/usr/bin/timeout 35s /usr/bin/docker restart --timeout 10 "$api"
api_probe
echo 'Ask CC socket is reachable from the SmartDev API after recovery.'
