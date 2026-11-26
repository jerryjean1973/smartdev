"""Verify a Docker installation bundle using independently trusted enrollment data."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID

from installation_compose import compose
from installation_release import verify, verify_operation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_public_key")
    parser.add_argument("mig_public_key")
    parser.add_argument("enrollment_id", type=UUID)
    parser.add_argument("bundle_directory", type=Path)
    args = parser.parse_args()
    root = args.bundle_directory
    handoff = json.loads((root / "handoff.json").read_text())
    plan, release = handoff["plan"], handoff["release"]
    verify(args.release_public_key, handoff["release_signature"], "release", release)
    verify_operation(handoff["grant"], handoff["grant_signature"], args.mig_public_key,
                     plan, str(args.enrollment_id), None)
    if plan["destination"]["kind"] != "docker" or handoff["grant"]["operation"] != "install":
        raise ValueError("Expected an install grant for Docker")
    if (plan["images"] != release["images"]
            or compose(plan) != json.loads((root / "compose.json").read_text())):
        raise ValueError("Compose does not match the signed release and plan")
    for artifact, filename in (("installer", "installer.sh"), ("coder_template", "coder-template.tar")):
        with (root / filename).open("rb") as stream:
            if sha256(stream.read()).hexdigest() != release[f"{artifact}_sha256"]:
                raise ValueError(f"Invalid {artifact} digest")
    print("Release, installation grant, Compose and artifacts verified.")


if __name__ == "__main__":
    main()
