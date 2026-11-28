"""List, claim and update owner-approved SMD deployment tasks."""

import argparse
import getpass
import json
import os
from ipaddress import IPv4Address
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://smartdev.youkandoit.com")
    parser.add_argument("--password-file", type=Path)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    claim = actions.add_parser("claim")
    claim.add_argument("task", type=UUID)
    progress = actions.add_parser("progress")
    progress.add_argument("task", type=UUID)
    progress.add_argument("status", choices=("processing", "deployed", "failed", "needs_attention"))
    progress.add_argument("note")
    progress.add_argument("--instance-id", type=UUID)
    progress.add_argument("--revision")
    enrollment = actions.add_parser("receipt-enrollment")
    enrollment.add_argument("task", type=UUID)
    enrollment.add_argument("instance_id", type=UUID)
    enrollment.add_argument("public_key")
    enrollment.add_argument("--ipv4", type=IPv4Address)
    receipt = actions.add_parser("receipt")
    receipt.add_argument("task", type=UUID)
    reconcile = actions.add_parser("reconcile-legacy")
    reconcile.add_argument("task", type=UUID)
    reconcile.add_argument("--confirm-prior-runner-stopped", action="store_true", required=True)
    reconcile.add_argument("--confirm-untagged-inventory-reviewed", action="store_true", required=True)
    reconcile.add_argument("--evidence-reference", required=True)
    actions.add_parser("fix-claim")
    fix_result = actions.add_parser("fix-result")
    fix_result.add_argument("request_id", type=UUID)
    fix_result.add_argument("claim_id", type=UUID)
    fix_result.add_argument("status", choices=("delivered", "denied", "unconfirmed"))
    fix_result.add_argument("--receipt-file", type=Path)
    fix_result.add_argument("--failure-reason")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error("Use an HTTPS portal origin.")
    password = (args.password_file.read_text().strip() if args.password_file
                else getpass.getpass("SmartDev owner password: "))

    def request(path, payload=None, cookie=None, timeout=15):
        headers = {"Origin": origin, "Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        data = None if payload is None else json.dumps(payload).encode()
        return urlopen(Request(origin + "/api" + path, data=data, headers=headers), timeout=timeout)

    try:
        with request("/login", {"password": password}) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        path = "/admin/smd-deploy-tasks"
        payload = None
        if args.action == "claim":
            path += f"/{args.task}/claim"
            requested_token = os.getenv("SMD_TASK_CLAIM_TOKEN")
            payload = {"claim_token": requested_token} if requested_token else {}
        elif args.action == "progress":
            token = os.getenv("SMD_TASK_CLAIM_TOKEN") or getpass.getpass("Task claim token: ")
            path += f"/{args.task}/progress"
            payload = {"claim_token": token, "status": args.status, "note": args.note,
                       "instance_id": str(args.instance_id) if args.instance_id else None,
                       "observed_revision": args.revision}
        elif args.action == "receipt-enrollment":
            path += f"/{args.task}/receipt-enrollment"
            payload = {"claim_token": os.environ["SMD_TASK_CLAIM_TOKEN"],
                       "instance_id": str(args.instance_id), "public_key": args.public_key}
            if args.ipv4:
                payload["ipv4"] = str(args.ipv4)
        elif args.action == "receipt":
            path += f"/{args.task}/receipt"
        elif args.action == "reconcile-legacy":
            path += f"/{args.task}/reconcile-legacy"
            payload = {"prior_runner_stopped": args.confirm_prior_runner_stopped,
                       "untagged_inventory_reviewed": args.confirm_untagged_inventory_reviewed,
                       "evidence_reference": args.evidence_reference}
        elif args.action == "fix-claim":
            path = "/admin/smd-environment-fixes/claim"
            payload = {}
        elif args.action == "fix-result":
            path = "/admin/smd-environment-fixes/result"
            receipt = json.loads(args.receipt_file.read_text()) if args.receipt_file else None
            payload = {"request_id": str(args.request_id), "claim_id": str(args.claim_id),
                       "status": args.status, "receipt": receipt,
                       "failure_reason": args.failure_reason}
        with request(path, payload, cookie, timeout=60 if args.action == "reconcile-legacy" else 15) as response:
            print(json.dumps(json.load(response), indent=2))
    except HTTPError as error:
        parser.exit(1, f"SmartDev returned HTTP {error.code}: {error.read().decode()}\n")


if __name__ == "__main__":
    main()
