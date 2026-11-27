"""Restore reviewed public asset aliases in the pinned BO-01 QA build checkout."""
import argparse
from pathlib import Path
import subprocess

CANONICAL = "8b1fedcc6dd41949da4e3abbc99f7f05b18dc694"
ALIASES = {
    "BC_logo_v4_1780606241148.png": "bcentrisk-logo.png",
    "cyber_1780155396169.jpg": "cyber.jpg",
    "detect_1780155396177.jpg": "detect.jpg",
    "emerging_1780155396173.jpg": "emerging.jpg",
    "enterprise_1780155396179.jpg": "enterprise.jpg",
    "image_1775714238092.webp": "about.webp",
    "investement_1780155396178.jpg": "investment.jpg",
    "landing_logo.png_1780156678530.jpg": "landing-logo.jpg",
    "opportunities_1780155396171.jpg": "opportunities.jpg",
    "outcome_predict_1780155396175.jpg": "outcome-predict.jpg",
    "pharma_1780155396168.jpg": "pharma.jpg",
    "predict_1780155396172.jpg": "predict.jpg",
    "propagate_1780155396176.jpg": "propagate.jpg",
    "recom_1780155396174.jpg": "recommendations.jpg",
    "signals_1780155396177.jpg": "signals.jpg",
    "venture_1780155396178.jpg": "venture.jpg",
}


def prepare(canonical):
    repo = ["git", "-C", canonical]
    if subprocess.check_output([*repo, "rev-parse", "HEAD"], text=True).strip() != CANONICAL:
        raise ValueError("These legacy aliases are specific to the reviewed VG QA canonical revision.")
    target = canonical / "attached_assets"
    if target.is_symlink():
        raise ValueError("Expected a private directory in the isolated build checkout.")
    target.mkdir(mode=0o700, exist_ok=True)
    for name, tracked in ALIASES.items():
        content = subprocess.check_output([*repo, "show", f"{CANONICAL}:client/src/assets/website/{tracked}"])
        path = target / name
        if path.is_symlink():
            raise ValueError(f"Unexpected symbolic link at {name}.")
        try:
            with path.open("xb") as output:
                output.write(content)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != content:
                raise ValueError(f"The existing input {name} differs from its pinned public asset.") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical_checkout", type=Path)
    prepare(parser.parse_args().canonical_checkout)
