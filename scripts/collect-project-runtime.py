#!/usr/bin/env python3
"""Publish bounded snapshots for registered SmartDev project runtimes without forwarding raw logs."""
import http.client
import time
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from uuid import UUID
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path("/var/lib/dev-command-center/runtime-observations")
DEFAULT_CONFIG = Path("/etc/smartdev/runtime-observations.json")



def docker(*args):
    return subprocess.check_output(["docker", *args], stderr=subprocess.STDOUT, text=True, timeout=10)


def summarize(logs):
    events = []
    for line in logs.splitlines():
        timestamp = line.split(" ", 1)[0]
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT[0-9:.]+Z", timestamp):
            continue
        if "ERR_MODULE_NOT_FOUND" in line and "build-fsa-rev1.mjs" in line:
            code = "FSA_RUNTIME_MODULE_MISSING"
        elif "DOMAIN_ACQUISITION_PROVIDER_BACKOFF" in line and "PROVIDER_HTTP_429" in line:
            code = "ACQUISITION_RATE_LIMITED"
        elif "CANONICAL_EXECUTION_SOURCE_NOT_AUTHORIZED" in line:
            code = "KPI_SOURCE_NOT_AUTHORIZED"
        else:
            continue
        events.append({"at": timestamp, "code": code})
    return events[-20:]


def collect(service):
    image, digest, health = docker("inspect", "--format",
        "{{.Config.Image}} {{.Image}} {{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
        service).split()
    if not re.fullmatch(r"[A-Za-z0-9_./:@-]+", image) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("Invalid image identity")
    return {"service": service, "image": image, "digest": digest, "health": health,
            "collectedAt": datetime.now(timezone.utc).isoformat(),
            "events": summarize(docker("logs", "--timestamps", "--since", "30m", "--tail", "500", service))}


def publish(target, snapshot):
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, mode="w") as temporary:
        try:
            json.dump(snapshot, temporary)
            temporary.close()
            os.chmod(temporary.name, 0o644)
            os.replace(temporary.name, target)
        finally:
            Path(temporary.name).unlink(missing_ok=True)


VG_PROJECT = "e2a3bfac-25a7-5fca-bc18-f5b2248c024f"
VG_APP = "vendorgate-app-1"


def portal_probe(connection):
    try:
        connection.request("GET", "/vendorgate-landing")
        response = connection.getresponse()
        body = response.read(4096)
        if response.status == 503 and b"UPDATE_MAINTENANCE" in body:
            return "maintenance"
        if response.status == 200 and b'<meta name="product-build" content="vendorgate">' in body:
            return "ready"
        return "unavailable"
    except (OSError, http.client.HTTPException):
        return "unavailable"
    finally:
        connection.close()


def observe_vg(snapshot, previous):
    identity = docker("inspect", "--format",
        '{{.Id}} {{.State.Status}} {{.State.StartedAt}} {{index .Config.Labels "com.docker.compose.project"}} '
        '{{index .Config.Labels "com.docker.compose.service"}}', VG_APP).strip().split()
    container, status, started, project, service = identity
    if (project, service) != ("vendorgate", "app") or not re.fullmatch(r"[0-9a-f]{64}", container):
        raise ValueError("Unexpected VG application identity")
    local = portal_probe(http.client.HTTPConnection("127.0.0.1",5001,timeout=4)) if status == "running" else status
    edge = portal_probe(http.client.HTTPSConnection("vendorgate.ca",timeout=5)) if local == "ready" else "unchecked"
    now = int(time.time())
    last_restart = previous.get("lastRecovery",0)
    failures = 0
    if local == "unavailable" and snapshot["health"] not in ("starting","paused"):
        failures = 1 + (previous.get("consecutiveFailures",0) if previous.get("startedAt") == started else 0)
    check = {"local":local, "public":edge, "startedAt":started, "consecutiveFailures":failures, "lastRecovery":last_restart}
    if local != "ready" or edge != "ready":
        snapshot["events"].append({"at":snapshot["collectedAt"],"code":"VG_PORTAL_" +
            ("PUBLIC_UNAVAILABLE" if local=="ready" else local.upper())})
    snapshot["portalCheck"] = check
    if failures >= 3 and now-last_restart >= 1800:
        # Target the observed immutable container, after checking its lifecycle again.
        current = docker("inspect","--format","{{.State.Status}} {{.State.StartedAt}}",container).strip()
        if current == "running " + started:
            check["lastRecovery"] = now
            publish(OUTPUT / (VG_PROJECT+".json"),snapshot)
            try:
                subprocess.run(["docker","restart","--time","10",container],check=True,
                               stdout=subprocess.DEVNULL,timeout=30)
                event = "VG_PORTAL_RECOVERY_RESTART"
            except (OSError,subprocess.SubprocessError):
                event = "VG_PORTAL_RECOVERY_FAILED"
            snapshot["events"].append({"at":snapshot["collectedAt"],"code":event})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    bindings = json.loads(args.config.read_text())
    if not isinstance(bindings, dict):
        raise ValueError("Runtime bindings must be an object")
    targets = {}
    for key, service in bindings.items():
        project = str(UUID(key))
        if project in targets or not isinstance(service, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", service):
            raise ValueError("Invalid project runtime binding")
        targets[project] = service
    for project, service in targets.items():
        try:
            snapshot = collect(service)
            if project == VG_PROJECT and service == VG_APP:
                path = OUTPUT / (project+".json")
                previous = json.loads(path.read_text()).get("portalCheck",{}) if path.exists() else {}
                observe_vg(snapshot,previous)
        except (subprocess.SubprocessError, OSError, ValueError):
            print(f"Could not collect runtime for project {project}", file=sys.stderr)
            snapshot = {"service": service, "health": "unavailable",
                        "collectedAt": datetime.now(timezone.utc).isoformat(), "events": []}
        publish(OUTPUT / f"{project}.json", snapshot)
    for stale in OUTPUT.glob("*.json"):
        if stale.stem not in targets:
            try:
                UUID(stale.stem)
            except ValueError:
                continue
            stale.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
