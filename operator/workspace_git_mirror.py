"""Refresh a QA host's read-only copy of its registered source through Git."""
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys

def run(args, env=None):
    subprocess.run(args, env=env, check=True, timeout=150)

def environment(key, known_hosts):
    options = ["ssh", "-F", "/dev/null", "-i", key, "-o", "BatchMode=yes",
               "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "UserKnownHostsFile=" + known_hosts, "-o", "ConnectTimeout=8"]
    return {**os.environ, "GIT_SSH_COMMAND": shlex.join(options), "GIT_TERMINAL_PROMPT": "0"}

def refresh_submodule(parent_git, config, spec, deployment):
    """Mirror only a commit explicitly linked by the enrolled parent's main tree."""
    name = "submodule." + spec["name"]
    def field(key):
        return subprocess.check_output([*parent_git, "config", "--blob", "refs/heads/main:.gitmodules",
                                        "--get", name+"."+key], text=True, timeout=10).strip()
    if field("path") != spec["path"] or field("url").removesuffix(".git").casefold() != (
            "https://github.com/" + spec["repository"]).casefold():
        raise ValueError("The selected submodule no longer matches its registered source.")
    tree = subprocess.check_output([*parent_git,"ls-tree","refs/heads/main","--",spec["path"]],text=True,timeout=10)
    match = re.fullmatch(r"160000 commit ([0-9a-f]{40})\t"+re.escape(spec["path"])+r"\n", tree)
    if not match:
        raise ValueError("The selected parent tree has no matching pinned submodule.")
    pin = match.group(1)
    ref = "refs/heads/pin-" + pin
    child = ["git", "-C", spec["cache"]]
    if subprocess.run([*child,"show-ref","--verify","--quiet",ref]).returncode:
        run([*child, "fetch", "--no-recurse-submodules", "--depth=1", "--no-tags",
             "git@github.com:"+spec["repository"]+".git", pin+":"+ref],
            environment(spec["upstream_key"], config["upstream_hosts"]))
    run([*child, "push", spec["target"], ref+":"+ref], deployment)

def refresh(config):
    cache = Path(config["cache"])
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "init", "--bare", "--initial-branch=main", str(cache)])
    git = ["git", "-C", str(cache)]
    source = "git@github.com:" + config["repository"] + ".git"
    refs = ["+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"]
    run([*git, "fetch", "--atomic", "--prune", source, *refs],
        environment(config["upstream_key"], config["upstream_hosts"]))
    deployment = environment(config["deployment_key"], config["deployment_hosts"])
    run([*git, "push", "--atomic", "--prune", config["target"], *refs], deployment)
    for spec in config.get("submodules", []):
        refresh_submodule(git, config, spec, deployment)

if __name__ == "__main__":
    refresh(json.loads(Path(sys.argv[1]).read_text()))
