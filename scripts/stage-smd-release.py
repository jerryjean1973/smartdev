"""Prepare a verified standalone SMD release for the Command Center API."""

import argparse
from hashlib import file_digest
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit
from uuid import UUID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("bundle_sha256")
    parser.add_argument("revision")
    parser.add_argument("portal_origin")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--dns-zone", required=True)
    parser.add_argument("--ssh-key-id", type=UUID, required=True)
    args = parser.parse_args()
    origin = urlsplit(args.portal_origin)
    if (origin.scheme != "https" or not origin.hostname or origin.username is not None
            or origin.path or origin.query or origin.fragment or re.search(r"\s", args.portal_origin)):
        parser.error("Set the HTTPS Command Center origin without a path.")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", args.dns_zone):
        parser.error("Set the configured DNS zone as a lower-case domain name.")
    bundle = args.bundle.resolve(strict=True)
    directory = args.directory.resolve()
    if not bundle.is_file() or not re.fullmatch(r"[a-f0-9]{64}", args.bundle_sha256):
        parser.error("Set the bundle file and its expected SHA-256.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.revision):
        parser.error("Set a valid release revision.")
    if any(char.isspace() for char in str(directory)):
        parser.error("Use a release directory without whitespace.")
    with bundle.open("rb") as source:
        if file_digest(source, "sha256").hexdigest() != args.bundle_sha256:
            parser.error("Bundle does not match its expected SHA-256.")
    required = {"revision", "compose.yaml", "configure.sh", "configure.py", "images.env",
                "architect-compose.yaml", "apps.py", "projects.py", "enrollment.py",
                "enrollment-compose.yaml", "coder-template/main.tf"}
    try:
        with tarfile.open(bundle) as archive:
            files = {member.name for member in archive if member.isfile()}
            if not required <= files:
                parser.error("Bundle is missing the Architect or selected project deployment files.")
            revision_file = archive.extractfile("revision")
            if revision_file is None or revision_file.read(130).decode("ascii").strip() != args.revision:
                parser.error("Bundle revision differs from the requested release.")
    except (tarfile.TarError, UnicodeError, OSError):
        parser.error("Bundle is not a readable SmartDev release archive.")
    if not bundle.stat().st_mode & 0o004:
        parser.error("Bundle must be readable by the non-root API container. Staging directory remains private.")
    directory.mkdir(mode=0o700)
    artifacts = directory / "artifacts"
    artifacts.mkdir(mode=0o755)
    (artifacts / "bundle.tar").hardlink_to(bundle)
    capability = secrets.token_hex(32)
    prefix = f"{args.portal_origin}/api/smd-release/{capability}"
    installer = artifacts / "installer.sh"
    builder = Path(__file__).with_name("build-smd-installer.py")
    result = subprocess.run([sys.executable, builder, prefix + "/bundle.tar", args.bundle_sha256,
                             args.revision, installer], check=True, capture_output=True, text=True)
    installer.chmod(0o644)
    settings = {
        "SMARTDEV_SMD_RELEASE_HOST_DIRECTORY": artifacts,
        "SMARTDEV_SMD_RELEASE_DOWNLOAD_TOKEN": capability,
        "SMARTDEV_SMD_INSTALLER_URL": prefix + "/installer.sh",
        "SMARTDEV_SMD_INSTALLER_SHA256": result.stdout.strip(),
        "SMARTDEV_SMD_RELEASE_REVISION": args.revision,
        "SMARTDEV_SMD_DNS_ZONE": args.dns_zone,
        "SMARTDEV_SMD_VULTR_SSH_KEY_ID": args.ssh_key_id,
    }
    env = directory / "release.env"
    env.touch(mode=0o600, exist_ok=False)
    env.write_text("".join(f"{key}={value}\n" for key, value in settings.items()))
    print(f"Staged {args.revision}. Installer SHA-256 {settings['SMARTDEV_SMD_INSTALLER_SHA256']}.")


if __name__ == "__main__":
    main()
