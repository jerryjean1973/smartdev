"""Authenticated access to the workspace-scoped project API."""
import json
import os
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def memory_settings():
    if "COMMAND_MEMORY_URL" in os.environ and "COMMAND_MEMORY_TOKEN" in os.environ:
        config = {"url": os.environ["COMMAND_MEMORY_URL"], "token": os.environ["COMMAND_MEMORY_TOKEN"]}
    else:
        config = json.loads(Path("/run/command-memory/memory.json").read_text())
    return config


def exchange(payload, suffix="", timeout=8):
    config = memory_settings()
    request = urllib.request.Request(
        config["url"] + suffix,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + config["token"],
                 "Content-Type": "application/json"},
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        return json.load(response)



def dispatch_host_version(approval_id, task_id, execution_id, ticket):
    """Request the fixed DEV host probe using a lease-specific MIG enrollment."""
    return exchange({
        "approval_id": approval_id, "task_id": task_id, "execution_id": execution_id,
        "environment": "dev", "operation": "codex.version", "ticket": ticket,
    }, "/operator-task")
