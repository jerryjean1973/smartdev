"""Validate selected projects and stage their exact Git trees."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from tempfile import TemporaryDirectory
from uuid import UUID

REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")
REVISION = re.compile(r"[0-9a-f]{40}")
HOSTNAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def load_manifest(path):
    content = json.loads(path.read_text())
    projects = content.get("projects") if isinstance(content, dict) else None
    if not isinstance(projects, list) or not 1 <= len(projects) <= 20:
        raise ValueError("Deployment manifest must include 1 to 20 projects")
    selected = []
    seen_ids = set()
    seen_hosts = set()
    for item in projects:
        id_, repository, revision, name, runtime = (item[key] for key in
                                                   ("id", "repository", "revision", "name", "runtime"))
        if (str(UUID(id_)) != id_ or id_ in seen_ids or not REPOSITORY.fullmatch(repository)
                or repository.split("/")[1] in {".", ".."} or not REVISION.fullmatch(revision)
                or not isinstance(name, str) or not name.strip() or len(name) > 80
                or runtime not in ("node", "python", "other")):
            raise ValueError("Invalid selected project or Git revision")
        entry = {"id": id_, "repository": repository, "revision": revision, "name": name, "runtime": runtime}
        if "verify_installation" in item:
            if item["verify_installation"] is not True:
                raise ValueError("Invalid installation verification mode.")
            entry["verify_installation"] = True
        if "build_context_sha256" in item:
            digest = item["build_context_sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Invalid approved product context SHA-256.")
            entry["build_context_sha256"] = digest
        if item.get("tenant_setup") is not None:
            if item["tenant_setup"] is not True or "build_context_sha256" not in entry:
                raise ValueError("Tenant setup must refer to a pinned product package.")
            entry["tenant_setup"] = True
        hostname = item.get("hostname")
        if hostname:
            if not HOSTNAME.fullmatch(hostname) or len(hostname) > 253 or hostname in seen_hosts:
                raise ValueError("Invalid or duplicate project hostname")
            seen_hosts.add(hostname)
            entry["hostname"] = hostname
        preview=item.get("preview_hostname")
        if preview:
            if not hostname or not hostname.startswith("app-") or preview!="dev-"+hostname[4:] or preview in seen_hosts:
                raise ValueError("Invalid managed DEV hostname")
            entry["preview_hostname"]=preview
            seen_hosts.add(preview)
        selected.append(entry)
        seen_ids.add(id_)
    return selected


def git(directory, *arguments, askpass=None, token_file=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0")
    if askpass:
        env.update(GIT_ASKPASS=str(askpass), SMD_GIT_TOKEN_FILE=str(token_file))
    try:
        return subprocess.run(
            ["git", "-C", directory, "-c", "core.hooksPath=/dev/null",
             "-c", "credential.helper=", "-c", "protocol.ext.allow=never",
             "-c", "http.followRedirects=false", *arguments],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=300, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as failure:
        raise ValueError(f"Git {arguments[0]} failed while staging an approved project") from failure


def stage_projects(data, selected):
    if not shutil.which("git"):
        raise RuntimeError("Install Git before staging selected projects.")
    root = data / "checkouts"
    root.mkdir(mode=0o700, exist_ok=True)
    for project in selected:
        project_root = root / project["id"]
        project_root.mkdir(mode=0o700, exist_ok=True)
        revision = project["revision"]
        approved = project_root / revision
        seeds = data / "workspace-seeds" / project["id"]
        seeds.mkdir(parents=True, mode=0o700, exist_ok=True)
        seed = seeds / f"{revision}.bundle"
        repository = f"https://github.com/{project['repository']}.git"
        if approved.exists():
            if git(approved, "rev-parse", "HEAD") != revision:
                raise ValueError("Staged checkout no longer matches the approved revision")
            if not seed.exists():
                export_seed(approved, seed)
            continue
        bundle = Path("/run/smartdev-git") / data.parent.name / f"{project['id']}.bundle"
        if not bundle.is_file():
            bundle = data / "git-bundles" / f"{project['id']}.bundle"
        source = bundle if bundle.is_file() else repository
        with TemporaryDirectory(dir=project_root) as temporary:
            stage = Path(temporary) / "repository"
            stage.mkdir()
            git(stage, "init", "--quiet")
            auth = {} if bundle.is_file() else git_auth(Path(temporary), data.parent.name, project["id"])
            try:
                git(stage, "fetch", "--no-tags", "--", str(source), revision, **auth)
            except ValueError:
                if not bundle.is_file():
                    raise ValueError(f"Cannot fetch approved revision for {project['repository']}. "
                                     "Check repository access or provide its authorized Git bundle.") from None
                # A Git bundle advertises references rather than individual SHA arguments.
                git(stage, "fetch", "--no-tags", "--", str(source), "refs/heads/*:refs/heads/*")
            git(stage, "checkout", "--quiet", "--detach", revision)
            if git(stage, "rev-parse", "HEAD") != revision:
                raise ValueError("Fetched checkout does not match the approved revision")
            git(stage, "remote", "add", "origin", repository)
            # Workspaces clone this host-owned immutable seed through a read-only bind mount.
            make_readable(stage)
            stage.replace(approved)
            export_seed(approved, seed)


def export_seed(checkout, seed):
    with TemporaryDirectory(dir=seed.parent) as temporary:
        bundle = Path(temporary) / "selected.bundle"
        git(checkout, "bundle", "create", str(bundle), "HEAD")
        bundle.chmod(0o444)
        bundle.replace(seed)


def git_auth(temporary, deployment_id, project_id):
    token_file = Path("/run/smartdev-git") / deployment_id / f"{project_id}.token"
    if not token_file.exists():
        return {}
    token_stat = token_file.lstat()
    if (not stat.S_ISREG(token_stat.st_mode) or token_stat.st_uid != 0
            or stat.S_IMODE(token_stat.st_mode) != 0o600):
        raise ValueError("The ephemeral Git credential must be a root-owned regular 0600 file")
    askpass = temporary / "git-askpass"
    askpass.write_text("""#!/bin/sh
case "$1" in
    Username*) printf '%s\\n' x-access-token ;;
    Password*) cat -- "$SMD_GIT_TOKEN_FILE" ;;
    *) exit 1 ;;
esac
""")
    askpass.chmod(0o700)
    return {"askpass": askpass, "token_file": token_file}


def make_readable(tree):
    for parent, directories, files in os.walk(tree):
        Path(parent).chmod(0o755)
        for name in files:
            path = Path(parent) / name
            if not path.is_symlink():
                path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
