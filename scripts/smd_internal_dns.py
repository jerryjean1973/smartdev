"""Register pinned deployment hostnames with an authorized internal DNS server."""

from dataclasses import dataclass
from ipaddress import ip_address
import os
from pathlib import Path
import re
import subprocess
from uuid import UUID

from smd_cloudflare_dns import DNSBlocked, expected_hosts, validate_zone
from smd_vultr_create import verify_ipv4


@dataclass(frozen=True)
class DeploymentDNS:
    zone: str
    server: str
    key_file: Path

    @classmethod
    def configured(cls):
        zone = validate_zone(os.getenv("SMARTDEV_SMD_DNS_ZONE", ""))
        try:
            server = str(ip_address(os.getenv("SMARTDEV_SMD_INTERNAL_DNS_SERVER", "")))
        except ValueError:
            raise DNSBlocked("Configure the authorized internal DNS server address.") from None
        key = Path(os.getenv("SMARTDEV_SMD_INTERNAL_DNS_KEY_FILE", ""))
        if not key.is_file() or not os.access(key, os.R_OK):
            raise DNSBlocked("Configure a readable internal DNS update key.")
        return cls(zone, server, key)

    def query(self, hostname, kind):
        try:
            result = subprocess.run(
                ["dig", "@" + self.server, hostname + ".", kind, "+tcp", "+norecurse",
                 "+noall", "+comments", "+answer", "+time=3", "+tries=1"],
                capture_output=True, text=True, timeout=8, check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            raise DNSBlocked("The internal DNS authority did not answer.") from None
        status = re.search(r"status: (NOERROR|NXDOMAIN),", result)
        flags = re.search(r";; flags: ([^;]+);", result)
        if not status or not flags or "aa" not in flags[1].split():
            raise DNSBlocked("The internal DNS response was not authoritative.")
        values = []
        for line in result.splitlines():
            if not line or line.startswith(";"):
                continue
            row = line.split(maxsplit=4)
            if len(row) != 5 or row[0].lower() != hostname + "." or row[2] != "IN" or row[3] != kind:
                raise DNSBlocked(f"Existing internal DNS for {hostname} conflicts with the deployment.")
            values.append(row[4])
        return status[1], values

    def existing(self, hostname, marker, ipv4=None):
        status, values = self.query(hostname, "A")
        if status == "NXDOMAIN" and not values:
            return False
        _, records = self.query(hostname, "TXT")
        if len(values) == 1 and records == [f'"{marker}"'] and (ipv4 is None or values == [ipv4]):
            return True
        raise DNSBlocked(f"Existing internal DNS for {hostname} conflicts with the deployment.")

    def preflight(self, claim):
        hosts = expected_hosts(claim, self.zone)
        if self.query(self.zone, "SOA")[0] != "NOERROR":
            raise DNSBlocked("The configured internal DNS zone was not found.")
        marker = "smartdev:smd:" + str(UUID(claim["id"]))
        for hostname in hosts:
            self.existing(hostname, marker)

    def assign(self, claim, ipv4, state, persist):
        hosts = expected_hosts(claim, self.zone)
        ip = verify_ipv4(ipv4)
        marker = "smartdev:smd:" + str(UUID(claim["id"]))
        existing = {host: self.existing(host, marker, ip) for host in hosts}
        for host, present in existing.items():
            if present:
                state[host] = {"verified": True}
                persist()
                continue
            if host in state:
                raise DNSBlocked(f"Internal DNS creation for {host} is unconfirmed. Reconcile by reading DNS.")
            state[host] = {"create_started": True}
            persist()
            command = (f'server {self.server}\nzone {self.zone}.\nprereq nxdomain {host}.\n'
                       f'update add {host}. 300 A {ip}\nupdate add {host}. 300 TXT "{marker}"\nsend\n')
            try:
                subprocess.run(["nsupdate", "-v", "-k", str(self.key_file)],
                               input=command, text=True, capture_output=True, check=True, timeout=12)
            except (OSError, subprocess.SubprocessError):
                raise DNSBlocked(f"Internal DNS update for {host} was unconfirmed. Reconcile by reading DNS.") from None
            if not self.existing(host, marker, ip):
                raise DNSBlocked(f"Internal DNS did not verify {host}.")
            state[host] = {"verified": True}
            persist()
