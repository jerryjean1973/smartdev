#!/usr/bin/env bash
set -euo pipefail

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ $EUID == 0 ]] || { echo "Run from the authorized Ubuntu root session." >&2; exit 1; }
[[ -x /usr/bin/python3 && -x /usr/bin/docker ]]
[[ $(docker --host unix:///var/run/docker.sock inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}/{{ index .Config.Labels "com.docker.compose.service" }}' smart-dev-ide-browser-portal-1) == smart-dev-ide-browser/portal ]]
systemctl is-active --quiet docker.service

changes=()
install_new() {
  local source=$1 destination=$2 mode=$3
  if [[ -e $destination || -L $destination ]]; then
    [[ ! -L $destination ]] && cmp -s -- "$source" "$destination" || {
      echo "Existing configuration differs: $destination" >&2
      return 1
    }
    return
  fi
  changes+=("$destination")
  install -o root -g root -m "$mode" -- "$source" "$destination"
}

cleanup() {
  local status=$1
  if ((status != 0 && ${#changes[@]} > 0)); then
    systemctl disable --now smartdev-operator.socket >/dev/null 2>&1 || true
    systemctl stop smartdev-operator.service >/dev/null 2>&1 || true
    rm -f -- "${changes[@]}"
    systemctl daemon-reload || true
  fi
}
install -d -o root -g root -m 0755 /usr/local/lib/smartdev-operator
install -d -o root -g root -m 0700 /etc/smartdev-operator
config=$(mktemp)
printf 'portal\n' > "$config"
trap 'status=$?; rm -f -- "$config"; cleanup "$status"' EXIT
install_new "$source_dir/host_operator.py" /usr/local/lib/smartdev-operator/host_operator.py 0644
install_new "$source_dir/smartdev-operator.service" /etc/systemd/system/smartdev-operator.service 0644
install_new "$source_dir/smartdev-operator.socket" /etc/systemd/system/smartdev-operator.socket 0644
# Keep administrator-managed enrollment on subsequent installations.
if [[ ! -e /etc/smartdev-operator/services ]]; then
  install_new "$config" /etc/smartdev-operator/services 0600
fi
systemctl daemon-reload
systemctl enable --now smartdev-operator.socket
/usr/bin/python3 - <<'PY'
import json
import socket

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(50)
    client.connect("/run/smartdev-operator.sock")
    client.sendall(b'{"operation":"container.inspect","service":"portal"}\n')
    with client.makefile("rb") as stream:
        receipt = json.loads(stream.readline())
    print(json.dumps(receipt))
    if receipt.get("status") != "observed":
        raise SystemExit("Operator verification failed")
PY
