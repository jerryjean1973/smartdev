"""Build a self-contained Ubuntu bootstrap pinned to an SMD Compose release bundle."""

import argparse
from base64 import b64encode
import json
from hashlib import sha256
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

BOOTSTRAP = r"""#!/bin/bash
set -euo pipefail
umask 077
exec 3>&1
exec 1>&2
[[ ( $# == 4 || $# == 6 ) && $1 == --project-id && $3 == --revision ]]
project=$2
revision=$4
manifest=
if [[ $# == 6 ]]; then
    [[ $5 == --project-manifest && $6 == /* && -f $6 ]]
    manifest=$6
fi
[[ $project =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ && $revision == "$release_revision" ]]
project_root="/opt/smartdev/$project"
install -d -m 0700 "$project_root/releases"
exec 4>"$project_root/install.lock"
flock 4
root="$project_root/releases/$release_identity"
scratch=$(mktemp -d "$project_root/.install-XXXXXXXX")
trap 'rm -rf "$scratch"' EXIT
curl --fail --silent --show-error --retry 5 --proto '=https' \
    --max-time 1200 "$bundle_url" -o "$scratch/bundle.tar"
echo "$bundle_sha  $scratch/bundle.tar" | sha256sum -c -
python3 - "$scratch/bundle.tar" "$scratch/release" "$bundle_revision" "$revision" "$release_overlay" <<'PY'
from base64 import b64decode
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

archive, destination, original_revision, revision, overlay = sys.argv[1:]
with tarfile.open(archive) as bundle:
    members = bundle.getmembers()
    seen = set()
    for member in members:
        path = PurePosixPath(member.name)
        if (path.is_absolute() or ".." in path.parts or path in seen
                or not (member.isfile() or member.isdir())):
            raise ValueError(f"Unexpected release bundle entry: {member.name!r}")
        seen.add(path)
    bundle.extractall(destination, members=members, filter="data")
root = Path(destination)
for name in ("compose.yaml", "configure.sh", "revision"):
    if not (root / name).is_file():
        raise ValueError(f"Missing release file: {name}")
if (root / "revision").read_text().rstrip("\n") != original_revision:
    raise ValueError("Release bundle revision does not match the installer")
allowed = {"configure.py", "compose.yaml", "apps.py", "projects.py", "enrollment.py"}
for name, encoded in json.loads(overlay).items():
    if name not in allowed:
        raise ValueError("Unexpected installer overlay entry")
    (root / name).write_bytes(b64decode(encoded, validate=True))
(root / "revision").write_text(revision + "\n")
PY
if [[ ! -d "$root" ]]; then
    mv -- "$scratch/release" "$root"
fi
cd "$root"
[[ $(cat revision) == "$revision" ]]
packages=()
if ! docker compose version >/dev/null 2>&1; then
    packages+=(docker.io docker-compose-v2)
fi
if [[ -n "$manifest" ]] && ! command -v git >/dev/null 2>&1; then
    packages+=(git)
fi
if (( ${#packages[@]} )); then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y "${packages[@]}"
fi
if ! docker info >/dev/null 2>&1; then
    systemctl enable --now docker
fi
if [[ -n "$manifest" ]]; then
    bash ./configure.sh "$project" "$manifest"
else
    bash ./configure.sh "$project"
fi
stack=(docker compose --project-name "smd-$project" -f compose.yaml)
if [[ -n "$manifest" ]]; then
    [[ -f enrollment-compose.yaml && -f enrollment.py ]]
    stack+=(-f enrollment-compose.yaml)
    if [[ -f app-networks.json ]]; then
        stack+=(-f app-networks.json)
    fi
fi
if [[ -f "$project_root/data/portal-access/current.json" ]]; then
    stack+=(-f "$project_root/data/portal-access/current.json")
fi
"${stack[@]}" config --format json > "$scratch/compose.json"
python3 - "$scratch/compose.json" "$root/images.tar.gz" <<'PY'
import json
import re
import sys
from pathlib import Path

local_images = Path(sys.argv[2]).is_file()
with open(sys.argv[1]) as source:
    services = json.load(source)["services"]
for name, service in services.items():
    image = service.get("image", "")
    pinned = (re.fullmatch(r"sha256:[0-9a-f]{64}", image) if local_images
              else re.search(r"@sha256:[0-9a-f]{64}$", image))
    if service.get("build") or not pinned:
        raise ValueError(f"Unpinned service image: {name}")
for name in ("api", "portal", "openbao"):
    health = services.get(name, {}).get("healthcheck", {})
    if health.get("disable") or not health.get("test") or "NONE" in health["test"]:
        raise ValueError(f"Missing service health check: {name}")
PY
if [[ -f images.tar.gz ]]; then
    docker image load --input images.tar.gz
    mapfile -t images < <("${stack[@]}" config --images)
    docker image inspect --format '{{.Id}}' "${images[@]}" >&2
    "${stack[@]}" up -d --wait --wait-timeout 300 --no-build --pull never
else
    "${stack[@]}" pull
    "${stack[@]}" up -d --wait --wait-timeout 300 --no-build
fi
for service in api portal openbao; do
    container=$("${stack[@]}" ps -q "$service")
    expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["services"][sys.argv[2]]["image"])' \
        "$scratch/compose.json" "$service")
    expected_id=$(docker image inspect --format '{{.Id}}' "$expected")
    actual=$(docker inspect --format '{{.Image}} {{.State.Health.Status}} {{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.service"}}' "$container")
    if [[ "$actual" != "$expected_id healthy smd-$project $service" ]]; then
        echo "Running service $service failed project, image, or health verification" >&2
        exit 1
    fi
done
if [[ -n "$manifest" ]]; then
    python3 ./enrollment.py "$project" "$manifest"
fi
printf '%s\n' "$revision" >&3
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_url")
    parser.add_argument("bundle_sha256")
    parser.add_argument("revision")
    parser.add_argument("output", type=Path)
    parser.add_argument("--qa-overlay", type=Path, help="Approved QA release directory")
    parser.add_argument("--base-revision", help="Revision inside the unchanged image bundle")
    args = parser.parse_args()
    if args.qa_overlay and not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", args.base_revision or ""):
        parser.error("The QA overlay also requires the original bundle revision.")
    overlay = {}
    if args.qa_overlay:
        for name in ("configure.py", "compose.yaml", "apps.py", "projects.py", "enrollment.py"):
            data = (args.qa_overlay / name).read_bytes()
            overlay[name] = b64encode(data).decode("ascii")
    changes = json.dumps(overlay, sort_keys=True, separators=(",", ":"))
    if len(changes) > 80000:
        parser.error("The QA overlay exceeds the supported size.")
    release_identity = sha256((args.bundle_sha256 + args.revision + changes).encode()).hexdigest()
    url = urlsplit(args.bundle_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment
            or not re.fullmatch(r"[a-f0-9]{64}", args.bundle_sha256)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", args.revision)):
        parser.error("Expected an HTTPS bundle URL, SHA-256 and release revision")
    variables = {
        "bundle_url": args.bundle_url,
        "bundle_sha": args.bundle_sha256,
        "release_revision": args.revision,
        "bundle_revision": args.base_revision or args.revision,
        "release_overlay": changes,
        "release_identity": release_identity,
    }
    shebang, script = BOOTSTRAP.split("\n", 1)
    parameters = "\n".join(f"{key}={shlex.quote(value)}" for key, value in variables.items())
    installer = f"{shebang}\n{parameters}\n{script}".encode()
    with args.output.open("xb") as target:
        target.write(installer)
    print(sha256(installer).hexdigest())


if __name__ == "__main__":
    main()
