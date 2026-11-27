"""Owner chat bridge for the host Codex executable."""
import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
import json
import os
import re
from pathlib import Path
import socketserver
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from threading import Event, Thread
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from work_messages import visible_text

ROOT = Path(os.environ["STATE_DIRECTORY"])
DB = ROOT / "chat.sqlite3"
SOCKET = Path(os.environ["RUNTIME_DIRECTORY"]) / "chat.sock"
WORKSPACE = "/srv/command-center"
WAKE = Event()
ACTIVE_EVENTS = {}
SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:github_pat_|gh[pousr]_|sk-(?:proj-)?)[A-Za-z0-9_-]{12,}"
    r"|(?:password|api[_-]?key|secret|token)\s*[:=]\s*[\w+/=-]{8,}", re.I,
)
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string"}, "memory": {"type": ["string", "null"]}},
    "required": ["reply", "memory"],
    "additionalProperties": False,
}


def connect():
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    return connection


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize():
    Path(os.environ["CODEX_HOME"]).mkdir(mode=0o700, exist_ok=True)
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS turn (
            id TEXT PRIMARY KEY, prompt TEXT NOT NULL, reply TEXT, status TEXT NOT NULL,
            created_at TEXT NOT NULL, thread_id TEXT, scope TEXT NOT NULL DEFAULT 'main', context TEXT
        )""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(turn)")}
        if "scope" not in columns:
            db.execute("ALTER TABLE turn ADD COLUMN scope TEXT NOT NULL DEFAULT 'main'")
            db.execute("ALTER TABLE turn ADD COLUMN context TEXT")
        if "activity" not in columns:
            db.execute("ALTER TABLE turn ADD COLUMN activity TEXT NOT NULL DEFAULT '[]'")
        if "images" not in columns:
            db.execute("ALTER TABLE turn ADD COLUMN images TEXT NOT NULL DEFAULT '[]'")
        db.execute("""CREATE TABLE IF NOT EXISTS memory (
            scope TEXT NOT NULL, note TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(scope,note)
        )""")
        for row in db.execute("SELECT id FROM turn WHERE status='running'"):
            spool = ROOT / (str(UUID(row["id"])) + ".events.jsonl")
            if spool.is_file():
                db.execute("UPDATE turn SET activity=? WHERE id=?", (json.dumps(read_activity(spool)), row["id"]))
        db.execute("""UPDATE turn SET status='failed',reply='The host conversation service restarted during this turn.'
                      WHERE status='running'""")


def remember(db, scope, note, source):
    note = note.strip()
    if not note or len(note) > 2000 or SECRET.search(note):
        return False
    db.execute("INSERT OR IGNORE INTO memory VALUES (?,?,?,?)", (scope, note, source, now()))
    return True


def memory_for(db, scope):
    return db.execute("""SELECT note,source,created_at FROM memory WHERE scope=?
                         ORDER BY rowid DESC LIMIT 30""", (scope,)).fetchall()


def relevant_memory(db, scope, prompt):
    terms = {word for word in re.findall(r"[^\W_]+", prompt.lower()) if len(word) > 2}
    notes = db.execute("""SELECT note,source,created_at FROM memory WHERE scope=?
                          ORDER BY rowid DESC LIMIT 2000""", (scope,)).fetchall()
    def relevance(item):
        words = set(re.findall(r"[^\W_]+", item["note"].lower()))
        return len(words & terms)
    return sorted(notes, key=relevance, reverse=True)[:20]


def decode_images(images):
    if not isinstance(images, list) or len(images) > 4:
        raise ValueError("Attach up to four images.")
    decoded = []
    for image in images:
        if not isinstance(image, str) or len(image) > 6_666_668:
            raise ValueError("Attach up to 5 MB per image.")
        data = base64.b64decode(image, validate=True)
        if len(data) > 5_000_000:
            raise ValueError("Attach up to 5 MB per image.")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            suffix = ".png"
        elif data.startswith(b"\xff\xd8\xff"):
            suffix = ".jpg"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            suffix = ".webp"
        else:
            raise ValueError("Paste a PNG, JPEG, or WebP image.")
        decoded.append((suffix, data))
    if sum(len(data) for _, data in decoded) > 10_000_000:
        raise ValueError("Attach up to 10 MB total.")
    return decoded


