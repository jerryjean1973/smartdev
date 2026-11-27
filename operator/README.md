# Dedicated SmartDev DEV host operator

This socket-activated service is separate from the bCentRisk customer deployer. Its socket clients are authorized Ubuntu root processes connecting to `/run/smartdev-operator.sock` (mode 0600). The service uses the host Docker socket under systemd confinement. That Docker access is privileged. Keep the operator socket out of application and workspace containers.

## Install and verify

From the actual Ubuntu host, with the reviewed files present at the documented checkout:

```bash
cd /root/dev-command-center
./operator/install.sh
```

The installer checks the observed SmartDev portal's Compose identity, enables socket activation and submits a read-only portal inspection. A successful receipt contains the container ID, image ID and observed state. Initial enrollment is portal only. A separately authorized administrator can edit the root-owned `/etc/smartdev-operator/services` and restart the operator service to load the new enrollment. Eligible targets are `api`, `portal`, `worker`, and `dev-gateway` in the observed SmartDev Compose project. The operator never accepts a caller-supplied Docker argument, container ID, shell command, or Compose file.

`journalctl -u smartdev-operator.service` shows structured receipts. A restart receipt reports Docker state. Verify application health separately. If restart is unconfirmed, inspect container state before retrying. The installer does not perform a restart acceptance test.

## Rollback

Disable the socket and stop its service. This leaves application containers running and preserves enrollment for review.

```bash
systemctl disable --now smartdev-operator.socket
systemctl stop smartdev-operator.service
```

## Completion boundary

The local host execution boundary and read-only outbound dispatch are implemented. Remote registration and dispatch require live acceptance. Remote mutations, deployment and rollback operations, and DEV/QA lifecycle workflows remain outside this release. MIG authorization remains independent of this operator.

## Outbound registration for portal inspection

The installed socket stays mode 0600. The outbound connector polls the existing SmartDev HTTPS API. It can only request the enrolled portal inspection locally. A configured SmartDev project UUID limits which existing MCP read grants may submit this operation. PostgreSQL links each request to its grant, a short-lived claim, and the host receipt ID. Match that ID against the host journal. This release has a single configured machine identity and project. Rotate by replacing the token file, deploying the new hash, and restarting the connector.

1. In the authorized Ubuntu root session, generate the credential. The commands print its SHA-256 hash only. Use the hash in deployment configuration and retain the token file on the host.

   ```bash
   umask 077
   python3 -c 'from pathlib import Path; import secrets; p=Path("/etc/smartdev-operator/machine-token"); f=p.open("x"); f.write(secrets.token_urlsafe(48)+"\n"); f.close()'
   python3 -c 'from pathlib import Path; import hashlib; print(hashlib.sha256(Path("/etc/smartdev-operator/machine-token").read_bytes().strip()).hexdigest())'
   ```

2. Deploy the updated SmartDev API using the release override for the running API service only, preserving the existing worker. Set `SMARTDEV_OPERATOR_PROJECT_ID` to the explicitly authorized SmartDev project UUID and `SMARTDEV_OPERATOR_TOKEN_SHA256` to the generated hash on the API service. Apply migration 031 through the API's existing migration mechanism. Keep the existing MCP feature flag and OAuth configuration. The recorded Compose overlays select different API images, so pin the intended updated API image in the final override and preserve the previous image ID for rollback.
3. Put `SMARTDEV_OPERATOR_ORIGIN=https://<actual-SmartDev-portal-host>` in `/etc/smartdev-operator/outbound.conf` owned by root and mode 0600. From the verified checkout install and start the connector:

   ```bash
   install -o root -g root -m 0644 operator/outbound.py /usr/local/lib/smartdev-operator/outbound.py
   install -o root -g root -m 0644 operator/smartdev-operator-outbound.service /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now smartdev-operator-outbound.service
   ```

4. From a SmartDev MCP client authorized for that project, call `smartdev_inspect_dev_portal` with its `project_id`. One read-only portal receipt is the initial remote Docker inventory. Verify the returned receipt ID in `journalctl -u smartdev-operator.service`. Repeat with an unauthorized project and an unsupported operation. Both must be rejected without a new host inspection. Restart `smartdev-operator-outbound.service` and repeat the authorized request, expecting a fresh receipt. Also confirm a request to the machine poll route without the bearer is rejected with HTTP 401.

Stopping and disabling `smartdev-operator-outbound.service` closes outbound dispatch. Clear the API credential hash to revoke the machine identity. The local operator and bCentRisk client deployer are independent of this connector.

## Scoped DEV task probe

The existing outbound connector polls task claims when its host configuration
sets `SMARTDEV_OPERATOR_TASKS=1`. The task allowlist contains `codex.version`.
The root-owned socket invokes a pinned local Codex binary with the fixed
`--version` argument. It records the task and claim IDs with the host receipt.
The version is available in the host journal.

The workspace POSTs to `/api/workspace-memory/{project}/operator-task` using its
existing project bearer and a separate, random ticket delivered to the assigned
execution. The request includes approval ID, task ID, execution ID, environment
and operation. Repeating the same request retrieves dispatch or terminal receipts.

Enrollment requires the MIG authority's explicit approval for the exact binding
and an Ed25519 signature over the canonical bytes in `api/operator_task.py`'s
`mig_payload`. The API deployment must pin that authority's raw 32-byte public key
as hex in `SMARTDEV_OPERATOR_TASK_MIG_PUBLIC_KEY`. A trusted administrator
registers the signed record and the SHA-256 of the ticket in `operator_task`.
The private key belongs to the MIG authority. The API rejects unsigned records
and records signed by an unconfigured authority. The existing BRIPe MIG
credentials and grants do not authorize this operation.

The API checks the project, task version, execution lease, approval and expiry
when queuing and again when claiming. Set `revoked_at` to stop an unclaimed
operation. Claiming is the dispatch commitment. An in-flight probe is bounded
by its deadline and cannot be recalled. The connector does not replay an
ambiguous claim. Correlate dispatch, claim, and completion events in
`operator_task_event` with `operator_task.host_receipt_id` in the operator journal.

For rollback, disable the task poll flag, return the API to its previous image,
and restore the previous operator scripts. Retain the additive tables for audit.
