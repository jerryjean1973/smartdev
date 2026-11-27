"""Read-only Vultr destination and plan checks for Command Center."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

BASE = "https://api.vultr.com/v2"
REGION = "atl"
PLAN = "vc2-4c-8gb"


def request(path, token):
    with urlopen(Request(BASE + path, headers={"Authorization": "Bearer " + token}), timeout=12) as response:
        return json.load(response)


def paged(get, resource, collection):
    cursor = None
    seen = set()
    while True:
        params = {"per_page": 100}
        if cursor:
            params["cursor"] = cursor
        page = get(resource + "?" + urlencode(params))
        yield from page[collection]
        cursor = page.get("meta", {}).get("links", {}).get("next")
        if not cursor:
            return
        if cursor in seen:
            raise ValueError("Vultr returned a repeated cursor.")
        seen.add(cursor)


def inventory(get):
    fields = ("id", "label", "region", "plan", "os", "status", "power_status", "server_status")
    return [{key: row[key] for key in fields} for row in paged(get, "/instances", "instances")]


def preflight(get, instance_id=None):
    instances = inventory(get)
    result = {"mode": "read_only", "instances": instances}
    if instance_id:
        selected = next((row for row in instances if row["id"] == str(instance_id)), None)
        if not selected:
            raise ValueError("The selected instance is not visible in this Vultr account.")
        running = (selected["status"], selected["power_status"], selected["server_status"]) == (
            "active", "running", "ok",
        )
        result["destination"] = {
            "kind": "existing_vultr_instance", "instance": selected,
            "provider_ready": running,
            "install_ready": False,
            "outstanding": ["Confirm project-to-host binding", "Verify SSH access and enrolled host identity",
                            "Verify the exact standalone release and installation receipt path"],
        }
    else:
        supported = get("/regions/" + REGION + "/availability")["available_plans"]
        offer = next((row for row in paged(get, "/plans", "plans") if row["id"] == PLAN), None)
        if not offer:
            raise ValueError("The selected plan is not listed by Vultr.")
        result["offer"] = {
            "region": REGION, "plan": PLAN, "available": PLAN in supported,
            "vcpus": offer["vcpu_count"], "ram_mb": offer["ram"],
            "monthly_cost_usd": offer["monthly_cost"],
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--instance-id", type=UUID)
    args = parser.parse_args()
    try:
        token = args.token_file.read_text().strip()
        if not token:
            raise ValueError("The Vultr credential file is empty.")
        result = preflight(lambda path: request(path, token), args.instance_id)
    except HTTPError as error:
        parser.exit(1, f"Vultr read returned HTTP {error.code}.\n")
    except (URLError, OSError, ValueError, KeyError):
        parser.exit(1, "Vultr preflight could not validate the credential, provider data, or selected instance.\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
