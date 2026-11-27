#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SMARTDEV_TEST_BROWSER_STATE:?Provide a dedicated Coder Playwright storage-state file}"
api=${SMARTDEV_TEST_API_CONTAINER:-smart-dev-ide-browser-api-1}
worker=${SMARTDEV_TEST_WORKER_CONTAINER:-smart-dev-ide-browser-worker-1}
node=${SMARTDEV_TEST_NODE:-node}
: "${SMARTDEV_TEST_OPENAI_KEY_FILE:?Provide a DEV OpenAI credential file authorized for disposable acceptance projects}"
docker exec -i "$api" python -c '
import sys
from fastapi import HTTPException
from codex_access import verify_provider_key
try:
    verify_provider_key(sys.stdin.read(4097))
except HTTPException as error:
    sys.exit(error.detail)
' < "$SMARTDEV_TEST_OPENAI_KEY_FILE"
umask 077
artifacts=$(mktemp -d "${SMARTDEV_TEST_ARTIFACT_ROOT:-/tmp}/smartdev-acceptance.XXXXXX")
remote_token="/tmp/${artifacts##*/}.token"
project_id=

cleanup() {
  local exit_code=$?
  trap - EXIT
  if [[ -n "$project_id" ]]; then
    local closed=false
    for ((attempt=0; attempt<25; attempt++)); do
      if docker exec "$api" python acceptance_fixture.py close "$project_id" > "$artifacts/close.log" 2>&1; then
        closed=true
        break
      fi
      sleep 4
    done
    if ! "$closed" || ! docker exec "$worker" python acceptance_fixture.py stop "$project_id"; then
      printf 'Cleanup needs retry for %s. Artifacts in %s\n' "$project_id" "$artifacts" >&2
      exit 1
    fi
  fi
  docker exec "$api" python -c 'from pathlib import Path; import sys; Path(sys.argv[1]).unlink(missing_ok=True)' "$remote_token"
  "$node" -e 'require("node:fs").rmSync(process.argv[1], {force: true})' "$artifacts/token"
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker exec "$api" python acceptance_fixture.py create "$remote_token" > "$artifacts/fixture.json"
project_id=$("$node" -p 'require(process.argv[1]).project_id' "$artifacts/fixture.json")
export SMARTDEV_TEST_PROJECT_ID=$project_id
export SMARTDEV_TEST_CONVERSATION_ID=$("$node" -p 'require(process.argv[1]).conversation_id' "$artifacts/fixture.json")
export SMARTDEV_TEST_ACCEPTANCE_TOKEN_FILE="$artifacts/token"
docker cp "$api:$remote_token" "$SMARTDEV_TEST_ACCEPTANCE_TOKEN_FILE"
chmod 600 "$SMARTDEV_TEST_ACCEPTANCE_TOKEN_FILE"
printf 'Fixture %s. Artifacts in %s\n' "$project_id" "$artifacts"

for ((attempt=0; attempt<45; attempt++)); do
  workspace_url=$(docker exec "$api" python -c '
import sys
from store import database
with database() as db:
    row = db.execute("SELECT workspace_url, status FROM projects WHERE id=%s", (sys.argv[1],)).fetchone()
    if row["status"] == "failed":
        sys.exit("Fixture workspace provisioning failed")
    print(row["workspace_url"] or "")
' "$project_id")
  [[ -n "$workspace_url" ]] && break
  sleep 2
done
[[ -n "$workspace_url" ]]
export SMARTDEV_TEST_CHAT_URL="${workspace_url%/}/apps/codex-chat/"

docker exec -i "$api" python -c '
import re, sys
from uuid import UUID
from store import database
from vault import call
key = sys.stdin.read(4097).strip()
if not re.fullmatch(r"[!-~]{16,4096}", key):
    sys.exit("Invalid fixture DEV OpenAI key")
project = UUID(sys.argv[1])
with database() as db:
    row = db.execute("""SELECT p.id FROM projects p JOIN acceptance_fixture f ON f.project_id=p.id
                        WHERE p.id=%s AND NOT p.archived AND f.closed_at IS NULL FOR UPDATE OF p""",
                     (project,)).fetchone()
    if not row:
        sys.exit("Active acceptance fixture required")
    call("POST", f"data/projects/{project}/DEV/OPENAI_API_KEY",
         {"options": {"cas": 0}, "data": {"value": key}})
    db.execute("INSERT INTO audit (project_id,action) VALUES (%s,%s)",
               (project,"vault:write:DEV:OPENAI_API_KEY"))
' "$project_id" < "$SMARTDEV_TEST_OPENAI_KEY_FILE"

workspace_name=$("$node" -p 'require(process.argv[1]).workspace_name' "$artifacts/fixture.json")
container=
for ((attempt=0; attempt<45; attempt++)); do
  while read -r candidate; do
    [[ $(docker exec "$candidate" printenv PROJECT_NAME 2>/dev/null) == "$workspace_name" ]] || continue
    container=$candidate
    break
  done < <(docker ps --filter name=command-dev- --format '{{.ID}}')
  [[ -n "$container" ]] && break
  sleep 2
done
[[ -n "$container" ]]
ready=false
for ((attempt=0; attempt<30; attempt++)); do
  if docker exec "$container" curl -fsS http://127.0.0.1:13338/ > /dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 2
done
"$ready"
docker exec -u 1000 "$container" sh -ec '
  cd "/projects/$PROJECT_NAME/repository"
  git -c user.name=Acceptance -c user.email=acceptance@invalid commit --allow-empty -m "Initialize acceptance fixture"
'
"$node" tests/acceptance_isolation_live.cjs 2>&1 | tee "$artifacts/acceptance.log"