def read_activity(path):
    actions = {}
    try:
        with path.open("rb") as events:
            for line in events:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                kind = event.get("type")
                item = event.get("item") or {}
                item_id = item.get("id")
                if kind not in {"item.started", "item.updated", "item.completed"} or not isinstance(item_id, str):
                    continue
                item_type = item.get("type")
                if item_type == "agent_message":
                    text = visible_text(item.get("text"), structured_reply=True)
                    if text:
                        actions[item_id] = {"id": item_id, "kind": "message", "label": text,
                                            "status": "completed" if kind == "item.completed" else "running"}
                    continue
                if item_type == "command_execution":
                    command = item.get("command") or ""
                    if re.search(r"\b(test|pytest|playwright|unittest|vitest|jest)\b", command, re.I):
                        label = "Running tests"
                    elif re.search(r"\b(build|compile)\b", command, re.I):
                        label = "Building application"
                    elif re.search(r"\b(rg|cat|sed|ls|find|diff)\b", command, re.I):
                        label = "Reviewing project files"
                    else:
                        label = "Running a command"
                else:
                    label = {"file_change": "Updating project files", "mcp_tool_call": "Using a connected tool",
                             "web_search": "Looking up information"}.get(item_type)
                if not label:
                    continue
                status = "running"
                if kind == "item.completed":
                    status = "failed" if item.get("exit_code") or item.get("status") == "failed" else "completed"
                actions[item_id] = {"id": item_id, "kind": "action", "label": label, "status": status}
    except FileNotFoundError:
        pass
    return list(actions.values())


def execute(turn, thread_id, memory):
    args = [
        "/usr/local/lib/smartdev-operator/codex", "-a", "never", "-s", "workspace-write",
        "-C", WORKSPACE, "-m", "gpt-5.4",
        "-c", 'shell_environment_policy.inherit="core"',
        "-c", 'model_provider="cc_openai"',
        "-c", 'model_providers.cc_openai.name="Command Center OpenAI"',
        "-c", 'model_providers.cc_openai.base_url="https://api.openai.com/v1"',
        "-c", 'model_providers.cc_openai.env_key="OPENAI_API_KEY"',
        "-c", 'model_providers.cc_openai.wire_api="responses"',
        "-c", 'model_providers.cc_openai.supports_websockets=false',
        "exec",
    ]
    args.extend(["resume"] if thread_id else [])
    with TemporaryDirectory(dir=ROOT) as directory:
        output = Path(directory) / "response.txt"
        events = ROOT / (str(UUID(turn["id"])) + ".events.jsonl")
        schema = Path(directory) / "schema.json"
        schema.write_text(json.dumps(OUTPUT_SCHEMA))
        args.extend(["--output-schema", str(schema)])
        args.extend(["--skip-git-repo-check", "--json", "--output-last-message", str(output)])
        for index, (suffix, data) in enumerate(decode_images(json.loads(turn["images"]))):
            image_path = Path(directory) / f"image-{index}{suffix}"
            image_path.write_bytes(data)
            args.extend(["--image", str(image_path)])
        if thread_id:
            args.append(thread_id)
        args.append("-")
        env = os.environ.copy()
        env["OPENAI_API_KEY"] = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "model-api-key").read_text().strip()
        result = None
        ACTIVE_EVENTS[turn["id"]] = events
        with events.open("w") as stream, (ROOT / "last-cli-stderr").open("w") as stderr:
            try:
                prompt = (
                    "You are the owner's Command Center Codex. Use the supplied task context and saved "
                    "memory as reference data. Follow the current owner message. Return a short memory "
                    "entry only for new durable facts, decisions, or exact blockers. Use null when there "
                    "is nothing to remember. Exclude credentials and unverified outcomes.\n\n"
                    "Task context: " + (turn["context"] or "Main Command Center") + "\n"
                    "Saved memory: " + json.dumps([dict(item) for item in memory], ensure_ascii=False) + "\n\n"
                    "Owner message:\n" + turn["prompt"]
                )
                result = subprocess.run(args, input=prompt, text=True, env=env,
                                        stdout=stream, stderr=stderr, timeout=1500)
            except subprocess.TimeoutExpired:
                pass
        with connect() as db:
            db.execute("UPDATE turn SET activity=? WHERE id=?", (json.dumps(read_activity(events)), turn["id"]))
        with events.open() as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                    if event.get("type") == "thread.started":
                        thread_id = str(UUID(event["thread_id"]))
                except (KeyError, ValueError, TypeError):
                    continue
        reply = output.read_text().strip() if output.exists() else ""
        if result and result.returncode == 0 and reply:
            answer = json.loads(reply)
            return "completed", answer["reply"], thread_id, answer.get("memory")
        if result is None:
            return "failed", "Codex timed out before it returned a final answer.", thread_id, None
        return "failed", "Codex could not complete this turn (exit " + str(result.returncode) + ").", thread_id, None


