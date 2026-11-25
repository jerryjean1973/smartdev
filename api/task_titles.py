"""Bounded, deterministic navigation labels drawn from a safe vocabulary."""
import json
import re

from inference import SECRET

TOPICS = (
    (r"\bleads?\b", r"\b(forecast|project(?:ion|ions)?|native br(?:ipe|ip))\b", "CRM lead forecast"),
    (r"\b(task|sidebar|conversation)\b", r"\b(navigation|layout|redesign)\b", "Task navigation redesign"),
    (r"\b(architecture|layers?|design)\b", r"\b(review|inspect|show|assess|layers?)\b", "Project architecture review"),
    (r"\b(submodules?|github|gitlab|git)\b", r"\b(access|auth|keys?|deploy|credentials?)\b", "Repository access verification"),
    (r"\b(preview|browser|layout)\b", r"\b(fix|repair|verify|validate|test)\b", "Application preview validation"),
    (r"\b(dns|nameservers?|domains?)\b", r"\b(verify|inspect|record|configure)\b", "Domain configuration review"),
    (r"\b(tests?|coverage)\b", r"\b(run|verify|validate|check)\b", "Project test validation"),
)
UNSAFE = re.compile(r"[\x00-\x1f\x7f{}\[\]`]|https?://|\b[A-Za-z0-9_-]{32,}\b|\b[^\s@]+@[^\s@]+\.[^\s@]+\b")


def valid_title(value):
    return bool(value and len(value) <= 120 and len(value.split()) <= 12 and
                not UNSAFE.search(value) and not SECRET.search(value))


def task_title(objective):
    first_line = objective.lstrip().split("\n", 1)[0].strip()
    heading = re.match(r"^(?:#\s+(?:(?:CODEX|CB)\s+BUILD\s*[—–:-]\s*)?|TASK:\s*)(.+)$",
                       first_line, re.IGNORECASE)
    if heading:
        proposed = heading.group(1).strip()
        if valid_title(proposed):
            return proposed
        words = proposed.split()
        for count in range(min(len(words), 12), 0, -1):
            shortened = " ".join(words[:count])
            if valid_title(shortened):
                return shortened
    text = objective[:30000].casefold()
    if text.lstrip().startswith(("{", "[")):
        try:
            entry = json.loads(text)
            if isinstance(entry, dict) and isinstance(entry.get("leads"), list):
                return "CRM lead processing"
        except ValueError:
            pass
    for first, second, title in TOPICS:
        if re.search(first, text) and re.search(second, text):
            return title
    return "Project development task"


def backfill_titles(db):
    rows = db.execute("SELECT id,original_request,display_name FROM objective_contract WHERE sidebar_task").fetchall()
    for row in rows:
        if not valid_title(row["display_name"]):
            db.execute("UPDATE objective_contract SET display_name=%s WHERE id=%s",
                       (task_title(row["original_request"]), row["id"]))
