"""Guarded in-place correction for CC-000227's pending MCP routing."""

import json
import re
import sys
from uuid import UUID, uuid5

from mcp_intake import digest, pack, unpack
from store import database


PROJECT = UUID("bca501cf-7f58-4cd6-b254-33bf4fe48c7c")
REQUEST = UUID("a614b876-e305-562f-8d5f-51e701891fa8")
OLD_TASK = UUID("10b332ba-ec84-43b8-8323-02f61b0602e1")
NEW_TASK = uuid5(REQUEST, "codex-task")
TITLE = "P0 SmartDev Integrations menu in every Coder environment"
HOLD = "Awaiting approved Vault cutover and successful DEV OpenAI credential check."


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main(revision):
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "Repair revision must be a full Git SHA")
    with database() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(PROJECT),))
        row = db.execute(
            "SELECT * FROM mcp_task_request WHERE project_id=%s AND request_id=%s FOR UPDATE",
            (PROJECT, REQUEST),
        ).fetchone()
        require(row, "CC-000227 request is missing")
        outbox = db.execute(
            "SELECT * FROM mcp_task_outbox WHERE project_id=%s AND request_id=%s FOR UPDATE",
            (PROJECT, REQUEST),
        ).fetchone()
        require(outbox, "CC-000227 outbox entry is missing")

        require(row["task_id"] == OLD_TASK, "CC-000227 old task mapping changed")
        require(row["board_item"] == REQUEST, "CC-000227 board item changed")
        require(row["kind"] == "message" and row["admission"] == "message", "CC-000227 is no longer a follow-up")
        require(row["delivery"] == "received", "CC-000227 delivery state changed")
        require(row["message_id"] is None, "CC-000227 already has a message ID")
        require(row["execution_ref"] is None, "CC-000227 already has an execution receipt")
        require(outbox["attempts"] == 0, "CC-000227 already has an outbox attempt")

        board = db.execute(
            """SELECT count(*) AS count,min(taskboard_number(number)) AS number
               FROM taskboard_item WHERE project_id=%s AND item_id=%s""",
            (PROJECT, REQUEST),
        ).fetchone()
        require(board["count"] == 1 and board["number"] == "CC-000227", "CC-000227 board identity is not unique")

        objective = db.execute(
            "SELECT xmin::text AS row_version FROM objective_contract WHERE project_id=%s AND id=%s FOR SHARE",
            (PROJECT, OLD_TASK),
        ).fetchone()
        conversation = db.execute(
            """SELECT xmin::text AS row_version FROM project_conversation
               WHERE project_id=%s AND id=%s AND active_task_id=%s FOR SHARE""",
            (PROJECT, OLD_TASK, OLD_TASK),
        ).fetchone()
        require(objective and conversation, "Provision SMD Host task or conversation is missing")

        previous_payload = unpack(outbox["envelope"], REQUEST)
        require(previous_payload.get("requestId") == str(REQUEST), "Outbox request identity changed")
        require(previous_payload.get("objective") == str(OLD_TASK), "Outbox objective is not Provision SMD Host")
        require(previous_payload.get("conversationId") == str(OLD_TASK), "Outbox conversation is not Provision SMD Host")
        require(not previous_payload.get("newTask"), "Outbox is already a new-task dispatch")

        instruction = row["instruction"]
        new_payload = {
            "tree": "repository",
            "files": [],
            "requestId": str(REQUEST),
            "prompt": instruction,
            "newTask": True,
            "title": TITLE,
        }
        payload_digest = digest(json.dumps([instruction, TITLE, []], ensure_ascii=False, separators=(",", ":")))
        db.execute(
            """UPDATE mcp_task_request SET task_id=%s,kind='create',admission='created',matched_request=NULL,
                 title=%s,payload_digest=%s,delivery='received',dispatch_hold=%s,failure_reason=NULL
               WHERE project_id=%s AND request_id=%s""",
            (NEW_TASK, TITLE, payload_digest, HOLD, PROJECT, REQUEST),
        )
        db.execute(
            """UPDATE mcp_task_outbox SET envelope=%s,next_attempt='infinity'::timestamptz
               WHERE project_id=%s AND request_id=%s AND attempts=0""",
            (pack(new_payload, REQUEST), PROJECT, REQUEST),
        )
        db.execute(
            """INSERT INTO taskboard_repair(project_id,request_id,revision,summary,evidence)
               VALUES (%s,%s,%s,%s,%s)""",
            (
                PROJECT,
                REQUEST,
                revision,
                "Corrected CC-000227 from a Provision SMD Host follow-up to a paused Integrations Menu new-task dispatch",
                [
                    f"old task {OLD_TASK}; kind message; admission message",
                    f"new task {NEW_TASK}; kind create; admission created",
                    "precondition attempts 0; message ID absent; execution receipt absent",
                    "board item and CC-000227 number preserved",
                    "dispatch paused until approved Vault cutover and successful DEV credential check",
                ],
            ),
        )

        changed = db.execute(
            """SELECT r.task_id,r.kind,r.admission,r.board_item,r.message_id,r.execution_ref,r.dispatch_hold,
                      o.attempts,o.next_attempt='infinity'::timestamptz AS dispatch_paused,
                      taskboard_number(t.number) AS task_number
               FROM mcp_task_request r JOIN mcp_task_outbox o USING(project_id,request_id)
               JOIN taskboard_item t ON (t.project_id,t.item_id)=(r.project_id,r.board_item)
               WHERE r.project_id=%s AND r.request_id=%s""",
            (PROJECT, REQUEST),
        ).fetchone()
        require(changed["task_id"] == NEW_TASK and changed["kind"] == "create" and changed["admission"] == "created", "New mapping failed")
        require(changed["board_item"] == REQUEST and changed["task_number"] == "CC-000227", "Board identity changed")
        require(changed["attempts"] == 0 and changed["dispatch_paused"], "Dispatch is not paused")
        require(changed["message_id"] is None and changed["execution_ref"] is None, "Execution identity appeared during repair")
        require(
            db.execute("SELECT xmin::text AS row_version FROM objective_contract WHERE project_id=%s AND id=%s", (PROJECT, OLD_TASK)).fetchone()["row_version"]
            == objective["row_version"],
            "Provision SMD Host task changed",
        )
        require(
            db.execute("SELECT xmin::text AS row_version FROM project_conversation WHERE project_id=%s AND id=%s", (PROJECT, OLD_TASK)).fetchone()["row_version"]
            == conversation["row_version"],
            "Provision SMD Host conversation changed",
        )

    print(json.dumps({
        "request_id": str(REQUEST),
        "task_number": "CC-000227",
        "old_task_id": str(OLD_TASK),
        "new_task_id": str(NEW_TASK),
        "delivery": "received",
        "attempts": 0,
        "dispatch_paused": True,
        "provision_smd_untouched": True,
    }))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: repair_cc_000227.py <full-git-revision>")
    main(sys.argv[1])
