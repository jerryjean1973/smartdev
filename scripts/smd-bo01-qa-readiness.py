"""Reflect the approved BO-01 QA run and its signed installation evidence in Taskboard."""
from pathlib import Path
import os
import httpx
from psycopg.types.json import Jsonb
from store import database

OBJECTIVE = "10b332ba-ec84-43b8-8323-02f61b0602e1"
PROJECT = "e2a3bfac-25a7-5fca-bc18-f5b2248c024f"
REVISION = "3473a6ee834d51b57a235e0a2e23a37c1922ea1b"
DIGEST = "c286ae1050a1597b062a68dfc20d7c639e654cd9edcfd10b0b2e54419396d02a"


def refresh():
    with database() as db:
        job = db.execute("""SELECT t.id,t.status,t.instance_id,t.note,r.enrolled_at,r.verified_at,r.connections
            FROM smd_deploy_task t LEFT JOIN smd_deploy_receipt r ON r.task_id=t.id
            WHERE t.reconciled_at IS NULL AND t.project_id=%s AND t.project_manifest @> %s
            ORDER BY t.queued_at DESC LIMIT 1""", (PROJECT, Jsonb([{"id": PROJECT, "revision": REVISION,
                "build_context_sha256": DIGEST, "tenant_setup": True}]))).fetchone()
        if not job:
            return
        blocked = job["status"] in ("failed", "needs_attention")
        launched = bool(job["instance_id"])
        verified = bool(job["verified_at"] and job["connections"])
        def update(pos, status, detail, checks):
            done = sum(c["status"] == "done" for c in checks)
            fields = (status, detail, Jsonb(checks), done, len(checks))
            db.execute("""UPDATE objective_milestone SET status=%s,detail=%s,checks=%s,progress_completed=%s,
                progress_total=%s,updated_at=now() WHERE objective_id=%s AND position=%s AND
                (status,detail,checks,progress_completed,progress_total) IS DISTINCT FROM (%s,%s,%s,%s,%s)""",
                (*fields, OBJECTIVE, pos, *fields))
        current = db.execute("SELECT checks FROM objective_milestone WHERE objective_id=%s AND position=6 FOR UPDATE",
                             (OBJECTIVE,)).fetchone()
        if not current:
            return
        checks = current["checks"][:4]
        start = "done" if launched else "blocked" if blocked else "working"
        checks.append({"title": "Start the approved VG QA host", "status": start,
                       "detail": f"Deployment {job['id']}. Tenant credentials are configured after installation."})
        update(6, start, f"Vultr instance {job['instance_id']} created." if launched else job["note"] or "QA test queued.", checks)
        run = "done" if verified else "blocked" if blocked else "working" if launched else "pending"
        suffix = job["id"].hex
        endpoints = [
            ("Verify SmartDev portal API health", f"smd-{suffix}", "/api/health", None),
            ("Verify Coder health", f"coder-{suffix}", "/healthz", None),
            ("Verify the installed VG package", f"app-{PROJECT.replace('-', '')[:12]}-{suffix[:12]}",
             "/.well-known/smartdev-installation", DIGEST),
        ]
        health = []
        with httpx.Client(timeout=3) as client:
            for title, host, route, marker in endpoints:
                passed = verified
                if launched and not verified:
                    try:
                        response = client.get(f"https://{host}.qa.youkandoit.com{route}")
                        passed = response.status_code == 200 and (marker is None or response.content == marker.encode())
                    except httpx.HTTPError:
                        pass
                health.append({"title": title, "status": "done" if passed else run, "detail": host + ".qa.youkandoit.com" + route})
        detail = ("Verified host services, the installed VG image and the VM-signed receipt." if verified else
                  "Portal, Coder and VG installation passed HTTPS checks. Waiting for the workspace and signed receipt."
                  if not blocked and all(item["status"] == "done" for item in health) else job["note"] or "Waiting for host installation.")
        update(7, run, detail, [
            {"title": "Provision the VM", "status": start, "detail": "Verify the Vultr instance for this deployment."},
            {"title": "Enroll the deployment identity", "status": "done" if job["enrolled_at"] else run,
             "detail": "Bind the receipt to the VM and its assigned public address."},
            *health, {"title": "Verify the workspace and signed receipt", "status": run,
             "detail": "Verify the Coder workspace checkout and the VM-signed installation receipt."}])
        if verified:
            published = job["status"] == "deployed"
            root = os.getenv("SMARTDEV_SMD_EVIDENCE_DIRECTORY")
            shots = bool(root and all((Path(root) / str(job["id"]) / (view + ".png")).is_file()
                                     for view in ("portal", "coder", "application", "connections")))
            update(8, "done" if published and shots else "working",
                   "Verified connection details are available in Deployments. VG is installed, awaiting tenant configuration."
                   if published else "Publishing the verified installation receipt.",
                   [{"title":"Publish signed connection details","status":"done" if published else "working","detail":str(job["id"])},
                    {"title":"Record browser evidence","status":"done" if shots else "working","detail":"Portal, Coder, VG setup and deployment connections."}])


if __name__ == "__main__":
    refresh()
