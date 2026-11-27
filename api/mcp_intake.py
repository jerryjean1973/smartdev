"""Reconcile incoming MCP work with open project tasks before scheduling delivery."""
import hashlib
import hmac
import json
import os
import re
from uuid import uuid5

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from store import database
from work_messages import visible_text


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def envelope_cipher():
    key = hmac.digest(bytes.fromhex(os.environ["SESSION_KEY"]), b"smartdev/mcp-outbox/v1", "sha256")
    return AESGCM(key)


def pack(payload, request):
    nonce = os.urandom(12)
    return nonce + envelope_cipher().encrypt(nonce, json.dumps(payload).encode(), request.bytes)


def unpack(blob, request):
    return json.loads(envelope_cipher().decrypt(blob[:12], blob[12:], request.bytes))


def normalized(text):
    return " ".join((text or "").split())


def words(text):
    stop = {"a","an","the","to","for","and","of","in","please","fix","ensure","add","with","task","should","must"}
    return {w for w in re.findall(r"[\w/-]+", (text or "").casefold()) if w not in stop}


def match(candidate, instruction, title, work_key, fingerprint):
    if work_key and candidate["work_key"] and work_key != candidate["work_key"]:
        return 0
    if candidate["instruction_hash"] == fingerprint or (
        not candidate["instruction_hash"] and candidate["instruction"] == instruction):
        return 3
    if work_key and candidate["work_key"]:
        return 2 if work_key == candidate["work_key"] else 0
    old = normalized(candidate["instruction"])
    new = normalized(instruction)
    if not old or not new or "[credential removed]" in old:
        return 0
    # A copied instruction plus acceptance criteria is a refinement even without a title.
    if len(words(old)) >= 4 and (new.startswith(old + " ") or new.startswith(old + ".")):
        return 2
    tokens, previous = words(new), words(old)
    shared = tokens & previous
    same_title = title and normalized(title).casefold() == normalized(candidate["title"]).casefold()
    if (same_title and len(words(title)) >= 2 and len(shared) >= 2
            and len(shared) >= .45 * min(len(tokens), len(previous))):
        return 1
    if len(shared) >= 5 and len(shared) >= .78 * min(len(tokens), len(previous)) and len(shared) >= .52 * len(tokens | previous):
        return 1
    return 0


CANDIDATES = """
SELECT r.task_id,COALESCE(r.matched_request,r.request_id) AS request_id,r.instruction,r.title,r.work_key,r.instruction_hash,r.received_at AS opened,
 r.execution_phase='Processing' AS running
FROM mcp_task_request r LEFT JOIN objective_contract c ON c.id=r.task_id AND c.project_id=r.project_id
WHERE r.project_id=%s AND r.task_id IS NOT NULL AND r.owner_done_at IS NULL
 AND r.admission IS DISTINCT FROM 'duplicate' AND r.execution_phase IS DISTINCT FROM 'Processed'
 AND (c.id IS NULL OR c.status='active') AND COALESCE(r.request_scope,mcp_inferred_scope(COALESCE(NULLIF(r.title,''),r.instruction)))=%s
 AND NOT EXISTS (SELECT 1 FROM objective_presentation h WHERE h.objective_id=r.task_id AND h.excluded_from_board)
UNION ALL
SELECT c.id,NULL,c.original_request,c.display_name,NULL,NULL,c.created_at,
 EXISTS (SELECT 1 FROM objective_run x WHERE x.project_id=c.project_id AND x.objective_id=c.id AND x.finished_at IS NULL AND x.lease_until>now()) FROM objective_contract c
WHERE c.project_id=%s AND c.status='active' AND
 COALESCE(substring(c.business_objective from '^BO-[0-9]+'),mcp_inferred_scope(c.original_request))=%s
 AND NOT EXISTS (SELECT 1 FROM objective_presentation h WHERE h.objective_id=c.id AND h.excluded_from_board)
 AND NOT EXISTS (SELECT 1 FROM objective_run x WHERE x.objective_id=c.id AND x.project_id=c.project_id
   AND x.version=c.version AND x.finished_at IS NOT NULL AND x.exit_code=0 AND NOT c.criteria @> '[{"mandatory":true}]'::jsonb
   AND NOT EXISTS (SELECT 1 FROM objective_run y WHERE y.objective_id=c.id AND y.started_at>x.started_at))
ORDER BY running DESC NULLS LAST, opened ASC NULLS LAST,task_id,request_id
"""


