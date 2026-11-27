#!/usr/bin/env python3
"""Serialize and reconcile local SmartDev Coder workspace updates."""
import argparse
import fcntl
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import UUID

ACTIVE = {"pending", "starting", "stopping", "canceling", "deleting"}
TERMINAL = {"running", "stopped", "failed", "canceled"}


def reconcile(request, workspace_id, version, deadline, pause):
    path = f"/workspaces/{workspace_id}"
    submitted = None
    while time.monotonic() < deadline:
        build = request(path)["latest_build"]
        status = build["status"]
        if status in ACTIVE:
            pause()
            continue
        if status not in TERMINAL:
            raise RuntimeError(f"Workspace has unsupported status {status}")
        if status == "running" and build["template_version_id"] == version:
            return
        if submitted == build["id"]:
            raise RuntimeError(f"Requested build {submitted} finished with status {status}")
        try:
            submitted = request(path + "/builds", {
                "transition": "start", "template_version_id": version,
            })["id"]
        except HTTPError as error:
            if error.code != 409:
                raise
        pause()
    raise TimeoutError(f"Workspace {workspace_id} did not reach version {version} before timeout")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--version", type=UUID, help="Pin an exact template version UUID")
    parser.add_argument("workspace", help="Workspace name, owner/name, or UUID")
    args = parser.parse_args()
    if not args.yes:
        parser.error("Pass --yes to authorize this workspace update")
    token = None
    for line in args.env_file.read_text().splitlines():
        if line.startswith("CODER_TOKEN="):
            token = line.partition("=")[2]
    if not token:
        raise RuntimeError("CODER_TOKEN missing from the configured environment file")
    base = "http://127.0.0.1:17080/api/v2"

    def request(path, body=None):
        query = Request(base + path, None if body is None else json.dumps(body).encode(),
                        {"Coder-Session-Token": token, "Content-Type": "application/json"})
        with urlopen(query, timeout=10) as response:
            return json.load(response)

    try:
        workspace_id = UUID(args.workspace)
    except ValueError:
        owner, sep, name = args.workspace.partition("/")
        if not sep:
            owner, name = "me", owner
        workspace_id = UUID(request(f"/users/{quote(owner, safe='')}/workspace/{quote(name, safe='')}")["id"])
    lockdir = Path("/run/lock/smartdev-coder-update")
    lockdir.mkdir(mode=0o700, exist_ok=True)
    deadline = time.monotonic() + 600
    with (lockdir / str(workspace_id)).open("a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for the updater for workspace {workspace_id}") from None
                time.sleep(2)
        workspace = request(f"/workspaces/{workspace_id}")
        version = str(args.version or UUID(workspace["template_active_version_id"]))
        reconcile(request, workspace_id, version, deadline, lambda: time.sleep(2))
    print(f"Workspace {workspace_id} is running template version {version}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        sys.exit(f"SmartDev workspace update failed: {error}")