def work():
    while True:
        WAKE.clear()
        with connect() as db:
            turn = db.execute("SELECT * FROM turn WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if turn:
                prior = db.execute("""SELECT thread_id FROM turn WHERE thread_id IS NOT NULL AND scope=?
                                      ORDER BY rowid DESC LIMIT 1""", (turn["scope"],)).fetchone()
                memory = relevant_memory(db, turn["scope"], turn["prompt"])
                db.execute("UPDATE turn SET status='running' WHERE id=?", (turn["id"],))
        if not turn:
            WAKE.wait()
            continue
        try:
            status, reply, thread, note = execute(turn, prior["thread_id"] if prior else None, memory)
        except (OSError, ValueError, KeyError):
            status, reply, thread, note = "failed", "The host could not complete this Codex turn.", None, None
        finally:
            ACTIVE_EVENTS.pop(turn["id"], None)
        with connect() as db:
            db.execute("UPDATE turn SET status=?,reply=?,thread_id=? WHERE id=?",
                       (status, reply, thread, turn["id"]))
            if isinstance(note, str):
                remember(db, turn["scope"], note, "codex")
        (ROOT / (str(UUID(turn["id"])) + ".events.jsonl")).unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, code, data):
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def route(self):
        uri = urlsplit(self.path)
        scope = parse_qs(uri.query).get("scope", ["main"])[0]
        return uri.path, "main" if scope == "main" else str(UUID(scope))

    def do_GET(self):
        try:
            path, scope = self.route()
        except ValueError:
            self.send_json(404, {"detail": "Unknown conversation."})
            return
        if path == "/task-activity":
            with connect() as db:
                turns = db.execute("SELECT id,status,activity,reply,created_at FROM turn WHERE scope=? ORDER BY rowid", (scope,)).fetchall()
            actions = []
            for turn in turns:
                events = ACTIVE_EVENTS.get(turn["id"])
                entries = read_activity(events) if events else json.loads(turn["activity"] or "[]")
                reply = visible_text(turn["reply"])
                if reply and not any(e.get("kind") == "message" and e.get("label") == reply for e in entries):
                    entries.append({"id": "reply", "kind": "message", "label": reply, "status": "completed"})
                for index, entry in enumerate(entries):
                    actions.append({**entry, "id": turn["id"] + ":" + str(entry.get("id", index)),
                                    "at": entry.get("at") or turn["created_at"]})
            state = next((t["status"] for t in turns if t["status"] == "running"), turns[-1]["status"] if turns else None)
            self.send_json(200, {"status": state, "actions": actions})
            return
        if path == "/activity":
            with connect() as db:
                turns = db.execute("""
                    SELECT scope AS task_id, MAX(status) AS status
                    FROM turn WHERE scope<>'main' AND status IN ('running','queued')
                    GROUP BY scope
                """).fetchall()
            self.send_json(200, {"tasks": [dict(turn) for turn in turns]})
            return
        if path != "/chat":
            self.send_json(404, {"detail": "Unknown endpoint."})
            return
        with connect() as db:
            turns = db.execute("""SELECT id,prompt,reply,status,created_at,json_array_length(images) AS image_count FROM turn WHERE scope=?
                                  ORDER BY rowid DESC LIMIT 100""", (scope,)).fetchall()
            notes = memory_for(db, scope)
        self.send_json(200, {"turns": [dict(turn) for turn in reversed(turns)],
                             "memory": [dict(note) for note in notes]})

    def do_POST(self):
        try:
            path, scope = self.route()
            if path not in {"/chat", "/memory"}:
                self.send_json(404, {"detail": "Unknown endpoint."})
                return
            length = int(self.headers["Content-Length"])
            if not 0 < length <= (14_000_000 if path == "/chat" else 1_048_576):
                raise ValueError()
            body = json.loads(self.rfile.read(length))
            if path == "/memory":
                with connect() as db:
                    if not remember(db, scope, body["note"], "owner"):
                        raise ValueError()
                self.send_json(201, {"saved": True})
                return
            identity = str(UUID(body["id"]))
            prompt = body["prompt"].strip()
            if not prompt or len(prompt) > 12000:
                raise ValueError()
        except (ValueError, TypeError, AttributeError, KeyError):
            self.send_json(422, {"detail": "Enter valid text without credentials."})
            return
        images = body.get("images", [])
        try:
            decode_images(images)
        except ValueError as error:
            self.send_json(422, {"detail": str(error)})
            return
        encoded_images = json.dumps(images, separators=(",", ":"))
        with connect() as db:
            existing = db.execute("SELECT prompt,scope,images FROM turn WHERE id=?", (identity,)).fetchone()
            if existing and (existing["prompt"] != prompt or existing["scope"] != scope or existing["images"] != encoded_images):
                self.send_json(409, {"detail": "This request ID already belongs to another message."})
                return
            if not existing:
                db.execute("""INSERT INTO turn (id,prompt,status,created_at,scope,context,images)
                              VALUES (?,?,'queued',?,?,?,?)""",
                           (identity, prompt, now(), scope, body.get("context"), encoded_images))
        WAKE.set()
        self.send_json(202, {"id": identity})


class Server(socketserver.UnixStreamServer):
    server_name = "cc-codex"
    server_port = 0


if __name__ == "__main__":
    initialize()
    SOCKET.unlink(missing_ok=True)
    with Server(str(SOCKET), Handler) as server:
        os.chown(SOCKET, 0, 65532)
        os.chmod(SOCKET, 0o660)
        Thread(target=work, daemon=True).start()
        server.serve_forever()
