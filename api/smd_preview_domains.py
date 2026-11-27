"""Installation-local preview policy and the public origins reported in Deployments."""
import json
import os
import socket
import ssl
import http.client
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import HTTPException

ENVIRONMENTS = ("DEV","QA","PREPROD","PROD")


def managed_hosts():
    return json.loads(os.getenv("SMD_MANAGED_APP_HOSTS","{}"))


def assert_public_https(hostname,path,marker):
    # Connect to the exact validated address. Preserve hostname verification and SNI.
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(hostname,443,type=socket.SOCK_STREAM)})
        if not addresses or any(not ip_address(ip).is_global for ip in addresses):
            raise ValueError
        with socket.create_connection((addresses[0],443),timeout=5) as raw:
            with ssl.create_default_context().wrap_socket(raw,server_hostname=hostname) as tls:
                tls.sendall(f"GET {path} HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n".encode("ascii"))
                reply=http.client.HTTPResponse(tls)
                reply.begin()
                if reply.status!=200 or marker and reply.read(len(marker)+1)!=marker.encode("ascii"):
                    raise ValueError
    except (OSError, ValueError, http.client.HTTPException):
        raise HTTPException(422,"The custom hostname must serve this application over verified HTTPS.") from None


def read_report(db,task,project,version):
    rows=db.execute("""SELECT environment,hostname FROM smd_custom_preview WHERE task_id=%s
                       AND project_id=%s AND assignment_version=%s""",(task,project,version)).fetchall()
    return {"version":version,"domains":{r["environment"]:r["hostname"] for r in rows}}


def reserve_challenge(db,task,project,version,body):
    from dns_inspection import verification_hostname
    from secrets import token_urlsafe
    try:
        host=verification_hostname(json.loads(body)["hostname"])
    except (ValueError,KeyError,TypeError,AttributeError):
        raise HTTPException(422,"Invalid custom hostname.") from None
    row=db.execute("""INSERT INTO smd_preview_challenge(task_id,assignment_version,project_id,hostname,challenge)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(task_id,assignment_version,project_id,hostname)
        DO UPDATE SET hostname=excluded.hostname RETURNING challenge""",(task,version,project,host,token_urlsafe(24))).fetchone()
    return row


def write_report(db,task,project,version,body):
    from dns_inspection import verification_hostname
    try:
        value=json.loads(body)
        env=value["environment"]
        host=value["hostname"]
        if value["version"]!=version or env not in ENVIRONMENTS:
            raise ValueError
        if host is not None and verification_hostname(host)!=host:
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError):
        raise HTTPException(409,"Domain or tenant assignment changed.") from None
    if host:
        from dns_inspection import PREFIX,txt_matches,verification_txt_answer
        row=db.execute("""SELECT challenge FROM smd_preview_challenge WHERE task_id=%s AND project_id=%s
                  AND assignment_version=%s AND hostname=%s""",(task,project,version,host)).fetchone()
        try:
            owned=row and txt_matches(verification_txt_answer(PREFIX+host),PREFIX+host,row["challenge"])
        except RuntimeError:
            owned=False
        if not owned:
            raise HTTPException(422,"Verify DNS ownership for the current tenant assignment.")
        project_row=db.execute("""SELECT m FROM smd_deploy_task t CROSS JOIN LATERAL
            jsonb_array_elements(t.project_manifest) m WHERE t.id=%s AND m->>'id'=%s""",(task,str(project))).fetchone()["m"]
        marker=project_row.get("build_context_sha256") if project_row.get("tenant_setup") else (project_row["revision"] if project_row.get("verify_installation") else None)
        assert_public_https(host,"/.well-known/smartdev-installation" if marker else "/",marker)
        db.execute("""INSERT INTO smd_custom_preview(task_id,assignment_version,project_id,environment,hostname)
          VALUES (%s,%s,%s,%s,%s) ON CONFLICT(task_id,assignment_version,project_id,environment)
          DO UPDATE SET hostname=excluded.hostname,verified_at=now()""",(task,version,project,env,host))
    else:
        db.execute("""DELETE FROM smd_custom_preview WHERE task_id=%s AND project_id=%s
                   AND assignment_version=%s AND environment=%s""",(task,project,version,env))
    db.execute("""INSERT INTO smd_deployment_event(task_id,kind,label) VALUES (%s,'dns',%s)""",
               (task,env+" preview "+("uses "+host if host else "restored to managed DNS")))
    return {"ok":True}


