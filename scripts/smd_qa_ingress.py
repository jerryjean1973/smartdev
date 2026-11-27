"""Render QA ingress routes for a pinned SmartDev deployment."""

from pathlib import Path
import re

from smd_cloudflare_dns import expected_hosts
from smd_vultr_create import verify_ipv4

QA_ZONE = "qa.youkandoit.com"
TRUST_DIRECTORY = Path("/etc/caddy/smd-ca")


def render(claim, ipv4, trust_file):
    """Terminate public HTTPS and validate the upstream with its deployment CA."""
    hosts = expected_hosts(claim, QA_ZONE)
    address = verify_ipv4(ipv4)
    trust_file = Path(trust_file)
    if (trust_file.parent != TRUST_DIRECTORY
            or not re.fullmatch(r"[a-z0-9-]+\.pem", trust_file.name)):
        raise ValueError("Use the approved ingress directory for the deployment CA.")
    return "\n".join(
        f"""{host} {{
    reverse_proxy https://{address} {{
        header_up Host {{http.request.host}}
        transport http {{
            tls_server_name {host}
            tls_trust_pool file {trust_file}
        }}
    }}
}}
"""
        for host in hosts
    )


def issue_certificate(claim):
    """Generate a deployment-local certificate; only this leaf is trusted upstream."""
    from tempfile import TemporaryDirectory
    import subprocess

    hosts = expected_hosts(claim, QA_ZONE)
    with TemporaryDirectory() as temp:
        root = Path(temp)
        cert, key = root / "certificate.pem", root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec",
                        "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "365",
                        "-subj", "/CN=" + hosts[0], "-addext",
                        "subjectAltName=" + ",".join("DNS:" + host for host in hosts),
                        "-keyout", str(key), "-out", str(cert)], capture_output=True, check=True, timeout=15)
        return {"certificate": cert.read_text(), "key": key.read_text()}


def inline_routes(claim, ipv4, certificate):
    from base64 import b64encode
    from ssl import PEM_cert_to_DER_cert

    raw = b64encode(PEM_cert_to_DER_cert(certificate)).decode("ascii")
    return render(claim, ipv4, TRUST_DIRECTORY / "inline.pem").replace(
        "tls_trust_pool file /etc/caddy/smd-ca/inline.pem",
        "tls_trust_pool inline {\n                trust_der " + raw + "\n            }")


def register(claim, ipv4, certificate, config=Path("/opt/bcentrisk-web/Caddyfile")):
    """Validate and add only the approved deployment block; restore on failed reload."""
    import fcntl
    import subprocess
    from uuid import UUID

    task = UUID(claim["id"])
    header = f"# BEGIN SMARTDEV QA {task}"
    end = f"# END SMARTDEV QA {task}"
    desired = header + "\n" + inline_routes(claim, ipv4, certificate) + end + "\n"
    with config.with_name(".smd-qa.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = config.read_text()
        if desired in original:
            return
        if header in original or end in original or any(
                re.search(r"(?m)^\s*" + re.escape(host) + r"\s*\{", original)
                for host in expected_hosts(claim, QA_ZONE)):
            raise ValueError("A QA route already exists outside the approved deployment block.")
        updated = original.rstrip() + "\n\n" + desired
        subprocess.run(["docker", "exec", "-i", "bcentrisk-web-proxy", "caddy", "validate",
                        "--config", "/dev/stdin", "--adapter", "caddyfile"], input=updated,
                       text=True, capture_output=True, timeout=15, check=True)
        if config.read_text() != original:
            raise ValueError("Ingress configuration changed during validation.")
        config.write_text(updated)
        restart = ["docker", "restart", "bcentrisk-web-proxy"]
        try:
            subprocess.run(restart, capture_output=True, timeout=35, check=True)
        except (OSError, subprocess.SubprocessError):
            config.write_text(original)
            subprocess.run(restart, capture_output=True, timeout=35, check=True)
            raise
