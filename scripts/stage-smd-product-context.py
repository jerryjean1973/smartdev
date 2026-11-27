"""Publish a sealed product context and bind it to a project and wrapper commit."""
import argparse
import gzip
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from uuid import UUID


def publish(wrapper, context, project_id, destination):
    revision = subprocess.check_output(["git", "-C", wrapper, "rev-parse", "HEAD"], text=True).strip()
    link = subprocess.check_output(["git", "-C", wrapper, "ls-tree", revision,
                                    "product-distribution/canonical-source"], text=True).split()
    distribution = json.loads(subprocess.check_output(
        ["git", "-C", wrapper, "show", revision + ":product-distribution/distribution.json"], text=True))
    manifest_file = context / "product-package.json"
    if manifest_file.is_symlink() or not manifest_file.is_file():
        raise ValueError("A verified product-package.json is required.")
    manifest = json.loads(manifest_file.read_text())
    if (not re.fullmatch(r"[0-9a-f]{40}", revision) or manifest["schema_version"] != "product-package.v1"
            or link[:3] != ["160000", "commit", manifest["source_revision"]]
            or distribution["source_revision"] != manifest["source_revision"]
            or distribution["product"] != manifest["product"]):
        raise ValueError("Package source and wrapper pin differ.")
    expected = set(manifest["files"]) | {"product-package.json"}
    paths = {}
    for path in context.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Unexpected file type in product context.")
        if path.is_file():
            paths[path.relative_to(context).as_posix()] = path
    if set(paths) != expected or "Dockerfile.products" not in paths:
        raise ValueError("Product context file set differs from its seal.")
    destination.mkdir(mode=0o750, parents=True, exist_ok=True)
    store = destination / str(project_id)
    store.mkdir(mode=0o750, exist_ok=True)
    with TemporaryDirectory(dir=store) as staging:
        pending = Path(staging) / "context.tar.gz"
        with pending.open("xb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w|") as tar:
                for name in sorted(paths):
                    with paths[name].open("rb") as source:
                        if name in manifest["files"] and file_digest(source, "sha256").hexdigest() != manifest["files"][name]:
                            raise ValueError("Product context bytes differ from its seal.")
                        source.seek(0)
                        info = tarfile.TarInfo(name)
                        metadata = os.fstat(source.fileno())
                        info.size = metadata.st_size
                        info.mode = 0o755 if metadata.st_mode & stat.S_IXUSR else 0o644
                        tar.addfile(info, source)
        with pending.open("rb") as source:
            digest = file_digest(source, "sha256").hexdigest()
        pending.chmod(0o440)
        final = store / (digest + ".tar.gz")
        pending.replace(final)
        pointer = Path(staging) / "digest"
        pointer.write_text(digest + "\n")
        pointer.chmod(0o440)
        pointer.replace(store / (revision + ".sha256"))
    print(json.dumps({"project_id": str(project_id), "wrapper": revision, "canonical": manifest["source_revision"],
                      "product": manifest["product"], "engine": manifest["engine"], "sha256": digest}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wrapper", type=Path)
    parser.add_argument("context", type=Path)
    parser.add_argument("project_id", type=UUID)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    publish(args.wrapper, args.context, args.project_id, args.destination)