def managed_previews(project):
    from smd_owner_client import owner_json
    default=managed_hosts().get(str(project))
    if not default:
        return None
    try:
        record=owner_json(project,"domains")
        domains=record["domains"]
    except HTTPException:
        domains={}
    return {env:{"mode":"url","url":"https://"+domains.get(env,default.get("preview_hostname",default["hostname"]) if env=="DEV" else default["hostname"])+"/"} for env in ENVIRONMENTS}


def change_managed_preview(db,project,env,url):
    from smd_owner_client import owner_json
    default=managed_hosts()[str(project)]
    record=owner_json(project,"domains")
    fallback=default.get("preview_hostname",default["hostname"]) if env=="DEV" else default["hostname"]
    host=None
    if url:
        parsed=urlsplit(url)
        if parsed.port not in (None,443) or parsed.query or parsed.fragment or parsed.path not in ("","/"):
            raise HTTPException(422,"Use the HTTPS origin of the application.")
        if parsed.hostname!=fallback:
            host=parsed.hostname
            if not db.execute("""SELECT 1 FROM project_domain WHERE project_id=%s AND hostname=%s
                AND verified_at IS NOT NULL""",(project,host)).fetchone():
                raise HTTPException(422,"Verify the custom hostname in Project domains first.")
            marker=default.get("marker")
            assert_public_https(host,"/.well-known/smartdev-installation" if marker else "/",marker)
    owner_json(project,"domain-report",{"environment":env,"hostname":host,"version":record["version"]})
    return {"mode":"url","url":"https://"+(host or fallback)+"/"}


CANDIDATE_TABLE="""CREATE TABLE IF NOT EXISTS smd_domain_candidate (
    project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,hostname text PRIMARY KEY REFERENCES project_domain(hostname)
    ON DELETE CASCADE,assignment_version integer NOT NULL,challenge text NOT NULL)"""


def domain_candidate(db,project,host,challenge,confirmed):
    from smd_owner_client import owner_json
    if str(project) not in managed_hosts():
        return
    db.execute(CANDIDATE_TABLE)
    if confirmed:
        version=owner_json(project,"domains")["version"]
        db.execute("""INSERT INTO smd_domain_candidate(project_id,hostname,assignment_version,challenge) VALUES (%s,%s,%s,%s)
            ON CONFLICT(hostname) DO UPDATE SET assignment_version=excluded.assignment_version,challenge=excluded.challenge""",
            (project,host,version,challenge))
    else:
        db.execute("DELETE FROM smd_domain_candidate WHERE hostname=%s AND project_id=%s",(host,project))


def eligible_aliases():
    from smd_owner_client import owner_json
    from store import database
    aliases={}
    with database() as db:
        db.execute(CANDIDATE_TABLE)
        for project,managed in managed_hosts().items():
            rows=db.execute("""SELECT c.hostname,c.assignment_version FROM smd_domain_candidate c JOIN project_domain d
                ON c.hostname=d.hostname AND c.project_id=d.project_id AND d.challenge=c.challenge
                WHERE c.project_id=%s AND d.verified_at IS NOT NULL""",(project,)).fetchall()
            if rows:
                version=owner_json(project,"domains")["version"]
                aliases.update({r["hostname"]:managed["hostname"] for r in rows if r["assignment_version"]==version})
    return aliases
