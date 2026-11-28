#!/usr/bin/python3
"""Fast-forward the authenticated workspace checkout using its enrolled Git transport."""

import fcntl
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from uuid import UUID

from command_api import exchange

NAME = re.compile(r"(?:p|project)-[A-Za-z0-9-]+")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")
TOOL = {
    "name": "sync",
    "description": "Fetch or fast-forward the current assigned checkout from its registered origin. "
    "Preserves uncommitted edits and reports conflicting files. Supply a branch only to select another source.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["fetch", "pull"]},
            "branch": {"type": "string", "maxLength": 200},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
}


class UpdateError(Exception):
    pass


def git(cwd, *args, check=True, index=None, data=None):
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/home/node",
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ALLOW_PROTOCOL": "smartdev:https",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    result = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            "-c",
            "submodule.recurse=false",
            "-c",
            "merge.autostash=false",
            "-c",
            "merge.verifySignatures=false",
            "-c",
            "http.followRedirects=false",
            *args,
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        input=data,
        timeout=90,
    )
    if check and result.returncode:
        # Show native conflicting file names, never remote errors containing credential material.
        lines = result.stderr.decode(errors="replace").splitlines()
        conflicts = [line.strip() for line in lines if line.startswith("\t")]
        for line in lines:
            match = re.match(r"error: (?:Entry|Untracked working tree file) '([^']+)'", line)
            if match:
                conflicts.append(match[1])
        reason = " Local paths: " + ", ".join(conflicts[:18]) if conflicts else ""
        raise UpdateError(f"Git {args[0]} failed in {cwd.name} (exit {result.returncode})." + reason)
    return result


def value(cwd, *args):
    return git(cwd, *args).stdout.decode().strip()


def identity(url):
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/", "smartdev::"):
        if url.startswith(prefix):
            name = url[len(prefix) :].removesuffix(".git")
            if REPOSITORY.fullmatch(name):
                return name.casefold()
    return None


def project_root():
    name = os.environ.get("PROJECT_NAME", "")
    if not NAME.fullmatch(name):
        raise UpdateError("Missing registered workspace.")
    return Path("/projects") / name


