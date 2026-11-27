#!/bin/sh
set -eu
umask 077
workspace="/projects/$PROJECT_NAME"
repository="$workspace/repository"
mkdir -p "$workspace"
if [ ! -e "$repository" ]; then
    temporary="$(mktemp -d "$workspace/.selected-source-XXXXXX")"
    trap 'rm -rf "$temporary"' EXIT
    git clone --no-checkout /run/selected-source.bundle "$temporary"
    git -C "$temporary" checkout --detach "$PROJECT_REVISION"
    git -C "$temporary" remote set-url origin "$PROJECT_SOURCE_REMOTE"
    test "$(git -C "$temporary" rev-parse HEAD)" = "$PROJECT_REVISION"
    mv -- "$temporary" "$repository"
fi
exec /usr/local/bin/start-project
