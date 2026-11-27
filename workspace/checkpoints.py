"""Capture worktree versions using Git trees and private project metadata."""
import json
import os
import re
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PRIVATE_PATH = re.compile(r"(^|/)(uploads|secrets|\.ssh|\.env(?:\.[^/]*)?)(/|$)|\.(?:pem|key|p12|pfx)$", re.I)
CREDENTIAL = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:gh[pousr]_|github_pat_|sk-(?:proj-|svcacct-)?|hvs\.)[A-Za-z0-9_-]{12,}")
SOURCE = re.compile(r"\.(?:py|js|jsx|ts|tsx|mjs|cjs|html|css|scss|go|rs|java|kt|rb|php|sh|sql|md|txt|json|toml|ya?ml|xml|c|cpp|h|cs|vue|svelte)$", re.I)


def command(directory, *args, env=None, data=None):
    return subprocess.run(["git", *args], cwd=directory, env=env, input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True).stdout


def checkpoint(directory, project, *, objective, execution, branch, head, phase, result=None, compared_to=None):
    checkpoint_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    history = project / "config" / "checkpoints"
    history.mkdir(mode=0o700, exist_ok=True)
    excluded = []
    with tempfile.TemporaryDirectory(dir=history) as temporary:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temporary) / "index")}
        command(directory, "read-tree", head or "--empty", env=env)
        tracked = (command(directory, "diff", "--name-only", "--no-renames", "-z", head, "--") if head
                   else command(directory, "ls-files", "-z"))
        untracked = command(directory, "ls-files", "--others", "--exclude-standard", "-z")
        tracked_paths = set(tracked.split(b"\0")) - {b""}
        paths = tracked_paths | (set(untracked.split(b"\0")) - {b""})
        for raw in sorted(paths):
            name = os.fsdecode(raw)
            file = directory / name
            if PRIVATE_PATH.search(name) or (raw not in tracked_paths and not SOURCE.search(name)):
                excluded.append(name)
                continue
            try:
                info = file.lstat()
            except FileNotFoundError:
                command(directory, "update-index", "--force-remove", "--", name, env=env)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size > 5_000_000 or not file.resolve().is_relative_to(directory):
                excluded.append(name)
                continue
            contents = file.read_bytes()
            if b"\0" in contents or CREDENTIAL.search(contents):
                excluded.append(name)
                continue
            blob = command(directory, "hash-object", "-w", "--stdin", data=contents).strip()
            mode = b"100755" if info.st_mode & stat.S_IXUSR else b"100644"
            command(directory, "update-index", "-z", "--index-info", env=env,
                    data=mode + b" " + blob + b"\t" + raw + b"\0")
        tree = command(directory, "write-tree", env=env).decode().strip()
        parent = ("-p", head) if head else ()
        sha = command(directory, "-c", "user.name=SmartDev", "-c", "user.email=smartdev@localhost",
                      "commit-tree", tree, *parent, "-m", f"SmartDev {phase} checkpoint {checkpoint_id}").decode().strip()
        diff_args = ("diff", "--name-only", "--no-renames", "-z", compared_to, sha, "--") if compared_to else (
            "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", sha)
        changed = command(directory, *diff_args)
        ref = f"refs/smartdev/checkpoints/{checkpoint_id}"
        command(directory, "update-ref", ref, sha)
        record = {"id": checkpoint_id, "projectId": project.name, "objective": objective, "execution": execution,
                  "agent": "codex", "branch": branch, "worktree": str(directory.relative_to(project)),
                  "head": head, "sha": sha, "comparedTo": compared_to or head, "phase": phase, "at": now, "exitCode": result,
                  "verification": "pending", "environment": "DEV", "excluded": excluded,
                  "changedFiles": [os.fsdecode(name) for name in changed.split(b"\0") if name]}
        path = Path(temporary) / "checkpoint.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        path.replace(history / f"{checkpoint_id}.json")
        return record


def restore(directory, project, source, target, filename=None):
    """Apply an explicitly reviewed transition in the original task worktree."""
    import fcntl
    import hashlib

    if source["worktree"] != target["worktree"] or source["branch"] != target["branch"]:
        raise ValueError("Choose two checkpoints from the same worktree and branch.")
    if source["excluded"] or target["excluded"]:
        raise ValueError("This transition contains excluded files. Review them in the IDE.")
    lock_id = hashlib.sha256(str(directory).encode()).hexdigest()[:24]
    with (project / "config" / f"codex-{lock_id}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        branch = command(directory, "branch", "--show-current").decode().strip()
        if branch != source["branch"]:
            raise ValueError("The worktree is on a different branch.")
        head = command(directory, "rev-parse", "HEAD").decode().strip()
        paths = [filename] if filename else []
        options = ["--literal-pathspecs", "diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-renames"]
        patch = command(directory, *options, source["sha"], target["sha"], "--", *paths)
        if not patch:
            raise ValueError("The selected checkpoints contain no changes to restore.")
        changed = command(directory, *options, "--name-only", "-z", source["sha"], target["sha"], "--", *paths)
        names = [os.fsdecode(name) for name in changed.split(b"\0") if name]
        if any(PRIVATE_PATH.search(name) for name in names):
            raise ValueError("The transition includes private paths.")
        recovery = checkpoint(directory, project, objective=source["objective"], execution=str(uuid4()),
                              branch=branch, head=head, phase="before-restore")
        if set(names) & set(recovery["excluded"]):
            raise ValueError("An affected file cannot be captured in the recovery checkpoint.")
        if command(directory, "--literal-pathspecs", "diff", "--name-only", recovery["sha"], source["sha"], "--", *names):
            raise ValueError("Affected files have changed since the selected starting checkpoint.")
        if command(directory, "--literal-pathspecs", "diff", "--cached", "--name-only", "--", *names):
            raise ValueError("Affected files have staged changes.")
        command(directory, "apply", "--check", "--binary", "-", data=patch)
        command(directory, "apply", "--binary", "-", data=patch)
        restored = checkpoint(directory, project, objective=target["objective"], execution=recovery["execution"],
                              branch=branch, head=head, phase="after-restore")
        if command(directory, "--literal-pathspecs", "diff", "--name-only", restored["sha"], target["sha"], "--", *names):
            raise ValueError(f"Restored paths failed verification. Recovery checkpoint {recovery['id']}.")
        return {"recovery": recovery["id"], "restored": restored["id"]}


if __name__ == "__main__":
    import sys

    payload = json.load(sys.stdin)
    try:
        print(json.dumps(restore(Path(payload["directory"]), Path(payload["project"]),
                                 payload["source"], payload["target"], payload.get("filename"))))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error) if isinstance(error, ValueError) else "Restore could not proceed. Check worktree activity and new changes.",
              file=sys.stderr)
        sys.exit(1)
