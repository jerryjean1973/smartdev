"""Install the signed release's workspace browser and account services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

def install(data, workspace_names):
    source = Path("workspace-tools")
    install_dir = Path("/opt/smartdev-workspace-tools")
    system = Path("/etc/systemd/system")
    install_dir.mkdir(parents=True, exist_ok=True)
    install_dir.chmod(0o755)
    archive = source / "chromium.tar.gz"
    with archive.open("rb") as stream:
        import hashlib
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    runtime = install_dir / ("chromium-" + digest)
    if not runtime.is_dir():
        with tempfile.TemporaryDirectory(dir=install_dir) as tmp:
            stage = Path(tmp) / "runtime"
            stage.mkdir()
            with tarfile.open(archive, "r:gz") as package:
                for member in package.getmembers():
                    if not member.isfile() or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                        raise ValueError("Invalid workspace browser release entry")
                package.extractall(stage, filter="data")
            for folder in (path for path in stage.rglob("*") if path.is_dir()):
                folder.chmod(0o755)
            stage.chmod(0o755)
            stage.rename(runtime)
    for item in source.iterdir():
        if item.name == archive.name:
            continue
        target = (system if item.suffix in (".timer",".service") else install_dir) / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
            for client in target.iterdir():
                client.chmod(0o644 if client.suffix == ".cjs" else 0o755)
        else:
            shutil.copyfile(item, target)
            target.chmod(0o755 if target.suffix == ".py" else 0o644)
    settings = Path("/etc/smartdev/workspace-tools.json")
    settings.parent.mkdir(mode=0o755, exist_ok=True)
    config = json.loads(settings.read_text()) if settings.exists() else {}
    coder_origin = "https://" + (data / "coder-host").read_text().strip()
    accounts = config.setdefault("workspace_coders", {})
    for name in workspace_names:
        accounts[name] = {"coder_api":coder_origin, "coder_origin":coder_origin,
                          "coder_token_json":str(data/"coder-session/owner.json")}
    config["browser_runtime"] = str(runtime)
    with tempfile.NamedTemporaryFile(dir=settings.parent, mode="w", delete=False) as new:
        json.dump(config, new)
        os.fchmod(new.fileno(),0o600)
    os.replace(new.name, settings)
    subprocess.run(["systemctl","daemon-reload"],check=True)
    subprocess.run(["systemctl","enable","--now","smartdev-workspace-tools.service","smartdev-workspace-accounts.timer","smartdev-workspace-health.timer"],check=True)
    subprocess.run(["systemctl","restart","smartdev-workspace-tools.service"],check=True)
    subprocess.run(["systemctl","start","smartdev-workspace-accounts.service"],check=True)
