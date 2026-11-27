"""Publish the visible messages of explicitly assigned host Codex turns to their task."""
import argparse
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from work_messages import visible_text

PRIVATE = Path("/var/lib/dev-command-center/task-turn-bindings")
PUBLIC = Path("/var/lib/dev-command-center/task-work-history")


def save(path, data, public=False):
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        json.dump(data, f, ensure_ascii=False)
        if public:
            os.fchown(f.fileno(), 0, 65532)
            os.fchmod(f.fileno(), 0o440)
    Path(f.name).replace(path)


def project_turn(source, turn):
    messages = []
    active = ended = False
    for line in source:
        if '"event_msg"' not in line[:160] and '"response_item"' not in line[:160]:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        payload = event.get("payload") or {}
        if event["type"] == "event_msg":
            kind = payload.get("type")
            if kind == "task_started":
                if active and payload.get("turn_id") != turn:
                    ended = True
                    break
                active = payload.get("turn_id") == turn
            elif active and kind in ("task_complete", "turn_aborted") and payload.get("turn_id", turn) == turn:
                ended = True
                break
        elif (active and payload.get("type") == "message" and payload.get("role") == "assistant"
              and payload.get("phase") in ("commentary", "final_answer")):
            body = visible_text("\n".join(part["text"] for part in payload.get("content", [])
                               if part.get("type") == "output_text" and isinstance(part.get("text"), str)))
            if body:
                messages.append({"id": f"{turn}:{len(messages)}", "kind": "message", "label": body,
                                 "status": "completed", "at": event.get("timestamp")})
    return messages, active and not ended, ended


def refresh(binding):
    entry = json.loads(binding.read_text())
    dest = PUBLIC / entry["project"] / entry["task"]
    for directory in (PUBLIC, dest.parent, dest):
        directory.mkdir(exist_ok=True)
        os.chown(directory, 0, 65532)
        directory.chmod(0o750)
    source = Path(entry["file"])
    if entry.get("finished"):
        return
    revision = source.stat().st_mtime_ns
    if entry.get("revision") == revision:
        return
    with source.open() as events:
        messages, active, ended = project_turn(events, entry["turn"])
    save(dest / (entry["turn"] + ".json"), {"running": active, "updated": revision / 1e9, "actions": messages}, True)
    save(binding, {**entry, "revision": revision, "finished": ended})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assign", nargs=3, metavar=("PROJECT", "TASK", "TURN"))
    args = parser.parse_args()
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    if args.assign:
        project, task, turn = map(lambda value: str(UUID(value)), args.assign)
        thread = str(UUID(os.environ["CODEX_THREAD_ID"]))
        sessions = Path("/root/.codex/sessions")
        matches = list(sessions.glob(f"*/*/*/rollout-*-{thread}.jsonl"))
        if len(matches) != 1:
            raise ValueError("No unique local transcript for the current thread.")
        binding = PRIVATE / f"{turn}.json"
        if binding.exists() and json.loads(binding.read_text())["task"] != task:
            raise ValueError("Turn already assigned to another task.")
        save(binding, {"project": project, "task": task, "turn": turn, "file": str(matches[0])})
    for binding in PRIVATE.glob("*.json"):
        refresh(binding)


if __name__ == "__main__":
    main()