def enrolled_origin(cwd, root, expected, parent=None):
    if cwd != cwd.resolve() or not cwd.is_relative_to(root):
        raise UpdateError("The checkout path does not belong to this workspace.")
    # The workspace supplies its transport and credentials. Repository-local programs and
    # includes do not belong in the managed update process.
    records = (
        r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks|worktree|autocrlf|eol|safecrlf|sparsecheckout|sparsecheckoutcone|trustctime|checkstat|untrackedcache)",
        r"extensions\.(objectformat|refstorage|worktreeconfig|partialclone)",
        r"(?:remote\..+\.(?:url|pushurl|fetch|tagopt|mirror|promisor|partialclonefilter|prune)|submodule\..+\.(?:active|url|branch|ignore|fetchrecursesubmodules))",
        r"branch\..+\.(remote|merge|description|vscode-merge-base|rebase|pushremote)",
        r"(user\.(name|email|signingkey)|lfs\.(repositoryformatversion|https://github\.com/[^ ]+/info/lfs\.access))",
    )
    per_worktree = git(
        cwd, "config", "--local", "--no-includes", "--bool", "--get", "extensions.worktreeConfig", check=False
    )
    scopes = ("--local", "--worktree") if per_worktree.stdout.strip() == b"true" else ("--local",)
    for scope in scopes:
        keys = value(cwd, "config", scope, "--no-includes", "--name-only", "--list").lower().splitlines()
        if any(not any(re.fullmatch(pattern, key) for pattern in records) for key in keys):
            raise UpdateError(
                "The checkout has custom Git configuration outside the managed update settings."
            )
    marker = cwd / ".git"
    metadata = Path(value(cwd, "rev-parse", "--absolute-git-dir"))
    common = Path(value(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if marker.is_symlink() or metadata != metadata.resolve() or common != common.resolve():
        raise UpdateError("Unexpected Git metadata path.")
    if not metadata.is_relative_to(root) or not common.is_relative_to(root):
        raise UpdateError("Git metadata belongs to another workspace.")
    if marker.is_file():
        if parent is not None:
            parent_meta = Path(value(parent, "rev-parse", "--absolute-git-dir"))
            if not metadata.is_relative_to(parent_meta / "modules") or common != metadata:
                raise UpdateError("The submodule metadata belongs to another checkout.")
        else:
            canonical = root / "repository" / ".git"
            if (
                common != canonical
                or metadata.parent != canonical / "worktrees"
                or Path((metadata / "gitdir").read_text().strip()) != marker
            ):
                raise UpdateError("The assigned worktree is not registered with the project repository.")
    elif metadata != marker or common != marker:
        raise UpdateError("The checkout does not own its Git metadata.")
    if value(cwd, "rev-parse", "--show-toplevel") != str(cwd):
        raise UpdateError("The registered checkout is unavailable.")
    origin = value(cwd, "remote", "get-url", "origin")
    if identity(origin) != expected.casefold() or not origin.startswith(
        ("smartdev::", "https://github.com/")
    ):
        raise UpdateError("The origin does not use this repository's enrolled transport.")
    return origin


def lease():
    from urllib.error import URLError

    try:
        context = json.loads(Path(os.environ["COMMAND_OBJECTIVE_CONTEXT"]).read_text())
        auth = context["authorization"]
        root = project_root()
        cwd = Path(context["directory"])
        if context["mode"] != "workspace-write" or context["branch"] == "detached":
            raise ValueError()
        if context.get("conversation"):
            if cwd != root / "repository" or auth["action"] != "authorize_run":
                raise ValueError()
            route = "/conversation"
        else:
            relative = cwd.relative_to(root).as_posix()
            if relative != "repository" and relative != "worktrees/task-" + str(UUID(auth["objective_id"])):
                raise ValueError()
            if (auth["action"], auth["worktree"], auth["branch"]) != (
                "authorize",
                relative,
                context["branch"],
            ):
                raise ValueError()
            route = "/governance"
        answer = exchange(auth, route, timeout=5)
        if not answer.get("ok") or answer.get("mode") != "workspace-write":
            raise ValueError()
        if value(cwd, "branch", "--show-current") != context["branch"]:
            raise ValueError()
        return root, cwd
    except (ValueError, KeyError, OSError, URLError) as exc:
        raise UpdateError("An active write assignment for this branch and checkout is required.") from exc


def gitlinks(cwd, commit):
    entries = git(cwd, "ls-tree", "-rz", commit).stdout.split(b"\0")
    links = {}
    for entry in entries:
        if entry.startswith(b"160000 "):
            header, name = entry.split(b"\t", 1)
            links[os.fsdecode(name)] = header.split()[2].decode()
    return links


def declared_module(cwd, commit, path, parent):
    config = git(
        cwd,
        "config",
        "--no-includes",
        "--null",
        "--blob",
        commit + ":.gitmodules",
        "--get-regexp",
        r"^submodule\..*\.(path|url)$",
    ).stdout
    fields = dict(item.split("\n", 1) for item in config.decode().split("\0") if item)
    urls = [
        fields.get(key[:-4] + "url", "")
        for key, location in fields.items()
        if key.endswith(".path") and location == path
    ]
    if len(urls) == 1 and urls[0].startswith(("./", "../")):
        urls[0] = "https://github.com/" + posixpath.normpath(parent + ".git/" + urls[0])
    if len(urls) != 1 or not identity(urls[0]):
        raise UpdateError(f"Submodule {path} has no registered GitHub origin.")
    return identity(urls[0])


def local_patch(cwd):
    return git(
        cwd,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        "--full-index",
        "--ignore-submodules=all",
        "HEAD",
        "--",
    ).stdout


def replayable_edits(cwd, before, target):
    try:
        git(cwd, "read-tree", "--dry-run", "-u", "-m", before, target)
        return None
    except UpdateError as native_error:
        if git(cwd, "diff", "--cached", "--name-only", "-z").stdout:
            raise
        added = git(
            cwd, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=A", before, target
        ).stdout
        for name in filter(None, added.split(b"\0")):
            if os.path.lexists(cwd / os.fsdecode(name)):
                raise native_error
        patch = local_patch(cwd)
        if not patch:
            raise
        # Prove the dirty patch merges against the new tree before touching any checkout.
        with tempfile.TemporaryDirectory(prefix="smartdev-git-preflight-") as folder:
            index = Path(folder) / "index"
            git(cwd, "read-tree", target, index=index)
            result = git(
                cwd,
                "apply",
                "--cached",
                "--3way",
                "--whitespace=nowarn",
                "-",
                index=index,
                data=patch,
                check=False,
            )
            if result.returncode:
                raise native_error
        return patch


def keep_patch(root, path, before, target, patch):
    folder = root / "config" / "git-recovery"
    folder.mkdir(mode=0o700, exist_ok=True)
    journal = Path(tempfile.mkdtemp(prefix="fast-forward-", dir=folder))
    (journal / "local.patch").write_bytes(patch)
    (journal / "update.json").write_text(
        json.dumps({"checkout": str(path.relative_to(root)), "before": before, "target": target})
    )
    return str(journal.relative_to(root))


def prepare(cwd, root, before, target, steps, pending, repository, depth=0):
    if depth > 12 or len(steps) > 128:
        raise UpdateError("Submodule nesting exceeds the supported limit.")
    if git(cwd, "ls-files", "-u").stdout:
        raise UpdateError(f"Finish the existing merge in {cwd.name}.")
    old, new = gitlinks(cwd, before), gitlinks(cwd, target)
    for path in sorted(old.keys() | new.keys()):
        child = cwd / path
        if path not in new:
            if (child / ".git").exists():
                raise UpdateError(
                    f"The update removes the initialized submodule {path}. Its files were preserved."
                )
            continue
        if not (child / ".git").exists():
            if old.get(path) != new[path]:
                pending.append(str(child.relative_to(root)))
            continue
        repository_name = declared_module(cwd, target, path, repository)
        origin = enrolled_origin(child, root, repository_name, parent=cwd)
        pin = new[path]
        head = value(child, "rev-parse", "HEAD")
        if head == pin:
            continue
        if head != old.get(path):
            raise UpdateError(f"Submodule {path} has a local commit outside the previous pin.")
        if git(child, "cat-file", "-e", pin + "^{commit}", check=False).returncode:
            shallow = ["--depth=1"] if value(child, "rev-parse", "--is-shallow-repository") == "true" else []
            fetch = ["fetch", "--no-recurse-submodules", "--no-tags", *shallow, origin]
            if git(child, *fetch, pin, check=False).returncode:
                git(child, *fetch, "refs/heads/pin-" + pin)
            value(child, "cat-file", "-e", pin + "^{commit}")
        prepare(child, root, head, pin, steps, pending, repository_name, depth + 1)
    if before != target:
        patch = replayable_edits(cwd, before, target)
        steps.append((cwd, before, target, value(cwd, "branch", "--show-current"), patch))


def update(root, cwd, arguments, authorize):
    if not isinstance(arguments, dict) or not {"operation"} <= arguments.keys() <= {"operation", "branch"}:
        raise UpdateError("Choose fetch or pull and an optional source branch.")
    op = arguments["operation"]
    if op not in ("fetch", "pull"):
        raise UpdateError("Choose fetch or pull.")
    origin = enrolled_origin(cwd, root, os.environ.get("PROJECT_REPOSITORY", ""))
    branch = value(cwd, "branch", "--show-current")
    if not branch:
        raise UpdateError("The assigned checkout has no current branch.")
    source = arguments.get("branch")
    if source is None:
        configured = (
            git(cwd, "config", "--get", "branch." + branch + ".merge", check=False).stdout.decode().strip()
        )
        remote = (
            git(cwd, "config", "--get", "branch." + branch + ".remote", check=False).stdout.decode().strip()
        )
        if remote and remote != "origin":
            raise UpdateError("Specify a branch on the registered origin.")
        source = configured.removeprefix("refs/heads/") if configured else branch
    if (
        not isinstance(source, str)
        or not 0 < len(source) <= 200
        or source.startswith(("-", "@"))
        or git(cwd, "check-ref-format", "refs/heads/" + source, check=False).returncode
    ):
        raise UpdateError("Invalid source branch.")
    with (root / "config" / "git-sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateError("Another Git update is running. Retry when it finishes.") from None
        before = value(cwd, "rev-parse", "HEAD")
        tracking = "refs/remotes/origin/" + source
        git(
            cwd,
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            origin,
            "+refs/heads/" + source + ":" + tracking,
        )
        target = value(cwd, "rev-parse", tracking)
        result = {
            "branch": branch,
            "source": "origin/" + source,
            "before": before,
            "upstream": target,
            "head": before,
        }
        if op == "fetch":
            return {**result, "status": "fetched"}
        if (
            before != target
            and git(cwd, "merge-base", "--is-ancestor", before, target, check=False).returncode
        ):
            if not git(cwd, "merge-base", "--is-ancestor", target, before, check=False).returncode:
                return {**result, "status": "local_branch_ahead"}
            raise UpdateError(
                "The histories have diverged or ancestry is unavailable. No checkout was moved."
            )
        steps, pending = [], []
        prepare(cwd, root, before, target, steps, pending, os.environ["PROJECT_REPOSITORY"])
        authorize()
        moved, backups = [], []
        try:
            for path, old, new, attached, patch in steps:
                if (
                    value(path, "rev-parse", "HEAD") != old
                    or value(path, "branch", "--show-current") != attached
                ):
                    raise UpdateError("The checkout changed during preparation.")
                if patch is not None:
                    if (
                        local_patch(path) != patch
                        or git(path, "diff", "--cached", "--name-only", "-z").stdout
                    ):
                        raise UpdateError("Local edits changed during preparation.")
                    backups.append(keep_patch(root, path, old, new, patch))
                if path == cwd:
                    flags = ["--autostash"] if patch else []
                    git(
                        path,
                        "-c",
                        "user.name=SmartDev workspace",
                        "-c",
                        "user.email=workspace@smartdev.invalid",
                        "merge",
                        "--ff-only",
                        "--no-edit",
                        "--no-overwrite-ignore",
                        *flags,
                        new,
                    )
                else:
                    flags = ["--merge"] if patch else []
                    git(
                        path,
                        "switch",
                        "--detach",
                        "--no-recurse-submodules",
                        "--no-overwrite-ignore",
                        *flags,
                        new,
                    )
                    moved.append((path, old, attached))
                if git(path, "ls-files", "-u").stdout:
                    raise UpdateError(
                        f"Git retained a merge conflict in {path.name}. Local backup: {backups}."
                    )
        except (UpdateError, OSError, subprocess.SubprocessError) as error:
            # If the parent moved, its submodule pins already refer to the new commits.
            # Otherwise undo only our own completed switches using normal Git safeguards.
            if value(cwd, "rev-parse", "HEAD") == before:
                for path, old, attached in reversed(moved):
                    if git(path, "ls-files", "-u").stdout:
                        raise UpdateError(
                            f"Git needs a merge review in {path.name}. Backups: {backups}"
                        ) from error
                    flags = ["--merge", "--no-overwrite-ignore", "--no-recurse-submodules"]
                    args = ["switch", *flags, attached] if attached else ["switch", *flags, "--detach", old]
                    if git(path, *args, check=False).returncode:
                        raise UpdateError(
                            f"Git preserved the changed checkout {path.name} for review. Backups: {backups}"
                        ) from error
            raise
        return {
            **result,
            "head": value(cwd, "rev-parse", "HEAD"),
            "status": "updated" if steps else "current",
            "submodules_updated": len(moved),
            "uninitialized_submodules": pending,
            "recovery_paths": backups,
        }


def registered_config(before, config):
    desired = tomllib.loads(config)["mcp_servers"]["workspace_git"]
    existing = tomllib.loads(before).get("mcp_servers", {}).get("workspace_git")
    if existing == desired:
        return before
    if existing is not None:
        core = ("command", "env_vars", "tool_timeout_sec")
        if ({key: existing.get(key) for key in core} != {key: desired.get(key) for key in core}
                or set(existing) - {*core, "tools"}):
            raise UpdateError("An existing system registration uses a different Git tool.")
        current_tools = existing.get("tools") or {}
        desired_tools = desired.get("tools") or {}
        if current_tools:
            raise UpdateError("An existing system registration uses different Git tool approvals.")
        sync = desired_tools.get("sync")
        if set(desired_tools) != {"sync"} or sync != {"approval_mode": "approve"}:
            raise UpdateError("The managed Git approval policy is invalid.")
        combined = before.rstrip() + "\n\n[mcp_servers.workspace_git.tools.sync]\napproval_mode = \"approve\"\n"
    else:
        combined = before.rstrip() + "\n\n" + config
    tomllib.loads(combined)
    return combined


def register():
    path = Path("/etc/codex/config.toml")
    before = path.read_text() if path.exists() else ""
    combined = registered_config(before, sys.stdin.read())
    if combined == before:
        return
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        output.write(combined)
    os.chmod(output.name, path.stat().st_mode & 0o777 if path.exists() else 0o644)
    os.replace(output.name, path)


def health():
    root = project_root()
    if not os.environ.get("PROJECT_REPOSITORY"):
        return
    cwd = root / "repository"
    enrolled_origin(cwd, root, os.environ["PROJECT_REPOSITORY"])
    metadata = Path(value(cwd, "rev-parse", "--absolute-git-dir"))
    with tempfile.NamedTemporaryFile(prefix="smartdev-health-", dir=metadata):
        pass


def serve():
    for line in sys.stdin:
        msg = {}
        try:
            msg = json.loads(line)
            if "id" not in msg:
                continue
            method = msg.get("method")
            if method == "initialize":
                version = msg.get("params", {}).get("protocolVersion")
                result = {
                    "protocolVersion": (
                        version
                        if version in ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
                        else "2025-06-18"
                    ),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "workspace-git", "version": "1"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [TOOL]}
            elif method == "tools/call" and msg.get("params", {}).get("name") == "sync":
                try:
                    root, cwd = lease()
                    data = update(root, cwd, msg["params"].get("arguments", {}), lease)
                    result = {"content": [{"type": "text", "text": json.dumps(data)}]}
                except (UpdateError, OSError, subprocess.SubprocessError) as exc:
                    note = (
                        str(exc)
                        if isinstance(exc, UpdateError)
                        else "The workspace Git operation could not finish."
                    )
                    result = {"isError": True, "content": [{"type": "text", "text": note}]}
            else:
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": msg["id"],
                            "error": {"code": -32601, "message": "Unknown method"},
                        }
                    ),
                    flush=True,
                )
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
        except (KeyError, TypeError, ValueError):
            print(
                json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
                ),
                flush=True,
            )


if __name__ == "__main__":
    if sys.argv[1:] == ["--register"]:
        register()
    elif sys.argv[1:] == ["--health"]:
        health()
    else:
        serve()
