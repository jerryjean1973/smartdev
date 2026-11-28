"""Initialize durable, project-specific configuration on the destination."""
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from uuid import UUID

from projects import load_manifest, stage_projects
from enrollment import prepare


def ipv4_source():
    route = subprocess.check_output(["ip", "-4", "route", "get", "1.1.1.1"], text=True)
    return str(ipaddress.IPv4Address(route.split()[route.split().index("src") + 1]))


def write_secure(path, contents):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(contents)
    temporary.replace(path)


def main():
    os.umask(0o077)
    project = str(UUID(sys.argv[1]))
    root = Path.cwd().parent.parent
    if root.name != project:
        raise ValueError("Installer release must be inside the project data directory")
    data = root / "data"
    for directory in (data, data / "vault-client", data / "vault-operator"):
        directory.mkdir(mode=0o700, exist_ok=True)
    # API runs as UID 65532. The directory is only exposed through its read-only mount.
    (data / "vault-client").chmod(0o755)
    selected = load_manifest(Path(sys.argv[2])) if len(sys.argv) > 2 else []
    if selected:
        stage_projects(data, selected)
    password_path = data / "owner-password"
    if not password_path.exists():
        write_secure(password_path, secrets.token_urlsafe(36) + "\n")
    password = password_path.read_text().strip()
    secrets_path = data / "secrets.env"
    if not secrets_path.exists():
        salt = secrets.token_bytes(16)
        password_hash = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
        entries = {
            "SMD_OWNER_PASSWORD_HASH": f"{salt.hex()}:{password_hash}",
            "SMD_MEMORY_KEY": secrets.token_hex(32),
            "SMD_SESSION_KEY": secrets.token_hex(32),
            "SMD_DATABASE_PASSWORD": secrets.token_hex(32),
            "SMD_CODER_DB_PASSWORD": secrets.token_hex(32),
        }
        write_secure(secrets_path, "".join(f"{key}={value}\n" for key, value in entries.items()))
    host_path = data / "public-host"
    host = host_path.read_text().strip() if host_path.exists() else ipv4_source()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
        raise ValueError("public-host must be an IPv4 address or DNS name")
    private_tls = not (data / "public-host").exists() or (data / "private-tls").exists()
    # Keep Coder on a distinct origin. A dedicated hostname is optional on a fresh host.
    coder_host_path = data / "coder-host"
    coder_host = coder_host_path.read_text().strip() if coder_host_path.exists() else "coder.invalid"
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", coder_host):
        raise ValueError("coder-host must be an IPv4 address or DNS name")
    if coder_host.lower().rstrip(".") == host.lower().rstrip("."):
        raise ValueError("Coder and Command Center must use distinct hostnames")
    tls = "tls internal" if private_tls else ""
    upstream_tls = data / "edge-tls"
    if (upstream_tls / "certificate.pem").is_file() and (upstream_tls / "key.pem").is_file():
        tls = "tls /run/smd-edge-tls/certificate.pem /run/smd-edge-tls/key.pem"
    upstream_tls.mkdir(mode=0o700, exist_ok=True)
    edge_config = f"""{{
    default_sni {host}
}}
{host}:443 {{
    {tls}
    handle_path /api/* {{
        reverse_proxy api:8000
    }}
    handle {{
        reverse_proxy portal:3000
    }}
}}
"""
    if selected and not coder_host_path.exists():
        raise ValueError("Coder enrollment requires its distinct deployment hostname")
    if selected:
        if any(project.get("hostname") in {host.lower(), coder_host.lower()} for project in selected):
            raise ValueError("Selected apps require hostnames distinct from SmartDev and Coder")
        from apps import prepare as prepare_apps
        app_routes = prepare_apps(data, selected)
        if tls:
            app_routes = app_routes.replace(":443 {\n", ":443 {\n    " + tls + "\n")
        edge_config += app_routes
    if coder_host_path.exists():
        coder_config = f"""{coder_host}:443 {{
    {tls}
    @session path /__smartdev/session
    reverse_proxy @session api:8000
    reverse_proxy coder:7080
}}
"""
        if selected and not (data / "coder-owner-ready").exists():
            write_secure(data / "Caddyfile-enrolled", edge_config + coder_config)
            (data / "Caddyfile-enrolled").chmod(0o644)
        else:
            edge_config += coder_config
    write_secure(data / "Caddyfile", edge_config)
    (data / "Caddyfile").chmod(0o644)
    write_secure(Path(".env"), Path("images.env").read_text() + secrets_path.read_text() + f"""SMD_DATA_DIR={data}
SMD_PUBLIC_HOST={host}
SMD_PORTAL_ORIGIN=https://{host}
SMD_CODER_ORIGIN=https://{coder_host}
SMD_ENVIRONMENT={next((value.upper() for value in ('qa','preprod','prod','dev') if value in host.lower().split('.')), 'DEV')}
""")
    if selected:
        prepare(data, selected)


if __name__ == "__main__":
    main()
