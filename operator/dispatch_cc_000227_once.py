"""Release the approved CC-000227 new-task dispatch exactly once."""

import json
from uuid import UUID

from codex_access import check_credentials
from external_mcp_tasks import record_delivery, workspace_chat_request
from mcp_intake import unpack
from store import database


PROJECT = UUID("bca501cf-7f58-4cd6-b254-33bf4fe48c7c")
REQUEST = UUID("a614b876-e305-562f-8d5f-51e701891fa8")
TASK = UUID("24b728cf-2739-57fc-b9be-9bef84668aa6")
TITLE = "P0 SmartDev Integrations menu in every Coder environment"
HOLD = "Awaiting approved Vault cutover and successful DEV OpenAI credential check."


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def claim():
    # Repeat the live provider gate immediately before the durable outbox claim.
    check_credentials(PROJECT)
    with database() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(PROJECT),))
        row = db.execute(
            """SELECT r.task_id,r.kind,r.admission,r.delivery,r.message_id,r.execution_ref,r.dispatch_hold,
                      r.board_item,o.attempts,o.envelope,p.workspace_url,g.revoked_at,g.expires_at>now() AS current,
                      'write'=ANY(g.scopes) AS writable,o.project_id=ANY(g.project_ids) AS scoped
               FROM mcp_task_request r JOIN mcp_task_outbox o USING(project_id,request_id)
               JOIN projects p ON p.id=r.project_id AND NOT p.archived
               JOIN external_mcp_grant g ON g.id=o.grant_id
               WHERE r.project_id=%s AND r.request_id=%s FOR UPDATE OF r,o""",
            (PROJECT, REQUEST),
        ).fetchone()
        require(row is not None, "CC-000227 outbox row is unavailable")
        require(row["task_id"] == TASK, "CC-000227 task mapping changed")
        require(row["kind"] == "create" and row["admission"] == "created", "CC-000227 is not a new-task dispatch")
        require(row["delivery"] == "received", "CC-000227 was already delivered or changed")
        require(row["message_id"] is None and row["execution_ref"] is None, "CC-000227 already has a receipt")
        require(row["dispatch_hold"] == HOLD, "CC-000227 dispatch gate changed")
        require(row["attempts"] == 0, "CC-000227 already has a dispatch attempt")
        require(row["revoked_at"] is None and row["current"] and row["writable"] and row["scoped"],
                "CC-000227 MCP authorization is unavailable")
        count = db.execute(
            "SELECT count(*) AS n FROM taskboard_item WHERE project_id=%s AND item_id=%s",
            (PROJECT, row["board_item"]),
        ).fetchone()["n"]
        require(count == 1, "CC-000227 board identity is not unique")
        payload = unpack(row["envelope"], REQUEST)
        require(payload.get("requestId") == str(REQUEST), "CC-000227 envelope request changed")
        require(payload.get("newTask") is True and payload.get("title") == TITLE, "CC-000227 new-task routing changed")
        require(not any(key in payload for key in ("objective", "conversationId", "waitingReply")),
                "CC-000227 still targets an existing conversation")
        changed = db.execute(
            """UPDATE mcp_task_outbox SET attempts=1,next_attempt=now()+interval '3 minutes'
               WHERE project_id=%s AND request_id=%s AND attempts=0 RETURNING request_id""",
            (PROJECT, REQUEST),
        ).fetchone()
        require(changed is not None, "CC-000227 dispatch claim was lost")
        db.execute(
            """UPDATE mcp_task_request SET dispatch_hold=NULL,failure_reason=NULL
               WHERE project_id=%s AND request_id=%s""",
            (PROJECT, REQUEST),
        )
        return row["workspace_url"], payload


def main():
    workspace, payload = claim()
    result = workspace_chat_request(PROJECT, workspace, "POST", "run", payload=payload, timeout=30)
    receipt = record_delivery(PROJECT, REQUEST, result)
    require(UUID(str(receipt["task_id"])) == TASK, "Workspace returned an unexpected task identity")
    print(json.dumps({
        "request_id": str(REQUEST),
        "task_id": receipt["task_id"],
        "message_id": receipt["message_id"],
        "execution_receipt": receipt["execution_id"],
        "status": receipt["status"],
        "attempts": 1,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