def receive(project, request, kind, prompt, title, task, scope=None, *, work_key=None, grant_id=None):
    if work_key is not None and (not isinstance(work_key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", work_key)):
        raise ValueError("Invalid work key.")
    binding = [prompt, title, []] if kind == "create" else [str(task), kind == "reply", prompt, []]
    payload_digest = digest(json.dumps(binding, ensure_ascii=False, separators=(",", ":")))
    fingerprint = digest(prompt.strip())
    with database() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(project),))
        if scope is not None and (not isinstance(scope, str) or scope not in ("global", "unassigned")
                                  and not db.execute("SELECT 1 FROM business_objective WHERE id=%s", (scope,)).fetchone()):
            raise ValueError("Unknown request scope.")
        saved = db.execute("SELECT * FROM mcp_task_request WHERE project_id=%s AND request_id=%s FOR UPDATE",
                           (project, request)).fetchone()
        if saved and (saved["payload_digest"] != payload_digest or (scope is not None and saved["request_scope"] != scope)
                      or (work_key is not None and saved["work_key"] != work_key)):
            raise ValueError("Request ID belongs to different instructions.")
        if saved and saved["admission"]:
            return saved
        action = "created" if kind == "create" else kind
        target = task
        linked = None
        if saved:
            claimed = db.execute("SELECT message_id FROM conversation_submission WHERE task_project_id=%s AND request_id=%s",
                                 (project, request)).fetchone()
            target = saved["task_id"] or (claimed["message_id"] if claimed and kind == "create" else task)
        if kind in ("create","message") and not saved:
            assignment = scope or db.execute("SELECT mcp_inferred_scope(%s) AS id", (title or prompt,)).fetchone()["id"]
            candidates = db.execute(CANDIDATES, (project, assignment, project, assignment)).fetchall()
            if kind=="message":
                candidates=[c for c in candidates if str(c["task_id"])==str(task) and c["request_id"]]
            ranked = [(match(c, prompt, title, work_key, fingerprint), c) for c in candidates]
            best = max((score for score,_ in ranked), default=0)
            hits = [c for score,c in ranked if score == best and best]
            if hits and (best==3 or work_key and best==2 or len({c["task_id"] for c in hits}) == 1):
                chosen = next((c for c in hits if c["task_id"]==hits[0]["task_id"] and c["request_id"]), hits[0])
                target, linked = chosen["task_id"], chosen["request_id"]
                action = "duplicate" if best == 3 else "updated"
        target = target or uuid5(request, "codex-task")
        row = db.execute("""INSERT INTO mcp_task_request
            (project_id,request_id,task_id,kind,received_at,instruction,title,payload_digest,delivery,request_scope,
             admission,matched_request,work_key,instruction_hash)
            VALUES (%s,%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(project_id,request_id) DO UPDATE SET task_id=EXCLUDED.task_id,admission=EXCLUDED.admission,
             instruction_hash=EXCLUDED.instruction_hash,kind=EXCLUDED.kind,instruction=COALESCE(mcp_task_request.instruction,EXCLUDED.instruction),
             title=COALESCE(NULLIF(mcp_task_request.title,''),EXCLUDED.title) RETURNING *""",
            (project, request, target, kind, visible_text(prompt), visible_text(title), payload_digest,
             "accepted" if action == "duplicate" else "received",scope,action,linked,work_key,fingerprint)).fetchone()
        if grant_id:
            db.execute("""UPDATE mcp_task_request r SET ingress_actor=g.owner_id,ingress_client=coalesce(nullif(g.refresh_client_id,''),'MCP'),
                ingress_tenant=(SELECT tenant_name FROM command_center_project_tenant WHERE project_id=r.project_id)
                FROM external_mcp_grant g WHERE g.id=%s AND r.project_id=%s AND r.request_id=%s AND r.ingress_actor IS NULL""",
                (grant_id,project,request))
        if action != "duplicate" and row["delivery"] != "accepted":
            routing = {"newTask":True,"title":title} if action == "created" else {
                "objective":str(target),"conversationId":str(target),"waitingReply":kind == "reply"}
            payload = {"tree":"repository","files":[],"requestId":str(request),"prompt":prompt,**routing}
            db.execute("""INSERT INTO mcp_task_outbox(project_id,request_id,grant_id,envelope,next_attempt)
                VALUES (%s,%s,%s,%s,now()+interval '45 seconds') ON CONFLICT DO NOTHING""",
                (project,request,grant_id,pack(payload, request)))
        return row


def retry_due():
    from external_mcp_tasks import record_delivery, workspace_chat_request
    with database() as db:
        db.execute("""DELETE FROM mcp_task_outbox o USING mcp_task_request r
            WHERE (r.project_id,r.request_id)=(o.project_id,o.request_id) AND r.delivery='accepted'""")
        row = db.execute("""WITH due AS (
            SELECT o.project_id,o.request_id,p.workspace_url FROM mcp_task_outbox o
            JOIN projects p ON p.id=o.project_id AND NOT p.archived
            JOIN external_mcp_grant g ON g.id=o.grant_id AND g.revoked_at IS NULL AND g.expires_at>now()
                AND p.id=ANY(g.project_ids) AND 'write'=ANY(g.scopes)
            JOIN mcp_task_request r USING(project_id,request_id)
            WHERE o.next_attempt<=now() AND (r.admission='created' OR EXISTS (
                SELECT 1 FROM objective_contract c WHERE c.id=r.task_id AND c.project_id=r.project_id))
            ORDER BY o.next_attempt,r.received_at LIMIT 1 FOR UPDATE OF o SKIP LOCKED
        ) UPDATE mcp_task_outbox o SET next_attempt=now()+least(300,45*(1+o.attempts))*interval '1 second',
           attempts=o.attempts+1 FROM due WHERE (due.project_id,due.request_id)=(o.project_id,o.request_id)
           RETURNING o.project_id,o.request_id,o.envelope,due.workspace_url""").fetchone()
    if row:
        payload = unpack(row["envelope"], row["request_id"])
        result = workspace_chat_request(row["project_id"], row["workspace_url"], "POST", "run", payload=payload)
        record_delivery(row["project_id"], row["request_id"], result)
    return bool(row)


async def delivery_loop():
    import asyncio
    import logging
    from external_mcp_tasks import WorkspaceUnavailable
    while True:
        try:
            moved = await asyncio.to_thread(retry_due)
        except WorkspaceUnavailable:
            moved = False
        except Exception as error:
            logging.getLogger(__name__).error("MCP redelivery failed (%s)",type(error).__name__)
            moved = False
        await asyncio.sleep(1 if moved else 5)
