"""Exercise the HTTP governance API against a running, dedicated test project."""
import base64
import concurrent.futures
import hashlib
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID, uuid4

BASE = os.environ["GOVERNANCE_TEST_URL"].rstrip("/")
PROJECT = os.environ["GOVERNANCE_TEST_PROJECT"]
SCOPE = f"/api/projects/{PROJECT}/objectives"
WORKSPACE = f"/api/workspace-memory/{PROJECT}/governance"
TLS = ssl.create_default_context(cafile=os.getenv("GOVERNANCE_TEST_CA"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


HTTP = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=TLS))


def acceptance_headers():
    workspace = "p-" + base64.b32encode(UUID(PROJECT).bytes).decode().rstrip("=").lower()
    return {"X-SmartDev-Acceptance": Path(os.environ["SMARTDEV_TEST_ACCEPTANCE_TOKEN_FILE"]).read_text().strip(),
            "X-SmartDev-Fixture-Conversation": os.environ["SMARTDEV_TEST_CONVERSATION_ID"],
            "X-SmartDev-Fixture-Workspace": workspace}


def request(path, body=None, expected=200, *, workspace=False, method=None):
    headers = {"Origin": BASE, "Content-Type": "application/json", **acceptance_headers()}
    if workspace:
        headers["Authorization"] = "Bearer " + os.environ["GOVERNANCE_TEST_PROJECT_TOKEN"]
    else:
        headers["Cookie"] = "__Host-command=" + os.environ["GOVERNANCE_TEST_SESSION"]
    req = urllib.request.Request(BASE + path, json.dumps(body).encode() if body is not None else None,
                                 headers, method=method)
    try:
        response = HTTP.open(req, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        result = json.load(response)
    assert response.status == expected, (response.status, result)
    return result


def require_acceptance_project():
    headers = acceptance_headers()
    conversation = headers["X-SmartDev-Fixture-Conversation"]
    workspace = headers["X-SmartDev-Fixture-Workspace"]
    req = urllib.request.Request(BASE + f"/api/workspace-memory/{PROJECT}/conversation",
        json.dumps({"action": "authorize_submission", "workspace_name": workspace,
                    "fixture_conversation_id": conversation}).encode(), {"Content-Type":"application/json",
         "Authorization":"Bearer " + os.environ["GOVERNANCE_TEST_PROJECT_TOKEN"],
         **headers})
    with HTTP.open(req, timeout=12) as result:
        identity=json.load(result)
    assert identity["environment"] == "acceptance" and identity["project_id"] == PROJECT
    assert identity["conversation_id"] == conversation and identity["workspace_name"] == workspace
    return workspace


def state(objective):
    return next(row["progress"] for row in request(SCOPE) if row["id"] == objective)


def run(objective, criterion, branch, tree, environment, action="begin", execution=None, **extra):
    body = {"objective_id": objective, "execution_id": execution or str(uuid4()),
            "criterion_id": criterion, "branch": branch, "worktree": tree, "action": action,
            "fingerprint": hashlib.sha256(environment.encode()).hexdigest(), **extra}
    return body["execution_id"], body


def evidence(objective, criterion, version=1):
    attempt = next(item for item in state(objective)["attempts"] if item["criterion_id"] == criterion)
    return request(f"{SCOPE}/{objective}/evidence", {
        "version": version, "criterion_id": criterion, "fingerprint": attempt["fingerprint"],
        "evidence": f"Observed test fixture outcome for {criterion} at {attempt['branch']}.",
    })


def create(request_text, criteria):
    return request(SCOPE, {"original_request": request_text, "expected_outcome": request_text,
                           "criteria": criteria}, 201)["id"]


def main():
    require_acceptance_project()
    if len(sys.argv) > 1:
        result = state(sys.argv[1])
        assert result["status"] == "COMPLETED" and len(result["verifiedEvidence"]) == 3
        print("PASS contract, checkpoints and verified completion survived service restart.")
        return
    objective = create("Complete all three objective fixtures.", [
        {"description": "Verify the prerequisite"},
        {"description": "Verify the primary outcome", "depends_on": [1]},
        {"description": "Verify the independent outcome"},
    ])
    begin = lambda body, expected=200: request(WORKSPACE, body, expected, workspace=True)
    _, prerequisite = run(objective, "criterion-1", "main", "repository", "env-a")
    _, dependent = run(objective, "criterion-2", "main", "repository", "env-a")
    assert begin(dependent)["decision"] == "block"
    assert begin(prerequisite)["decision"] == "execute"
    assert begin(prerequisite)["decision"] == "execute"  # Request replay.
    begin({**prerequisite, "action": "heartbeat"})
    _, duplicate = run(objective, "criterion-3", "main", "worktrees/independent", "env-b")
    begin(duplicate, 409)
    _, independent = run(objective, "criterion-3", "independent", "worktrees/independent", "env-b")
    _, contender = run(objective, "criterion-3", "another", "worktrees/another", "env-c")
    with concurrent.futures.ThreadPoolExecutor() as pool:
        # Each request performs a real independent PostgreSQL transaction.
        def try_claim(body):
            try:
                return begin(body)["decision"]
            except AssertionError as conflict:
                assert conflict.args[0][0] == 409, conflict
                return "conflict"
        claims = list(pool.map(try_claim, [independent, contender]))
    assert sorted(claims) == ["conflict", "execute"], claims
    winner = independent if claims[0] == "execute" else contender
    begin({**prerequisite, "action": "finish", "exit_code": 0})
    assert state(objective)["completedCriteria"] == []
    assert state(objective)["status"] != "COMPLETED"
    assert evidence(objective, "criterion-1")["completedCriteria"] == ["criterion-1"]
    assert begin({**prerequisite, "execution_id": str(uuid4())})["decision"] == "skip"
    resumed = state(objective)
    assert "criterion-2" in resumed["ready"]
    assert begin(dependent)["decision"] == "execute"
    begin({**winner, "action": "finish", "exit_code": 0})
    evidence(objective, "criterion-3")
    begin({**dependent, "action": "finish", "exit_code": 0})
    assert evidence(objective, "criterion-2")["status"] == "COMPLETED"
    unauthorized = urllib.request.Request(BASE + f"{SCOPE}/{objective}", json.dumps({
        "version": 1, "reason": "expand scope", "expected_outcome": "changed",
        "criteria": [{"description": "changed"}],
    }).encode(), {"Content-Type": "application/json", "Origin": BASE,
                 "Authorization": "Bearer " + os.environ["GOVERNANCE_TEST_PROJECT_TOKEN"]}, method="PUT")
    try:
        urllib.request.urlopen(unauthorized, context=TLS, timeout=20)
    except urllib.error.HTTPError as error:
        assert error.code == 401, error.code
    else:
        raise AssertionError("A workspace credential amended owner scope.")

    revision = create("Recover after a dependency change.", [{"description": "Verify dependency output"}])
    _, first = run(revision, "criterion-1", "main", "repository", "dep-v1")
    begin(first)
    begin({**first, "action": "finish", "exit_code": 0})
    evidence(revision, "criterion-1")
    _, second = run(revision, "criterion-1", "main", "repository", "dep-v2")
    assert begin(second)["decision"] == "execute"
    assert state(revision)["completedCriteria"] == []
    begin({**second, "action": "finish", "exit_code": 1})
    for _ in range(2):
        _, failed = run(revision, "criterion-1", "main", "repository", "dep-v2")
        assert begin(failed)["decision"] == "execute"
        begin({**failed, "action": "finish", "exit_code": 1})
    _, blocked = run(revision, "criterion-1", "main", "repository", "dep-v2")
    assert begin(blocked)["decision"] == "block"
    assert state(revision)["status"] == "BLOCKED"
    _, recovered = run(revision, "criterion-1", "main", "repository", "dep-v3")
    assert begin(recovered)["decision"] == "execute"
    begin({**recovered, "action": "finish", "exit_code": 0})
    amended = request(f"{SCOPE}/{revision}", {"version": 1, "reason": "Owner approved an updated outcome.",
        "expected_outcome": "Verify updated dependency output.", "criteria": [{"description":"Verify updated output"}]},
        method="PUT")
    assert amended["version"] == 2 and amended["original_request"] == "Recover after a dependency change."
    print("PASS prerequisites, checkpoint reuse, conflict-free concurrent assignments, owner amendments,")
    print("state recovery, stale evidence, bounded failure paths and evidence-based completion.")
    print(objective)


if __name__ == "__main__":
    main()
