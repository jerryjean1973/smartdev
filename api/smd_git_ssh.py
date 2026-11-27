"""Read an approved GitHub repository using its existing SSH deploy key."""

import os
from pathlib import Path
import re
import shlex
import subprocess
from tempfile import TemporaryDirectory
from uuid import UUID

from core import REPOSITORY

SHA = re.compile(r"[a-f0-9]{40}")


def key_for(project_id):
    directory = os.getenv("SMARTDEV_SMD_GIT_KEY_DIRECTORY", "/run/smd-git-keys")
    identity = Path(directory) / str(UUID(str(project_id)))
    return identity if identity.is_file() and not identity.is_symlink() else None


def git(project, key, working_path, *args, timeout=30):
    repository = project["repository"]
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid approved GitHub repository.")
    ssh = shlex.join(["ssh", "-F", "/dev/null", "-i", str(key), "-o", "IdentitiesOnly=yes",
                       "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o",
                       "UserKnownHostsFile=" + str(Path(__file__).with_name("github_known_hosts")),
                       "-o", "ConnectTimeout=8"])
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_SSH_COMMAND=ssh, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL="/dev/null")
    command = ["git", "-C", str(working_path), "-c", "core.hooksPath=/dev/null",
               "-c", "protocol.ext.allow=never", *args]
    result = subprocess.run(command, env=env, capture_output=True, timeout=timeout, text=True)
    if result.returncode:
        raise ValueError("The configured SSH deploy key could not read the approved repository.")
    return result.stdout.strip()


def pin(project, key):
    repository = "git@github.com:" + project["repository"] + ".git"
    requested = project["revision"]
    with TemporaryDirectory() as scratch:
        if requested is None:
            observed = git(project, key, scratch, "ls-remote", "--exit-code", repository, "HEAD").split()
            if len(observed) == 2 and observed[1] == "HEAD" and SHA.fullmatch(observed[0]):
                return observed[0]
        elif SHA.fullmatch(requested):
            git(project, key, scratch, "init", "--quiet", "--bare")
            git(project, key, scratch, "fetch", "--no-tags", "--depth=1", repository, requested)
            if git(project, key, scratch, "rev-parse", "FETCH_HEAD") == requested:
                return requested
    raise ValueError("GitHub did not verify the requested commit.")


def build_bundle(project, key, directory):
    """Export only the approved repository reference; keep the key at the broker."""
    revision = project["revision"]
    if not SHA.fullmatch(revision):
        raise ValueError("Invalid pinned Git revision.")
    directory = Path(directory)
    repository = "git@github.com:" + project["repository"] + ".git"
    scratch = directory / "repository"
    scratch.mkdir(mode=0o700)
    git(project, key, scratch, "init", "--quiet", "--bare")
    git(project, key, scratch, "fetch", "--no-tags", repository, revision, timeout=240)
    if git(project, key, scratch, "rev-parse", "FETCH_HEAD") != revision:
        raise ValueError("Git bundle source differs from the pinned revision.")
    ref = "refs/heads/smartdev-approved"
    git(project, key, scratch, "update-ref", ref, revision)
    bundle = directory / "project.bundle"
    git(project, key, scratch, "bundle", "create", str(bundle), ref, timeout=240)
    git(project, key, scratch, "bundle", "verify", str(bundle))
    return bundle
