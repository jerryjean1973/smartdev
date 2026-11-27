"""Exercise the real issuer database, HTTPS receiver preflight and one-time grant checks in a fixture."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import os
from secrets import token_urlsafe
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient
from core import issue_session
from main import app
from portal_handoff import challenge
from store import database


class Form(HTMLParser):
    def __init__(self, response):
        super().__init__()
        assert response.status_code == 200, response.status_code
        assert response.headers["cache-control"] == "no-store"
        self.values = {}
        self.feed(response.text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input":
            self.values[attrs["name"]] = attrs["value"]


def run(task, instance, portal):
    origin = os.environ["PORTAL_ORIGIN"]
    route = f"/admin/compute/deployments/{task}/portal-login"
    c = TestClient(app,base_url=origin)
    headers = {"Origin":origin}
    with database() as db:
        assert db.execute("SELECT current_database() AS n").fetchone()["n"] == "issuer"
        record = db.execute("SELECT portal_account,version FROM smd_deployment_assignment WHERE task_id=%s",(task,)).fetchone()
        assert record["portal_account"] == "owner"

    assert c.post(route,headers=headers).status_code == 401
    session = issue_session(bytes.fromhex(os.environ["SESSION_KEY"]))
    c.cookies.set("__Host-command",session)
    assert c.post(route,headers={"Origin":"https://unrelated.example"}).status_code == 403

    def begin():
        flow = Form(c.post(route,headers=headers)).values["flow"]
        verifier = token_urlsafe(32)
        form = {"flow":flow,"challenge":challenge(verifier)}
        assert c.post("/deployment-sign-in/return",data=form,headers=headers).status_code == 403
        assert c.post("/deployment-sign-in/return",data=form,headers={"Origin":portal}).status_code == 200
        other = TestClient(app,base_url=origin)
        other.cookies.set("__Host-command",issue_session(bytes.fromhex(os.environ["SESSION_KEY"])))
        assert other.post("/deployment-sign-in/authorize",data=form,headers=headers).status_code == 403
        grant = Form(c.post("/deployment-sign-in/authorize",data=form,headers=headers)).values
        assert c.post("/deployment-sign-in/authorize",data=form,headers=headers).status_code == 403
        return dict(**grant,verifier=verifier,task_id=str(task),instance_id=str(instance))

    def exchange(grant):
        return c.post("/deployment-sign-in/redeem",json=grant).status_code

    grant = begin()
    for key,value in (("verifier",token_urlsafe(32)),("code",token_urlsafe(32)),("instance_id",str(uuid4())),("task_id",str(uuid4()))):
        assert exchange({**grant,key:value}) == 403
    # A browser without the destination verifier cannot finish even with the issued form.
    with httpx.Client(trust_env=False) as anon:
        form = {k:grant[k] for k in ("flow","code")}
        result = anon.post(portal+"/api/deployment-portal/finish",data=form,headers={"Origin":portal})
        assert result.status_code == 403 and "__Host-command" not in result.cookies
        result = anon.post(portal+"/api/deployment-portal/start",data={"flow":grant["flow"]},headers={"Origin":"https://unrelated.example"})
        assert result.status_code == 403
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(exchange,[grant,grant])) == [200,403]
    assert exchange(grant) == 403
    expired = begin()
    with database() as db:
        db.execute("UPDATE smd_portal_launch SET expires_at=now()-interval '1 second' WHERE id=%s",(expired["flow"],))
    assert exchange(expired) == 403
    reassigned = begin()
    with database() as db:
        db.execute("UPDATE smd_deployment_assignment SET version=version+1 WHERE task_id=%s",(task,))
    assert exchange(reassigned) == 403
    retired = begin()
    try:
        with database() as db:
            db.execute("UPDATE smd_deploy_task SET reconciled_at=now() WHERE id=%s",(task,))
        assert exchange(retired) == 409 and c.post(route,headers=headers).status_code == 409
    finally:
        with database() as db:
            db.execute("UPDATE smd_deploy_task SET reconciled_at=NULL WHERE id=%s",(task,))
    print("Passed default account, authorization, browser binding, concurrent redemption, replay, expiry, reassignment and retirement.")


if __name__ == "__main__":
    import sys
    run(UUID(sys.argv[1]), UUID(sys.argv[2]), sys.argv[3])
