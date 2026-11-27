"""Create one approved Vultr VM per SMD queue claim and reconcile uncertain replies."""

import base64
import binascii
from ipaddress import IPv4Address
import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

BASE = "https://api.vultr.com/v2"
REGION = "atl"
PLAN = "vc2-4c-8gb"
OS_ID = 2284
MONTHLY_COST_USD = 40


class CreateUnconfirmed(RuntimeError):
    """A previous create might have succeeded. The caller must reconcile by tag."""


class OfferChanged(ValueError):
    """The requested capacity or price differs from the owner-approved Vultr offer."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, message, headers, response)


def make_request(token):
    """Create the Vultr request transport without exposing the credential."""
    if not token.strip():
        raise ValueError("Vultr credential is empty.")
    opener = build_opener(NoRedirect)

    def request(method, path, body=None):
        headers = {"Authorization": "Bearer " + token.strip(), "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        query = Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                        headers=headers, method=method)
        with opener.open(query, timeout=15) as response:
            return json.load(response)

    return request


def pages(request, resource):
    seen = set()
    cursor = None
    while True:
        parameters = {"per_page": 100}
        if cursor:
            parameters["cursor"] = cursor
        page = request("GET", "/" + resource + "?" + urlencode(parameters))
        yield from page[resource]
        cursor = page.get("meta", {}).get("links", {}).get("next")
        if not cursor:
            return
        if cursor in seen:
            raise ValueError("Vultr repeated an inventory cursor.")
        seen.add(cursor)


def verify_offer(request):
    availability = request("GET", f"/regions/{REGION}/availability")["available_plans"]
    if PLAN not in availability:
        raise OfferChanged("The approved Vultr plan is no longer available in Atlanta.")
    plan = next((item for item in pages(request, "plans") if item["id"] == PLAN), None)
    if plan is None or (plan.get("vcpu_count"), plan.get("ram"), plan.get("monthly_cost")) != (
        4, 8192, MONTHLY_COST_USD,
    ):
        raise OfferChanged("Vultr's current plan specification or price differs from the approved offer.")


def exact_match(request, tag):
    matching = {}
    for item in pages(request, "instances"):
        if tag not in (item.get("tags") or []) and item.get("label") != tag:
            continue
        if (item.get("label"), item.get("region"), item.get("plan")) != (tag, REGION, PLAN):
            raise CreateUnconfirmed("The deployment tag is present on an unexpected Vultr instance.")
        matching[str(UUID(item["id"]))] = item
    if len(matching) > 1:
        raise CreateUnconfirmed("More than one Vultr instance matches the deployment tag.")
    return next(iter(matching), None)


def verify_ipv4(value):
    address = IPv4Address(value)
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise ValueError("Expected a public unicast IPv4 address.")
    return str(address)


def verify_ssh_key(request, key_id):
    key_id = str(UUID(key_id))
    key = request("GET", f"/ssh-keys/{key_id}").get("ssh_key")
    if not isinstance(key, dict) or key.get("id") != key_id or not key.get("ssh_key"):
        raise ValueError("Vultr did not confirm the pinned owner SSH public key.")


def instance_ipv4(request, job_id, instance_id):
    """Return a usable IPv4 only for the exact, booted instance of this claim."""
    instance_id = str(UUID(instance_id))
    tag = "smd-" + str(UUID(job_id))
    item = request("GET", f"/instances/{instance_id}").get("instance")
    if (not isinstance(item, dict) or item.get("id") != instance_id or item.get("label") != tag
            or tag not in (item.get("tags") or []) or item.get("region") != REGION
            or item.get("plan") != PLAN or item.get("os_id") != OS_ID):
        raise CreateUnconfirmed("Vultr did not confirm the exact authorized instance for DNS.")
    if (item.get("status"), item.get("power_status"), item.get("server_status")) != ("active", "running", "ok"):
        return None
    try:
        return verify_ipv4(item.get("main_ip"))
    except ValueError:
        return None


def create_or_reconcile(request, job_id, state, persist, user_data, *, ssh_key_id=None):
    """Return the instance ID for one serialized, owner-approved queue claim.

    State must be dedicated to this claim. Persist must commit atomically before
    returning, and the caller must hold exclusive ownership of the claim. Treat
    CreateUnconfirmed as read-only reconciliation on later attempts.
    """
    tag = "smd-" + str(UUID(str(job_id)))
    if state and state.get("tag") != tag:
        raise ValueError("Vultr state belongs to a different deployment.")
    if state.get("instance_id"):
        return str(UUID(state["instance_id"]))

    instance_id = exact_match(request, tag)
    if instance_id:
        state.update(tag=tag, instance_id=instance_id)
        persist(state)
        return instance_id
    if state.get("create_started"):
        raise CreateUnconfirmed("Vultr has not yet returned a VM for this tag. The create will not be repeated.")

    try:
        if not base64.b64decode(user_data, validate=True):
            raise ValueError("Missing cloud-init bootstrap.")
    except (binascii.Error, TypeError) as exc:
        raise ValueError("Cloud-init bootstrap must be nonempty base64.") from exc
    verify_offer(request)
    if ssh_key_id:
        verify_ssh_key(request, ssh_key_id)
    state.update(tag=tag, create_started=True)
    persist(state)

    body = {
        "region": REGION, "plan": PLAN, "os_id": OS_ID,
        "label": tag, "tags": [tag], "user_data": user_data,
        "block_devices": [], "backups": "disabled", "ddos_protection": False,
    }
    if ssh_key_id:
        body["sshkey_id"] = [str(UUID(ssh_key_id))]
    try:
        response = request("POST", "/instances", body)
        instance_id = str(UUID(response["instance"]["id"]))
    except HTTPError as exc:
        raise CreateUnconfirmed(f"Vultr replied HTTP {exc.code} to the create request. The create will not be repeated.") from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CreateUnconfirmed("Vultr create did not return a usable instance ID. Reconcile by tag before proceeding.") from exc

    state["instance_id"] = instance_id
    persist(state)
    return instance_id
