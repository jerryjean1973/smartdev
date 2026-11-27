"""Create and reconcile only DNS records authorized by a pinned SMD claim."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from uuid import UUID

from smd_vultr_create import NoRedirect, verify_ipv4

API = "https://api.cloudflare.com/client/v4"


class DNSBlocked(ValueError):
    """DNS state or authorization could not be confirmed."""


def validate_zone(zone):
    segments = zone.split(".")
    if (len(segments) < 2 or len(zone) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in segments)):
        raise DNSBlocked("The configured DNS zone is not a valid canonical hostname.")
    return zone


def expected_hosts(claim, zone):
    if claim.get("dns_zone") != validate_zone(zone):
        raise DNSBlocked("The deployment is not pinned to the runner's authorized DNS zone.")
    task = UUID(claim["id"]).hex
    portal, coder = f"smd-{task}.{zone}", f"coder-{task}.{zone}"
    if claim.get("deployment_hostname") != portal or claim.get("coder_hostname") != coder:
        raise DNSBlocked("The portal or Coder hostname differs from the pinned deployment hostname.")
    hosts = [portal, coder]
    projects = claim.get("projects")
    if not isinstance(projects, list) or not projects:
        raise DNSBlocked("The deployment has no pinned project manifest.")
    for project in projects:
        project_key = UUID(project["id"]).hex
        hostname = f"app-{project_key[:12]}-{task[:12]}.{zone}"
        if project.get("hostname") != hostname:
            raise DNSBlocked("A project application hostname differs from the pinned deployment hostname.")
        hosts.append(hostname)
    if len(hosts) != len(set(hosts)) or any(len(host) > 253 for host in hosts):
        raise DNSBlocked("The pinned deployment hostnames contain duplicate or oversized names.")
    return hosts


def make_request(token):
    if not token or any(char.isspace() for char in token):
        raise DNSBlocked("The Cloudflare DNS token file is empty or malformed.")
    opener = build_opener(NoRedirect)

    def request(method, path, body=None):
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            query = Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                            headers=headers, method=method)
            with opener.open(query, timeout=15) as response:
                result = json.load(response)
        except HTTPError as error:
            raise DNSBlocked(f"Cloudflare returned HTTP {error.code} to the DNS {method} request.") from error
        except (OSError, ValueError) as error:
            raise DNSBlocked(f"Cloudflare did not return a usable response to the DNS {method} request.") from error
        if not isinstance(result, dict) or result.get("success") is not True:
            raise DNSBlocked(f"Cloudflare did not confirm the DNS {method} request.")
        return result

    return request


@dataclass(frozen=True)
class DeploymentDNS:
    request: object
    zone: str
    zone_id: str

    @classmethod
    def configured(cls):
        zone = validate_zone(os.getenv("SMARTDEV_SMD_DNS_ZONE", ""))
        zone_id = os.getenv("SMARTDEV_SMD_DNS_ZONE_ID", "")
        if not re.fullmatch(r"[a-f0-9]{32}", zone_id):
            raise DNSBlocked("The runner's Cloudflare zone ID is missing or malformed.")
        token_file = os.getenv("SMARTDEV_SMD_DNS_TOKEN_FILE")
        if not token_file:
            raise DNSBlocked("The runner's Cloudflare DNS token file is not configured.")
        try:
            token = Path(token_file).read_text().strip()
        except OSError as error:
            raise DNSBlocked("The runner's Cloudflare DNS token file is unreadable.") from error
        return cls(make_request(token), zone, zone_id)

    @property
    def records_path(self):
        return f"/zones/{self.zone_id}/dns_records"

    def verify_zone(self):
        result = self.request("GET", f"/zones/{self.zone_id}").get("result")
        if (not isinstance(result, dict) or result.get("id") != self.zone_id
                or result.get("name") != self.zone or result.get("status") != "active"):
            raise DNSBlocked("Cloudflare did not confirm the configured active DNS zone.")

    def records(self, hostname):
        # Filter by name and recheck every returned name before trusting the result.
        for page in range(1, 51):
            parameters = urlencode({"name.exact": hostname, "page": page, "per_page": 100})
            response = self.request("GET", f"{self.records_path}?{parameters}")
            rows, info = response.get("result"), response.get("result_info")
            if not isinstance(rows, list) or not isinstance(info, dict):
                raise DNSBlocked("Cloudflare returned an incomplete DNS inventory.")
            pages = info.get("total_pages")
            if type(pages) is not int or not 0 <= pages <= 50 or info.get("page") != page:
                raise DNSBlocked("Cloudflare returned unusable DNS inventory pagination.")
            for row in rows:
                if not isinstance(row, dict) or row.get("name") != hostname or not isinstance(row.get("type"), str):
                    raise DNSBlocked("Cloudflare returned an unexpected DNS record.")
                yield row
            if page >= pages:
                return
        raise DNSBlocked("Cloudflare DNS inventory exceeded the verification limit.")

    def existing_record(self, hostname, marker, ipv4=None):
        conflicts = [row for row in self.records(hostname)
                     if row["type"] in {"A", "AAAA", "CNAME", "NS", "HTTPS", "SVCB", "DNAME"}]
        if not conflicts:
            return None
        if len(conflicts) == 1:
            row = conflicts[0]
            if (row["type"] == "A" and row.get("comment") == marker and row.get("proxied") is False
                    and (ipv4 is None or row.get("content") == ipv4)
                    and re.fullmatch(r"[a-f0-9]{32}", row.get("id") or "")):
                return row["id"]
        raise DNSBlocked(f"Existing DNS for {hostname} conflicts with the pinned deployment.")

    def preflight(self, claim):
        hosts = expected_hosts(claim, self.zone)
        self.verify_zone()
        marker = "smartdev:smd:" + str(UUID(claim["id"]))
        for hostname in hosts:
            self.existing_record(hostname, marker)

    def assign(self, claim, ipv4, state, persist):
        hosts = expected_hosts(claim, self.zone)
        ipv4 = verify_ipv4(ipv4)
        self.verify_zone()
        marker = "smartdev:smd:" + str(UUID(claim["id"]))
        # Read every host before changing any of them, including on resumes.
        existing = {
            hostname: self.existing_record(hostname, marker, ipv4) for hostname in hosts
        }
        for hostname, record_id in existing.items():
            attempt = state.get(hostname)
            if record_id:
                if attempt and attempt.get("record_id") not in (None, record_id):
                    raise DNSBlocked(f"DNS ownership changed for {hostname} after it was recorded.")
                state[hostname] = {"record_id": record_id}
                persist()
                continue
            if attempt:
                raise DNSBlocked(f"DNS creation for {hostname} is unconfirmed. It will only be reconciled by read.")
            state[hostname] = {"create_started": True}
            persist()
            result = self.request("POST", self.records_path, {
                "name": hostname, "type": "A", "content": ipv4, "proxied": False,
                "ttl": 300, "comment": marker,
            }).get("result")
            if (not isinstance(result, dict) or result.get("name") != hostname
                    or result.get("type") != "A" or result.get("content") != ipv4
                    or result.get("comment") != marker or result.get("proxied") is not False
                    or not re.fullmatch(r"[a-f0-9]{32}", result.get("id") or "")):
                raise DNSBlocked(f"Cloudflare did not confirm DNS ownership for {hostname}.")
            state[hostname] = {"record_id": result["id"]}
            persist()
