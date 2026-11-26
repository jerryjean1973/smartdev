"""Build a customer-operated bundle from immutable, signed release artifacts."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from installation_compose import compose

ROOT = Path(__file__).parent
ARTIFACTS = {
    "installer": "installer.sh",
    "coder_template": "coder-template.tar",
}
FILES = {
    "postgres.env.example": "POSTGRES_USER=\nPOSTGRES_PASSWORD=\nPOSTGRES_DB=\n",
    "api.env.example": (
        "DATABASE_URL=\nREDIS_URL=\nSESSION_KEY=\nMEMORY_ACCESS_KEY=\n"
        "OWNER_PASSWORD_HASH=\nCODER_PUBLIC_URL=\nOPENBAO_ADDR=http://openbao:8200\n"
    ),
    "worker.env.example": (
        "DATABASE_URL=\nREDIS_URL=\nCODER_API_URL=http://coder:7080\n"
        "CODER_PUBLIC_URL=\nCODER_TOKEN=\nCODER_ORGANIZATION_ID=\nCODER_OWNER=\nCODER_TEMPLATE_ID=\n"
    ),
    "coder.env.example": "CODER_PG_CONNECTION_URL=\nCODER_ACCESS_URL=\n",
    "redis.conf.example": "appendonly yes\nrequirepass REPLACE_WITH_CUSTOMER_SECRET\n",
    "openbao.hcl.example": (
        'storage "file" { path = "/openbao/data" }\n'
        'listener "tcp" { address = "0.0.0.0:8200"\n tls_disable = true }\n'
    ),
}
INSTRUCTIONS = """# SmartDev Docker installation

Obtain the release and central MIG public keys and your enrollment ID through
your authenticated Command Center enrollment. Run the verifier from a trusted
Command Center distribution and point it at the extracted bundle. Use Python 3 with cryptography
installed from your trusted package source. Before executing the installer, run:

    python3 verify_bundle.py RELEASE_PUBLIC_KEY_HEX MIG_PUBLIC_KEY_HEX ENROLLMENT_UUID /path/to/extracted-bundle

Use the existing enrolled installer to initialize the services. Create credentials
locally, restrict file permissions, initialize and unseal OpenBao, and supply
a scoped vault token. Use the example files as templates and set
SMD_CREDENTIALS_DIR to the absolute path of the host-local credentials directory.
Point customer TLS
ingress to the portal and route /api to the API with that prefix stripped.
Keep Postgres, Redis and the vault on private networks.

For limited connectivity, transfer the pinned images through your organization's
trusted registry or verified Docker image transfer process before bootstrapping.
Verify that locally imported images match the signed repository digests and use
Compose with pull disabled. Keep the installation's volumes, credentials and
image cache on customer-controlled storage. Install while the MIG grant is
valid. Running local services can use their cached images and local state.
Operations that require a fresh central MIG decision need central connectivity.
"""


def bundle(handoff, artifact_root):
    release = handoff["release"]
    archive = BytesIO()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
        for artifact, filename in ARTIFACTS.items():
            digest = release[f"{artifact}_sha256"]
            path = artifact_root / digest
            with path.open("rb") as stream:
                data = stream.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024 or sha256(data).hexdigest() != digest:
                raise ValueError("Invalid release artifact")
            output.writestr(filename, data)
        output.writestr("handoff.json", json.dumps({
            key: handoff[key] for key in ("release", "release_signature", "grant", "grant_signature", "plan")
        }))
        output.writestr("compose.json", json.dumps(compose(handoff["plan"]), indent=2))
        output.writestr("README.md", INSTRUCTIONS)
        for name, template in FILES.items():
            output.writestr("templates/" + name, template)
        for filename in ("verify_bundle.py", "installation_release.py", "installation_compose.py"):
            output.write(ROOT / filename, filename)
    return archive.getvalue()
